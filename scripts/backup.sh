#!/usr/bin/env bash
# Back up AI Image Studio into ONE .tar file. See README "Backing up and restoring".
#
#   scripts/backup.sh [options] DESTINATION_FOLDER
#
# Makes DESTINATION_FOLDER/ai-image-studio-backup-<date>-<time>.tar, a single file you can copy to a
# NAS or anywhere else. Inside it (an ordinary tar, nothing proprietary):
#   data.tar.gz      your history and images (the ./data folder)           always
#   image.tar.gz     the Docker image, exactly as built                    unless --no-image
#   env.backup       your .env settings (may hold a token)                 unless --no-env
#   model-cache.tar  the studio's models from the Hugging Face cache       unless --no-model
#                    (the image model, the music model, the Enlarge upscaler file; nothing else in the cache)
#   MANIFEST.txt     what this is, and which git commit and image it came from
#   SHA256SUMS       a checksum for every other piece
set -euo pipefail
# shellcheck source=scripts/_backup_common.sh
. "$(dirname -- "${BASH_SOURCE[0]}")/_backup_common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/backup.sh [options] DESTINATION_FOLDER

Backs up your history and images (./data), the Docker image, your .env settings and the studio's models
into a single .tar file, named ai-image-studio-backup-<date>-<time>.tar, inside DESTINATION_FOLDER. The studio
is stopped for the few seconds it takes to copy the data, then started again; the image and models are
copied while it runs. The finished .tar is re-read and every checksum checked before it gets its name.

The studio's models are the three it uses, found from your .env settings: the image model (STUDIO_MODEL),
the music model (STUDIO_MUSIC_MODEL) and the Enlarge upscaler file (STUDIO_UPSCALER_MODEL), all in the
Hugging Face cache; about 60 GiB with the defaults. Nothing else in that cache is touched. One that isn't
there (never downloaded, or not in the cache at all) is skipped with a warning that names it.

Options:
  --no-model     leave out the models (they are included by default)
  --model        accepted, and does nothing: the models are included anyway (old command lines keep working)
  --no-image     leave out the Docker image
  --no-env       leave out the .env file
  --no-verify    skip the final re-read and checksum check (faster; not recommended)
  --keep N       afterwards delete the oldest backups in DESTINATION_FOLDER, keeping the newest N
  --yes, -y      don't ask questions (for scheduled runs)
  --interrupt    back up even if an image is being generated (the run is interrupted and marked failed)
  --dry-run      show what would be done and change nothing
  --allow-root   run as root (not recommended)
  -h, --help     this text

The .tar can contain your Hugging Face token (in .env), so it is readable only by you. Copy it to a
NAS or another machine, then check the copy with:  scripts/restore.sh --verify THE_COPY.tar

Exit codes: 0 done; 1 an error; 2 wrong usage; 3 not done because the studio is busy or you said no.
EOF
}

WITH_MODEL=1; WITH_IMAGE=1; WITH_ENV=1; VERIFY=1; KEEP=0
ASSUME_YES=0; DRY=0; INTERRUPT=0; ALLOW_ROOT=0; DEST=""
while (( $# )); do
  case $1 in
    --model)      WITH_MODEL=1 ;;   # the default since models were made part of every backup
    --no-model)   WITH_MODEL=0 ;;
    --no-image)   WITH_IMAGE=0 ;;
    --no-env)     WITH_ENV=0 ;;
    --no-verify)  VERIFY=0 ;;
    --keep)       [[ ${2:-} =~ ^[1-9][0-9]*$ ]] || die "--keep needs a whole number, 1 or more." 2
                  KEEP=$2; shift ;;
    --keep=*)     KEEP=${1#--keep=}
                  [[ $KEEP =~ ^[1-9][0-9]*$ ]] || die "--keep needs a whole number, 1 or more." 2 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    --interrupt)  INTERRUPT=1 ;;
    --dry-run)    DRY=1 ;;
    --allow-root) ALLOW_ROOT=1 ;;
    -h|--help)    usage; exit 0 ;;
    -*)           die "Unknown option: $1 (see --help)." 2 ;;
    *)            [[ -z $DEST ]] || die "Give one destination folder, not two." 2; DEST=$1 ;;
  esac
  shift
