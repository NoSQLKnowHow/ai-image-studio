#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh. See README "Backing up and restoring".
#
#   scripts/restore.sh [options] BACKUP_FOLDER
#
# Nothing is ever deleted: whatever is in the way (the current data folder, .env or model cache)
# is moved aside to a name ending in .before-restore-<date>-<time>, and you remove it when you're sure.
set -euo pipefail
# shellcheck source=scripts/_backup_common.sh
. "$(dirname -- "${BASH_SOURCE[0]}")/_backup_common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/restore.sh [options] BACKUP_FOLDER

BACKUP_FOLDER is one of the ai-image-studio-backup-<date>-<time> folders that scripts/backup.sh made.
It restores what that backup holds: your history and images (./data), the Docker image, your .env
settings and, if it was included, the model cache. The studio is stopped while it works.

Options:
  --no-image     don't load the Docker image, even if the backup has one
  --no-env       don't restore .env
  --no-model     don't restore the model cache, even if the backup has one
  --force        replace what is already there: ./data, .env and the model folder are moved
                 aside (not deleted), where without --force restoring over them is refused
  --start        start the studio afterwards (docker compose up -d --no-build)
  --no-verify    skip the checksum check (not recommended)
  --dry-run      show what would be done and change nothing
  --allow-root   run as root (not recommended)
  -h, --help     this text

Only restore backups you made yourself: an archive is unpacked into your folders.
Exit codes: 0 done; 1 an error; 2 wrong usage; 3 refused because something would be overwritten, or you said no.
EOF
}

WITH_IMAGE=1; WITH_ENV=1; WITH_MODEL=1; FORCE=0; START=0; VERIFY=1
DRY=0; ALLOW_ROOT=0; ASSUME_YES=0; BACKUP=""
while (( $# )); do
  case $1 in
    --no-image)   WITH_IMAGE=0 ;;
    --no-env)     WITH_ENV=0 ;;
    --no-model)   WITH_MODEL=0 ;;
    --force)      FORCE=1 ;;
    --start)      START=1 ;;
    --no-verify)  VERIFY=0 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    --dry-run)    DRY=1 ;;
    --allow-root) ALLOW_ROOT=1 ;;
    -h|--help)    usage; exit 0 ;;
    -*)           die "Unknown option: $1 (see --help)." 2 ;;
    *)            [[ -z $BACKUP ]] || die "Give one backup folder, not two." 2; BACKUP=$1 ;;
  esac
  shift
