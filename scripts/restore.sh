#!/usr/bin/env bash
# Restore (or just check) a backup made by scripts/backup.sh. See README "Backing up and restoring".
#
#   scripts/restore.sh [options] BACKUP.tar
#   scripts/restore.sh --verify BACKUP.tar      only check that the file is intact; changes nothing
#
# Nothing is ever deleted: whatever is in the way (the current data folder, .env or model cache)
# is moved aside to a name ending in .before-restore-<date>-<time>, and you remove it when you're sure.
set -euo pipefail
# shellcheck source=scripts/_backup_common.sh
. "$(dirname -- "${BASH_SOURCE[0]}")/_backup_common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/restore.sh [options] BACKUP

BACKUP is a .tar file made by scripts/backup.sh (it can sit on a mounted NAS share), or a folder
holding the same files (what you get by unpacking that .tar). It is read in place: nothing needs
unpacking first. Restores your history and images (./data), the Docker image, your .env settings and,
if they were included, the studio's models (each one that isn't already in the Hugging Face cache). The
studio is stopped while it works.

Options:
  --verify       only check the backup is intact (every checksum), show what it holds, and stop.
                 Changes nothing and needs no Docker, so it also works on a NAS or another computer
  --no-image     don't load the Docker image, even if the backup has one
  --no-env       don't restore .env
  --no-model     don't restore the models, even if the backup has them
  --force        replace what is already there: ./data, .env and the models are moved
                 aside (not deleted), where without --force restoring over them is refused
  --start        start the studio afterwards (docker compose up -d --no-build)
  --no-verify    skip the checksum check before restoring (not recommended)
  --dry-run      show what would be done and change nothing
  --allow-root   run as root (not recommended)
  -h, --help     this text

Only restore backups you made yourself: an archive is unpacked into your folders.
Exit codes: 0 done; 1 an error (including a damaged backup); 2 wrong usage;
3 refused because something would be overwritten, or you said no.
EOF
}

ONLY_VERIFY=0; WITH_IMAGE=1; WITH_ENV=1; WITH_MODEL=1; FORCE=0; START=0; VERIFY=1
DRY=0; ALLOW_ROOT=0; ASSUME_YES=0
while (( $# )); do
  case $1 in
    --verify)     ONLY_VERIFY=1 ;;
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
    *)            [[ -z $BACKUP ]] || die "Give one backup, not two." 2; BACKUP=$1 ;;
  esac
  shift