done
if [[ -z $DEST ]]; then usage >&2; exit 2; fi
case $DEST in /*) ;; *) DEST="$PWD/$DEST" ;; esac   # before we change directory
DEST=${DEST%/}

# ---------------------------------------------------------------- checks, before anything is touched
refuse_root
for tool in tar gzip sha256sum df du docker; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' is needed but isn't installed."
done
cd "$REPO"
[[ -f compose.yaml ]] || die "compose.yaml not found next to the scripts folder ($REPO)."
[[ -d data ]] || die "There is no data folder yet ($REPO/data), so there is nothing to back up. Has the studio been started?"
if (( WITH_IMAGE )); then
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || die "The Docker image $IMAGE doesn't exist on this machine. Build it first (README), or back up without it using --no-image."
fi
if (( WITH_ENV )) && [[ ! -f .env ]]; then
  warn "There is no .env file, so there are no settings to back up (the defaults are in use)."
  WITH_ENV=0
fi

# The studio's models that are in the cache. A model that can't be included is skipped with a warning that names
# it, never an error: a fresh install, or a model that was never used, must not stop every backup.
HF_DIR=""; MODEL_PATHS=(); MODEL_LABELS=(); MODELS_NOTE="no (--no-model)"
# want_model LABEL PATH WHY_NOT: adds PATH (inside the cache folder; empty = it isn't in the cache at all) if it is there.
want_model() {
  local label=$1 path=$2 why=$3
  if [[ -z $path ]]; then warn "Leaving the $label out of the backup: $why"; return 0; fi
  if ! valid_model_path "$path"; then warn "Leaving the $label out of the backup: '$path' has a name that a restore would refuse."; return 0; fi
  if [[ $path == hub/* && -d $HF_DIR/$path ]] || [[ $path != hub/* && -f $HF_DIR/$path ]]; then
    MODEL_PATHS+=("$path"); MODEL_LABELS+=("$label")
  else
    warn "Leaving the $label out of the backup: it isn't in the model cache yet ($HF_DIR/$path). It downloads the first time it is used."
  fi
}
if (( WITH_MODEL )); then
  HF_DIR=$(hf_cache_dir)
  path=$(hf_model_path STUDIO_MODEL "$DEFAULT_MODEL") || path=""
  want_model "image model" "$path" "STUDIO_MODEL is a folder on disk, not a model in the Hugging Face cache. Back that folder up yourself."
  path=$(hf_model_path STUDIO_MUSIC_MODEL "$DEFAULT_MUSIC_MODEL") || path=""
  want_model "music model" "$path" "STUDIO_MUSIC_MODEL is a folder on disk, not a model in the Hugging Face cache. Back that folder up yourself."
  path=$(upscaler_path) || path=""
  want_model "Enlarge upscaler" "$path" "STUDIO_UPSCALER_MODEL is outside the model cache (/models in the container). Back that file up yourself."
  if (( ${#MODEL_PATHS[@]} == 0 )); then
    warn "None of the studio's models was found in the cache, so this backup holds no model."
    WITH_MODEL=0; MODELS_NOTE="no (none was found in the cache)"
  fi
fi

STAMP=$(date +%Y%m%d-%H%M%S)
if [[ -e $DEST/${BACKUP_PREFIX}${STAMP}.tar || -e $DEST/.partial-${STAMP} || -e $DEST/.partial-${STAMP}.tar ]]; then
  sleep 1   # another backup was started in the same second
  STAMP=$(date +%Y%m%d-%H%M%S)
fi
FINAL="$DEST/${BACKUP_PREFIX}${STAMP}.tar"
STAGE="$DEST/.partial-${STAMP}"             # the pieces, while they are being made
PARTIAL_TAR="$DEST/.partial-${STAMP}.tar"   # the .tar, until it has been checked
[[ ! -e $FINAL && ! -e $STAGE && ! -e $PARTIAL_TAR ]] || die "$FINAL already exists. Wait a moment and run it again."

# ---------------------------------------------------------------- what it will cost
data_bytes=$(du -sb data | cut -f1)
image_bytes=0; model_bytes=0; IMAGE_ID=""
if (( WITH_IMAGE )); then
  IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}')
  image_bytes=$(docker image inspect "$IMAGE" --format '{{.Size}}')
fi
for (( i = 0; i < ${#MODEL_PATHS[@]}; i++ )); do
  # a model folder is counted as it is (its snapshots link to its blobs); the upscaler file may itself be a link
  if [[ -d $HF_DIR/${MODEL_PATHS[i]} ]]; then one=$(du -sb "$HF_DIR/${MODEL_PATHS[i]}" | cut -f1); else one=$(du -sbL "$HF_DIR/${MODEL_PATHS[i]}" | cut -f1); fi
  model_bytes=$(( model_bytes + one ))
done
need=$(( data_bytes + image_bytes + model_bytes ))
largest=$data_bytes
(( image_bytes > largest )) && largest=$image_bytes
(( model_bytes > largest )) && largest=$model_bytes
peak=$(( need + largest ))   # the pieces are moved into the .tar one at a time, so at worst: all of it, plus the biggest piece

say "Backing up AI Image Studio ($REPO)"
say "  to:      $FINAL"
say "  data:    yes ($(human "$data_bytes"))"
say "  image:   $( ((WITH_IMAGE)) && echo "yes ($IMAGE)" || echo no )"
say "  .env:    $( ((WITH_ENV)) && echo yes || echo no )"
if (( WITH_MODEL )); then
  say "  models:  yes ($(human "$model_bytes"))"
  for (( i = 0; i < ${#MODEL_PATHS[@]}; i++ )); do say "             - ${MODEL_LABELS[i]}: ${MODEL_PATHS[i]}"; done
else
  say "  models:  $MODELS_NOTE"
fi

if [[ -d $DEST ]]; then avail=$(df --output=avail -B1 "$DEST" | tail -n 1 | tr -d ' ')
else                    avail=$(df --output=avail -B1 "$(dirname -- "$DEST")" | tail -n 1 | tr -d ' '); fi
if (( avail < peak )); then
  warn "May need up to $(human "$peak") free at the destination (the image is compressed, so less in practice), and $(human "$avail") is free."
  (( DRY )) || confirm "Carry on anyway?"
fi

# ---------------------------------------------------------------- is it busy?
WAS_RUNNING=0
if container_running; then
  WAS_RUNNING=1
  if busy=$(studio_busy); then
    if (( INTERRUPT )); then
      warn "$busy. Backing up anyway (--interrupt): the running image will be marked failed."
    else
      die "Not now: $busy. Stopping the studio would cut that off. Try again when it's idle, or use --interrupt." 3
    fi
  fi
fi

if (( DRY )); then
  say ""
  say "Dry run: nothing was changed. It would:"
  (( WAS_RUNNING )) && say "  - stop the studio (docker compose stop $SERVICE)"
  say "  - pack ./data into data.tar.gz"
  (( WAS_RUNNING )) && say "  - start the studio again"
  (( WITH_IMAGE )) && say "  - save $IMAGE (docker save | gzip) into image.tar.gz"
  (( WITH_MODEL )) && say "  - pack the models (${MODEL_PATHS[*]}) from $HF_DIR into model-cache.tar"
  (( WITH_ENV )) && say "  - copy .env"
  say "  - write MANIFEST.txt and SHA256SUMS, and put everything into one file: $FINAL"
  (( VERIFY )) && say "  - re-read that file and check every checksum, before it gets its final name"
  (( KEEP )) && say "  - delete all but the newest $KEEP backups in $DEST"
  exit 0
fi

# ---------------------------------------------------------------- clean-up that always happens
STOPPED_BY_US=0
# shellcheck disable=SC2317  # runs from the EXIT trap
finish() {
  local rc=$?
  trap - EXIT
  if (( STOPPED_BY_US )); then
    warn "Starting the studio again..."
    docker compose start "$SERVICE" >/dev/null 2>&1 \
      || warn "Could not start it again. Run: docker compose start $SERVICE"
  fi
  rm -rf -- "$STAGE"
  rm -f -- "$PARTIAL_TAR"
  if (( rc != 0 )); then
    warn "The backup did not finish. Whatever had been written so far was removed; nothing was kept."
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

umask 077   # the .tar can hold a token: only you may read it
mkdir -p -- "$DEST"
[[ -w $DEST ]] || die "$DEST isn't writable by you."
mkdir -- "$STAGE"

# ---------------------------------------------------------------- 1. the data, while the studio is stopped
# The database is in WAL mode, so copying it while the studio runs could give an inconsistent copy.
if (( WAS_RUNNING )); then
  say "Stopping the studio for the few seconds it takes to copy the data..."
  SECONDS=0
  STOPPED_BY_US=1
  docker compose stop "$SERVICE" >/dev/null
fi
say "Packing your history and images..."
tar -czf "$STAGE/data.tar.gz" -C "$REPO" data
if (( STOPPED_BY_US )); then
  docker compose start "$SERVICE" >/dev/null
  STOPPED_BY_US=0
  say "  The studio is running again (it was stopped for ${SECONDS}s)."
fi

# ---------------------------------------------------------------- 2. everything that can be copied while it runs
if (( WITH_IMAGE )); then
  say "Saving the Docker image (several GB; a few minutes)..."
  docker save "$IMAGE" | gzip > "$STAGE/image.tar.gz"
fi
if (( WITH_MODEL )); then
  say "Packing the models ($(human "$model_bytes"); a few minutes)..."
  for (( i = 0; i < ${#MODEL_PATHS[@]}; i++ )); do
    # a model folder keeps its own links (its snapshots point at its blobs, which must not be stored twice); a model
    # FILE that is itself a link is stored as the file, so that a restore doesn't make a link that points nowhere
    deref=(); [[ ${MODEL_PATHS[i]} == hub/* ]] || deref=(-h)
    if (( i == 0 )); then mode=-cf; else mode=-rf; fi
    tar "$mode" "$STAGE/model-cache.tar" ${deref[@]+"${deref[@]}"} -C "$HF_DIR" -- "${MODEL_PATHS[i]}"
  done
fi
if (( WITH_ENV )); then
  cp -p .env "$STAGE/env.backup"
  chmod 600 "$STAGE/env.backup"
fi

# ---------------------------------------------------------------- 3. say what is in it, and checksum it
included="data"
(( WITH_IMAGE )) && included+=",image"
(( WITH_ENV )) && included+=",env"
(( WITH_MODEL )) && included+=",model"
git_commit=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo none)
git_dirty=$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ' || echo 0)
version=$(grep -m1 '__version__' backend/studio/__init__.py 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' || true)
format=1; (( WITH_MODEL )) && format=2   # a backup with models says which; format 1 is the layout older scripts read
{
  echo "format=$format"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(hostname)"
  echo "user=$(id -un)"
  echo "git_commit=$git_commit"
  echo "git_uncommitted_files=$git_dirty"
  echo "studio_version=${version:-unknown}"
  echo "image_name=$IMAGE"
  echo "image_id=$IMAGE_ID"
  echo "included=$included"
  if (( WITH_MODEL )); then echo "model_paths=$(IFS=,; echo "${MODEL_PATHS[*]}")"; else echo "model_dir="; fi
} > "$STAGE/MANIFEST.txt"

say "Checksumming..."
pieces=()
for name in data.tar.gz env.backup image.tar.gz model-cache.tar MANIFEST.txt; do
  [[ -f $STAGE/$name ]] && pieces+=("$name")
done
(cd "$STAGE" && sha256sum -- "${pieces[@]}" > SHA256SUMS)
pieces+=(SHA256SUMS)

# ---------------------------------------------------------------- 4. one file
# Each piece is appended to the .tar and deleted at once, so the disk never holds the whole backup
# twice. The .tar is not called by its real name until it has been checked.
say "Putting it all into one file..."
first=1
for name in "${pieces[@]}"; do
  if (( first )); then tar -cf "$PARTIAL_TAR" -C "$STAGE" -- "$name"; first=0
  else                 tar -rf "$PARTIAL_TAR" -C "$STAGE" -- "$name"; fi
  rm -f -- "$STAGE/$name"
done
rmdir -- "$STAGE"

if (( VERIFY )); then
  say "Re-reading the file to check every checksum (a few minutes for a big one)..."
  BACKUP=$PARTIAL_TAR
  open_backup
  verify_backup || die "The finished .tar failed its own checksum check; it was not kept. Check the disk and try again."
fi
mv -- "$PARTIAL_TAR" "$FINAL"

# ---------------------------------------------------------------- 5. keep only the newest N
if (( KEEP )); then
  i=0
  while IFS= read -r name; do
    [[ $name =~ ^${BACKUP_PREFIX}[0-9]{8}-[0-9]{6}\.tar$ ]] || continue
    i=$(( i + 1 ))
    (( i > KEEP )) || continue
    if tar -tf "$DEST/$name" MANIFEST.txt >/dev/null 2>&1 && tar -tf "$DEST/$name" SHA256SUMS >/dev/null 2>&1; then
      say "Removing old backup $name"
      rm -f -- "${DEST:?}/$name"
    else
      warn "Leaving $name alone: it doesn't look like one of ours (no MANIFEST.txt and SHA256SUMS inside)."
    fi
  done < <(find "$DEST" -maxdepth 1 -mindepth 1 -type f -name "${BACKUP_PREFIX}*.tar" -printf '%f\n' | sort -r)
fi

say ""
say "Done: $FINAL ($(human "$(stat -c %s "$FINAL")"))"
tar -tvf "$FINAL" | awk '{ printf "  %-18s %s bytes\n", $6, $3 }'
say ""
say "It is one ordinary .tar file: copy it wherever you like, for example to a NAS:"
say "  cp \"$FINAL\" /path/to/nas/    or    rsync -ah --progress \"$FINAL\" user@nas:backups/"
say "Check the copy arrived intact (no Docker needed):  scripts/restore.sh --verify /path/to/nas/$(basename -- "$FINAL")"
say "To restore:  scripts/restore.sh \"$FINAL\""
(( WITH_ENV )) && say "Note: it may contain your Hugging Face token (in .env). Keep it somewhere private."
exit 0