done
if [[ -z $BACKUP ]]; then usage >&2; exit 2; fi
case $BACKUP in /*) ;; *) BACKUP="$PWD/$BACKUP" ;; esac
BACKUP=${BACKUP%/}

# ---------------------------------------------------------------- is this a backup, and is it intact?
refuse_root
for tool in tar gzip sha256sum docker; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' is needed but isn't installed."
done
[[ -d $BACKUP ]] || die "$BACKUP isn't a folder."
[[ -f $BACKUP/MANIFEST.txt && -f $BACKUP/SHA256SUMS ]] \
  || die "$BACKUP has no MANIFEST.txt and SHA256SUMS, so it doesn't look like a backup made by backup.sh."
[[ -f $BACKUP/data.tar.gz ]] || die "$BACKUP has no data.tar.gz."

manifest() { grep -m1 "^$1=" "$BACKUP/MANIFEST.txt" | cut -d= -f2- || true; }
[[ $(manifest format) == 1 ]] || die "This backup has a format ($(manifest format)) that this version of restore.sh doesn't know."
included=",$(manifest included),"
has() { [[ $included == *",$1,"* ]]; }

if (( VERIFY )); then
  say "Checking the backup's checksums..."
  (cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS) \
    || die "The backup is damaged or incomplete (a checksum doesn't match). Nothing was changed."
fi

cd "$REPO"
[[ -f compose.yaml ]] || die "compose.yaml not found next to the scripts folder ($REPO)."

do_image=0; do_env=0; do_model=0
(( WITH_IMAGE )) && has image && do_image=1
(( WITH_ENV ))   && has env   && do_env=1
(( WITH_MODEL )) && has model && do_model=1

HF_DIR=""; MODEL_DIR=""
if (( do_model )); then
  MODEL_DIR=$(manifest model_dir)
  [[ $MODEL_DIR =~ ^models--[A-Za-z0-9._-]+$ ]] || die "The backup names a model folder I don't trust: '$MODEL_DIR'."
fi

# ---------------------------------------------------------------- tell the user what this backup is
say "Backup:   $BACKUP"
say "  made:   $(manifest created_utc) on $(manifest host), studio $(manifest studio_version)"
say "  holds:  $(manifest included)"
backup_commit=$(manifest git_commit)
now_commit=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo none)
if [[ $backup_commit != none && $now_commit != none && $backup_commit != "$now_commit" ]]; then
  warn "The code here is at commit ${now_commit:0:10} but the backup was made at ${backup_commit:0:10}."
  warn "Newer code can normally open older data, but older code refuses data a newer version has changed."
  warn "To match the backup exactly:  git checkout $backup_commit"
fi
[[ $(manifest git_uncommitted_files) =~ ^[1-9] ]] && warn "The backup was made with uncommitted changes in the code, which are not in git."

# ---------------------------------------------------------------- what is in the way?
stamp=$(date +%Y%m%d-%H%M%S)
data_in_way=0; env_in_way=0; model_in_way=0
if [[ -e data ]] && [[ -n $(ls -A data 2>/dev/null || true) ]]; then data_in_way=1; fi
if (( do_env )) && [[ -f .env ]] && ! cmp -s .env "$BACKUP/env.backup"; then env_in_way=1; fi

if (( ! FORCE )); then
  (( data_in_way )) && die "./data already has files in it. Restoring would replace your current history. Use --force to move it aside first (it is kept, not deleted)." 3
  if (( env_in_way )); then warn "Keeping your current .env (it differs from the backup's). Use --force to replace it."; do_env=0; fi
fi

# Where the model goes depends on the .env that will be in force afterwards.
if (( do_model )); then
  if (( do_env )); then HF_DIR=$(ENV_FILE="$BACKUP/env.backup" hf_cache_dir); else HF_DIR=$(hf_cache_dir); fi
  if [[ -e $HF_DIR/hub/$MODEL_DIR ]]; then
    model_in_way=1
    if (( ! FORCE )); then
      warn "The model is already in the cache, so it is not restored. Use --force to move the cached copy aside and restore it."
      do_model=0
    fi
  fi
fi

# the image: skip the load when this very image is already here
current_image_id=""
if (( do_image )); then
  current_image_id=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || true)
  if [[ -n $current_image_id && $current_image_id == "$(manifest image_id)" ]]; then
    say "The Docker image $IMAGE is already this exact image; not loading it again."
    do_image=0
  fi
fi

# check every archive's contents before unpacking anything
check_archive_paths "$BACKUP/data.tar.gz" "data"
(( do_model )) && check_archive_paths "$BACKUP/model-cache.tar" "hub/$MODEL_DIR"

say ""
say "This will:"
if (( FORCE && data_in_way )); then say "  - move the current ./data aside to data.before-restore-$stamp"; fi
say "  - restore ./data from data.tar.gz"
if (( do_env )); then say "  - restore .env$( ((env_in_way)) && echo " (the current one is moved aside to .env.before-restore-$stamp)")"; fi
if (( do_image )); then say "  - load the Docker image$( [[ -n $current_image_id ]] && echo " (the current $IMAGE is kept as ai-image-studio:before-restore-$stamp)")"; fi
if (( do_model )); then say "  - restore the model into $HF_DIR/hub/$MODEL_DIR$( ((model_in_way)) && echo " (the current copy is moved aside first)")"; fi
say "  - stop the studio while it does this$( ((START)) && echo ", and start it afterwards")"

if (( DRY )); then
  say ""
  say "Dry run: nothing was changed."
  exit 0
fi
if [[ -t 0 ]]; then confirm "Go ahead?"; fi

# ---------------------------------------------------------------- do it
WAS_RUNNING=0
if container_running; then WAS_RUNNING=1; say "Stopping the studio..."; docker compose stop "$SERVICE" >/dev/null; fi

TMP="$REPO/.restore-tmp-$stamp"
# shellcheck disable=SC2317  # runs from the EXIT trap
finish() {
  local rc=$?
  trap - EXIT
  rm -rf -- "$TMP"
  if (( rc != 0 )); then
    warn "The restore did not finish. The studio was left stopped, on purpose, so nothing runs on half-restored data."
    [[ -d data.before-restore-$stamp ]] && warn "Your previous data is safe in data.before-restore-$stamp."
    [[ -f .env.before-restore-$stamp ]] && warn "Your previous .env is safe in .env.before-restore-$stamp."
    warn "Fix the problem above and run the restore again (add --force if the data folder now exists)."
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -- "$TMP"

say "Unpacking your history and images..."
tar -xzf "$BACKUP/data.tar.gz" -C "$TMP"
[[ -d $TMP/data ]] || die "The data archive didn't contain a data folder."
if [[ -e data ]]; then
  if [[ -n $(ls -A data 2>/dev/null || true) ]]; then mv -- data "data.before-restore-$stamp"; else rmdir data; fi
fi
mv -- "$TMP/data" data

if (( do_env )); then
  say "Restoring .env..."
  if [[ -f .env ]] && (( env_in_way )); then mv -- .env ".env.before-restore-$stamp"; fi
  cp -- "$BACKUP/env.backup" .env
  chmod 600 .env
fi

if (( do_image )); then
  if [[ -n $current_image_id ]]; then docker tag "$IMAGE" "ai-image-studio:before-restore-$stamp"; fi
  say "Loading the Docker image (several GB; a few minutes)..."
  gunzip -c "$BACKUP/image.tar.gz" | docker load >/dev/null
  loaded=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || true)
  [[ -n $loaded ]] || die "The image was loaded but $IMAGE doesn't exist afterwards."
  if [[ $loaded != "$(manifest image_id)" ]]; then warn "The loaded image's id differs from the one in the manifest."; fi
fi

if (( do_model )); then
  say "Restoring the model (about 31 GiB; a few minutes)..."
  mkdir -p -- "$HF_DIR/hub"
  mkdir -- "$TMP/model"
  tar -xf "$BACKUP/model-cache.tar" -C "$TMP/model"
  if [[ -e $HF_DIR/hub/$MODEL_DIR ]]; then mv -- "$HF_DIR/hub/$MODEL_DIR" "$HF_DIR/hub/$MODEL_DIR.before-restore-$stamp"; fi
  mv -- "$TMP/model/hub/$MODEL_DIR" "$HF_DIR/hub/$MODEL_DIR"
fi

# ---------------------------------------------------------------- finish
say ""
say "Restored."
if (( START || WAS_RUNNING )); then
  say "Starting the studio..."
  docker compose up -d --no-build
else
  say "The studio is stopped. Start it with:  docker compose up -d --no-build"
fi
(( FORCE && data_in_way )) && say "Your previous data is in data.before-restore-$stamp. Delete it when you're sure you don't need it."
exit 0