done
if [[ -z $BACKUP ]]; then usage >&2; exit 2; fi
case $BACKUP in /*) ;; *) BACKUP="$PWD/$BACKUP" ;; esac
BACKUP=${BACKUP%/}

# ---------------------------------------------------------------- is this a backup, and is it intact?
(( ONLY_VERIFY )) || refuse_root
tools=(tar gzip sha256sum)
(( ONLY_VERIFY )) || tools+=(docker)
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' is needed but isn't installed."
done
open_backup
has_member data.tar.gz || die "$BACKUP has no data.tar.gz."
# Format 1: no models, or the one image model (model_dir). Format 2: the models are listed in model_paths. Any other number
# was made by a newer script that this one cannot safely read, so stop instead of guessing.
[[ $(manifest format) == 1 || $(manifest format) == 2 ]] || die "This backup has a format ($(manifest format)) that this version of restore.sh doesn't know. Update the scripts (git pull) and try again."
included=",$(manifest included),"
has() { [[ $included == *",$1,"* ]]; }

if (( ONLY_VERIFY || VERIFY )); then
  say "Checking every checksum (reads the whole backup; a few minutes for a big one)..."
  verify_backup || die "The backup is damaged or incomplete (see above). Nothing was changed."
  say "  All checksums match."
fi

# ---------------------------------------------------------------- tell the user what this backup is
say "Backup:   $BACKUP"
say "  made:   $(manifest created_utc) on $(manifest host), studio $(manifest studio_version)"
say "  holds:  $(manifest included)"
# The models the backup holds (format 2): comma-separated paths inside the cache folder. Empty for older backups and for backups without models
backup_models=$(manifest model_paths)
if [[ -n $backup_models ]]; then say "  models: ${backup_models//,/ }"; fi
backup_commit=$(manifest git_commit)
if (( ONLY_VERIFY )); then
  say "  git:    $backup_commit"
  say ""
  say "This backup is intact."
  exit 0
fi

cd "$REPO"
[[ -f compose.yaml ]] || die "compose.yaml not found next to the scripts folder ($REPO)."
now_commit=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo none)
if [[ $backup_commit != none && $now_commit != none && $backup_commit != "$now_commit" ]]; then
  warn "The code here is at commit ${now_commit:0:10} but the backup was made at ${backup_commit:0:10}."
  warn "Newer code can normally open older data, but older code refuses data a newer version has changed."
  warn "To match the backup exactly:  git checkout $backup_commit"
fi
[[ $(manifest git_uncommitted_files) =~ ^[1-9] ]] && warn "The backup was made with uncommitted changes in the code, which are not in git."

do_image=0; do_env=0; do_model=0
(( WITH_IMAGE )) && has image && has_member image.tar.gz && do_image=1
(( WITH_ENV ))   && has env   && has_member env.backup && do_env=1
(( WITH_MODEL )) && has model && has_member model-cache.tar && do_model=1

# Work out which models the backup holds. Each path is going to be written into your own cache folder, so a path
# that restore.sh would not itself have made is refused before anything is changed.
MODEL_PATHS=()   # the models the backup holds, as paths inside the cache folder
if (( do_model )); then
  if [[ -n $backup_models ]]; then
    # format 2: the manifest lists them, separated by commas
    IFS=, read -r -a MODEL_PATHS <<< "$backup_models"
  else   # a backup made when only the image model could be included: one model, named by model_dir
    MODEL_DIR=$(manifest model_dir)
    [[ $MODEL_DIR =~ ^models--[A-Za-z0-9._-]+$ ]] || die "The backup names a model folder I don't trust: '$MODEL_DIR'."
    MODEL_PATHS=("hub/$MODEL_DIR")
  fi
  # whatever the manifest says, each path must be a model folder (hub/models--NAME) or a file inside some other folder:
  # never a '..', never an absolute path, never the whole hub/ folder, which other tools' models share
  for path in "${MODEL_PATHS[@]}"; do
    valid_model_path "$path" || die "The backup names a model path I don't trust: '$path'."
  done
fi

# ---------------------------------------------------------------- what is in the way?
stamp=$(date +%Y%m%d-%H%M%S)
data_in_way=0; env_in_way=0; HF_DIR=""
if [[ -e data ]] && [[ -n $(ls -A data 2>/dev/null || true) ]]; then data_in_way=1; fi
if (( do_env )) && [[ -f .env ]] && ! member_cat env.backup | cmp -s .env -; then env_in_way=1; fi

if (( ! FORCE )); then
  (( data_in_way )) && die "./data already has files in it. Restoring would replace your current history. Use --force to move it aside first (it is kept, not deleted)." 3
  if (( env_in_way )); then warn "Keeping your current .env (it differs from the backup's). Use --force to replace it."; do_env=0; fi
fi

# Where the model goes depends on the .env that will be in force afterwards.
if (( do_model )); then
  if (( do_env )); then
    env_tmp=$(mktemp)   # private (mode 600); only to read HF_CACHE_DIR from, removed at once
    member_cat env.backup > "$env_tmp" || { rm -f -- "$env_tmp"; die "Could not read env.backup."; }
    HF_DIR=$(ENV_FILE="$env_tmp" hf_cache_dir); rm -f -- "$env_tmp"
  else
    HF_DIR=$(hf_cache_dir)
  fi
  # The rule for each model: not in the cache, restore it. In the cache and no --force, leave it alone and say so. In the cache with
  # --force, restore it and move the cached copy aside first (ASIDE remembers which, for the plan printed below).
  # each model on its own: one that is already in the cache is left alone (or moved aside with --force)
  WANT_PATHS=(); declare -A ASIDE=()
  for path in "${MODEL_PATHS[@]}"; do
    if [[ -e $HF_DIR/$path || -L $HF_DIR/$path ]]; then
      if (( FORCE )); then WANT_PATHS+=("$path"); ASIDE[$path]=1
      else warn "$path is already in the cache, so it is not restored. Use --force to move the cached copy aside and restore it."; fi
    else
      WANT_PATHS+=("$path")
    fi
  done
  # every model was already in the cache: skip the whole model step, including unpacking the archive
  (( ${#WANT_PATHS[@]} )) || do_model=0
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

# look inside the archives before unpacking anything
data_listing=$(member_cat data.tar.gz | tar -tzf -) || die "data.tar.gz can't be read."
check_listing "data" "data.tar.gz" "$data_listing"
if (( do_model )); then
  model_listing=$(member_cat model-cache.tar | tar -tf -) || die "model-cache.tar can't be read."
  check_listing_paths "model-cache.tar" "$model_listing" "${MODEL_PATHS[@]}"
fi

say ""
say "This will:"
if (( FORCE && data_in_way )); then say "  - move the current ./data aside to data.before-restore-$stamp"; fi
say "  - restore ./data from data.tar.gz"
if (( do_env )); then say "  - restore .env$( ((env_in_way)) && echo " (the current one is moved aside to .env.before-restore-$stamp)")"; fi
if (( do_image )); then say "  - load the Docker image$( [[ -n $current_image_id ]] && echo " (the current $IMAGE is kept as ai-image-studio:before-restore-$stamp)")"; fi
if (( do_model )); then
  # one line per model that will really be restored, saying so when the cached copy is moved aside first
  for path in "${WANT_PATHS[@]}"; do
    say "  - restore the model $HF_DIR/$path$( [[ -n ${ASIDE[$path]:-} ]] && echo " (the current copy is moved aside first)")"
  done
fi
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
member_cat data.tar.gz | tar -xzf - -C "$TMP"
[[ -d $TMP/data ]] || die "The data archive didn't contain a data folder."
if [[ -e data ]]; then
  if [[ -n $(ls -A data 2>/dev/null || true) ]]; then mv -- data "data.before-restore-$stamp"; else rmdir data; fi
fi
mv -- "$TMP/data" data

if (( do_env )); then
  say "Restoring .env..."
  if [[ -f .env ]] && (( env_in_way )); then mv -- .env ".env.before-restore-$stamp"; fi
  member_cat env.backup > .env
  chmod 600 .env
fi

if (( do_image )); then
  if [[ -n $current_image_id ]]; then docker tag "$IMAGE" "ai-image-studio:before-restore-$stamp"; fi
  say "Loading the Docker image (several GB; a few minutes)..."
  member_cat image.tar.gz | gunzip | docker load >/dev/null
  loaded=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || true)
  [[ -n $loaded ]] || die "The image was loaded but $IMAGE doesn't exist afterwards."
  if [[ $loaded != "$(manifest image_id)" ]]; then warn "The loaded image's id differs from the one in the manifest."; fi
fi

if (( do_model )); then
  # Restore only the wanted models. They are unpacked into a scratch folder first, so a failure part-way leaves your cache untouched,
  # and then each is moved into place. A copy already in the way was approved with --force and is moved aside, never deleted.
  say "Restoring the models (a few minutes)..."
  mkdir -p -- "$HF_DIR"
  mkdir -- "$TMP/model"
  member_cat model-cache.tar | tar -xf - -C "$TMP/model" -- "${WANT_PATHS[@]}"
  for path in "${WANT_PATHS[@]}"; do
    # the upscaler lives in a folder (upscalers/) that a fresh cache doesn't have yet
    mkdir -p -- "$(dirname -- "$HF_DIR/$path")"
    if [[ -e $HF_DIR/$path || -L $HF_DIR/$path ]]; then mv -- "$HF_DIR/$path" "$HF_DIR/$path.before-restore-$stamp"; fi
    mv -- "$TMP/model/$path" "$HF_DIR/$path"
  done
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
