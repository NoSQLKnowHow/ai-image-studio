# shellcheck shell=bash
# shellcheck disable=SC2034  # the names below are used by the scripts that source this file
# Helpers shared by backup.sh and restore.sh. Sourced by them; not meant to be run.

IMAGE="ai-image-studio:local"      # the image name in compose.yaml
SERVICE="studio"                   # the service name in compose.yaml
BACKUP_PREFIX="ai-image-studio-backup-"
# The models the studio loads when .env doesn't say otherwise (the same defaults as compose.yaml and config.py).
# A backup looks for exactly the models the studio would use, so these must stay in step with those files.
DEFAULT_MODEL="Qwen/Qwen-Image-2.1"
DEFAULT_MUSIC_MODEL="MiniMaxAI/MiniMax-Music3"
DEFAULT_UPSCALER="upscalers/RealESRGAN_x2plus.pth"   # inside the model cache folder

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$SCRIPT_DIR/.." && pwd)

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
# die MESSAGE [EXIT_CODE]. Never call it inside $(...): that would only end the subshell.
die()  { printf 'ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

# human BYTES -> "12.3 GiB"
human() {
  awk -v b="$1" 'BEGIN {
    split("B KiB MiB GiB TiB", u, " "); i = 1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    if (i == 1) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i]
  }'
}

# confirm QUESTION: asks y/N. With --yes (ASSUME_YES=1) it says yes; without a terminal it refuses,
# so a scheduled run never hangs waiting for an answer.
confirm() {
  (( ${ASSUME_YES:-0} )) && return 0
  [[ -t 0 ]] || die "$1 Run it again with --yes to go ahead without being asked." 3
  local answer
  read -r -p "$1 [y/N] " answer
  [[ $answer == [yY] || $answer == [yY][eE][sS] ]] || die "Stopped; nothing was changed." 3
}

# The studio is meant to run as your own user. As root, $HOME is /root, so the model cache would be
# looked for in the wrong place, and restored files would belong to root, which the container
# (running as you) could not write to.
refuse_root() {
  if [[ $(id -u) -eq 0 && ${ALLOW_ROOT:-0} -eq 0 ]]; then
    die "Run this as your normal user, not as root or with sudo (it would look for the model cache in the wrong home folder, and restored files would belong to root). Use --allow-root only if you know you want that." 2
  fi
}

# env_value NAME DEFAULT: the value Compose would use. The shell environment wins, then .env
# (read as text, never executed; set ENV_FILE to read another file), then DEFAULT.
env_value() {
  local name=$1 default=${2-} value="" line file=${ENV_FILE:-$REPO/.env}
  if [[ -n ${!name:-} ]]; then
    value=${!name}
  elif [[ -f $file ]]; then
    line=$(grep -E "^[[:space:]]*${name}=" "$file" | tail -n 1 || true)
    if [[ -n $line ]]; then
      value=${line#*=}
      value=$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//')
      value=${value#\"}; value=${value%\"}; value=${value#\'}; value=${value%\'}
    fi
  fi
  if [[ -n $value ]]; then printf '%s' "$value"; else printf '%s' "$default"; fi
}

# The folder on the host that compose.yaml mounts as the model cache.
hf_cache_dir() {
  local dir
  dir=$(env_value HF_CACHE_DIR "")
  [[ -n $dir ]] || dir="$HOME/.cache/huggingface"
  printf '%s' "${dir/#\~/$HOME}"
}

# hf_model_path SETTING DEFAULT: where the Hugging Face model named by that setting lives inside the cache folder
# ("Qwen/Qwen-Image-2.1" -> "hub/models--Qwen--Qwen-Image-2.1"). Fails for a model given as a path on disk, which is
# not in the cache at all.
hf_model_path() {
  local model
  model=$(env_value "$1" "$2")
  case $model in /*|./*|../*) return 1;; esac
  printf 'hub/models--%s' "${model//\//--}"
}

# upscaler_path: where Enlarge's model file lives inside the cache folder. STUDIO_UPSCALER_MODEL is a path as the
# container sees it, where the cache folder is /models; empty means the default file. Fails for a path outside /models.
upscaler_path() {
  local file
  file=$(env_value STUDIO_UPSCALER_MODEL "")
  if [[ -z $file ]]; then printf '%s' "$DEFAULT_UPSCALER"; return 0; fi
  case $file in /models/*) printf '%s' "${file#/models/}";; *) return 1;; esac
}

# valid_model_path PATH: is this a path inside the cache folder that a backup may hold? A model folder
# (hub/models--NAME), or a file inside some other folder (the upscaler). Never a ".." or an absolute path, and never
# anything else in hub/: that folder holds other tools' models too.
valid_model_path() {
  local path=$1
  [[ $path != *..* && $path != /* ]] || return 1
  [[ $path =~ ^hub/models--[A-Za-z0-9._-]+$ ]] && return 0
  [[ $path =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+$ && $path != hub/* ]]
}

# Is the studio's container running right now?
container_running() {
  local id
  id=$(docker compose ps -q "$SERVICE" 2>/dev/null | head -n 1 || true)
  [[ -n $id ]] && [[ $(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null || true) == "true" ]]
}

# Is the studio generating or holding queued jobs? Prints a sentence and returns 0 if so. Returns 1
# when idle, and also when that can't be found out (no curl or python3, or the page isn't reachable
# on localhost): a backup then goes ahead, as it would without this check.
studio_busy() {
  command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 || return 1
  local port json
  port=$(env_value STUDIO_PORT 8080)
  json=$(curl -fsS --max-time 3 "http://127.0.0.1:${port}/api/status" 2>/dev/null) || return 1
  printf '%s' "$json" | python3 -c '
import json, sys
try:
    queue = json.load(sys.stdin)["queue"]
except Exception:
    sys.exit(1)
running, queued = queue.get("running"), queue.get("queued") or 0
if not running and not queued:
    sys.exit(1)
parts = []
if running:
    parts.append("an image is being generated")
if queued:
    parts.append("%d job(s) are waiting" % queued)
print(" and ".join(parts))
'
}

# check_listing PREFIX NAME LISTING: refuse an archive listing (the output of `tar -t`) with any entry
# outside PREFIX/, with an absolute path, or with a ".." in it. Archives made by backup.sh always
# pass; this is a safety net for a damaged or tampered one, checked before anything is unpacked.
check_listing() {
  local prefix=$1 name=$2 listing=$3 bad
  bad=$(printf '%s\n' "$listing" | grep -Ev "^${prefix}(/|$)" | head -n 1 || true)
  [[ -z $bad ]] || die "Refusing to unpack $name: it holds an entry outside '$prefix/' (for example: $bad)."
  if printf '%s\n' "$listing" | grep -Eq '(^|/)\.\.(/|$)'; then
    die "Refusing to unpack $name: it holds a path containing '..'."
  fi
}

# check_listing_paths NAME LISTING PATH...: like check_listing, for an archive that may hold several paths (the models).
# Used on model-cache.tar, which holds several paths (the models), where check_listing handles a single one.
# Every entry in the archive must be one of the listed paths or lie inside one. Anything else, or any '..', is refused
# BEFORE anything is unpacked, so a damaged or tampered backup cannot write outside the model folders it declares.
check_listing_paths() {
  local name=$1 listing=$2 line path ok
  shift 2
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    # an entry is fine if it IS a listed path, or is inside one (the path followed by a slash and more)
    ok=0
    for path in "$@"; do
      if [[ $line == "$path" || $line == "$path"/* ]]; then ok=1; break; fi
    done
    # no listed path matched this entry: stop here, nothing has been unpacked yet
    (( ok )) || die "Refusing to unpack $name: it holds an entry outside the paths the backup lists (for example: $line)."
  done <<< "$listing"
  # a '..' anywhere could climb out of the folder it is unpacked into, even inside a listed path
  if printf '%s\n' "$listing" | grep -Eq '(^|/)\.\.(/|$)'; then
    die "Refusing to unpack $name: it holds a path containing '..'."
  fi
}

# ---------------------------------------------------------------------------------------------
# Reading a backup. A backup is one .tar file (what backup.sh makes) or a folder holding the same
# files (what you get by unpacking that .tar). Pieces are read straight out of the .tar with
# `tar -xO`, so nothing needs unpacking first, which matters when the backup is tens of GB.
#
#   data.tar.gz      your history and images
#   image.tar.gz     the Docker image (docker save, gzipped)
#   env.backup       your .env
#   model-cache.tar  the studio's models from the Hugging Face cache (hub/models--… folders, the upscaler file)
#   MANIFEST.txt     what this is (key=value lines)
#   SHA256SUMS       a checksum for every other file
KNOWN_MEMBERS=(data.tar.gz image.tar.gz env.backup model-cache.tar MANIFEST.txt SHA256SUMS)
BACKUP=""                          # the .tar file or folder, set by the caller before open_backup
BACKUP_KIND=""                     # "tar" or "dir"
declare -A MEMBER_NAME=()          # piece name -> the name it has inside the .tar
MANIFEST_TEXT=""

# open_backup: reads the list of pieces in $BACKUP, and refuses anything a backup made by
# backup.sh never contains: an unknown piece, a piece that appears twice, or no manifest.
open_backup() {
  local listing entry name
  MEMBER_NAME=()
  if [[ -d $BACKUP ]]; then
    BACKUP_KIND=dir
    for name in "${KNOWN_MEMBERS[@]}"; do
      if [[ -f $BACKUP/$name ]]; then MEMBER_NAME[$name]=$name; fi
    done
  elif [[ -f $BACKUP ]]; then
    BACKUP_KIND=tar
    listing=$(tar -tf "$BACKUP" 2>/dev/null) || die "$BACKUP isn't a readable .tar file (damaged, or not a tar at all)."
    while IFS= read -r entry; do
      [[ -n $entry && $entry != */ ]] || continue          # a folder entry such as ./
      name=${entry#./}
      [[ " ${KNOWN_MEMBERS[*]} " == *" $name "* ]] \
        || die "$BACKUP holds '$entry', which a backup made by backup.sh never contains. Refusing to use it."
      [[ -z ${MEMBER_NAME[$name]:-} ]] || die "$BACKUP holds '$name' twice. Refusing to use it."
      MEMBER_NAME[$name]=$entry
    done <<< "$listing"
  else
    die "$BACKUP isn't a file or a folder."
  fi
  if [[ -z ${MEMBER_NAME[MANIFEST.txt]:-} || -z ${MEMBER_NAME[SHA256SUMS]:-} ]]; then
    die "$BACKUP has no MANIFEST.txt and SHA256SUMS, so it doesn't look like a backup made by backup.sh."
  fi
  MANIFEST_TEXT=$(member_cat MANIFEST.txt) || die "Could not read MANIFEST.txt from $BACKUP."
}

has_member() { [[ -n ${MEMBER_NAME[$1]:-} ]]; }

# member_cat NAME: the piece's bytes on standard output.
member_cat() {
  if [[ $BACKUP_KIND == tar ]]; then tar -xOf "$BACKUP" -- "${MEMBER_NAME[$1]}"
  else cat -- "$BACKUP/$1"; fi
}

# manifest KEY: a value from MANIFEST.txt (empty when missing).
manifest() { printf '%s\n' "$MANIFEST_TEXT" | grep -m1 "^$1=" | cut -d= -f2- || true; }

# verify_backup: re-reads every piece and compares it with SHA256SUMS. Returns 0 only if each piece
# listed there is present and matches, and no piece is missing from the list.
verify_backup() {
  local sums want name got bad=0
  sums=$(member_cat SHA256SUMS) || return 1
  [[ -n $sums ]] || { warn "SHA256SUMS is empty."; return 1; }
  while read -r want name; do
    [[ -n $want && -n $name ]] || continue
    name=${name#\*}
    if ! has_member "$name"; then warn "$name is listed in SHA256SUMS but missing from the backup."; bad=1; continue; fi
    got=$(member_cat "$name" | sha256sum | cut -d' ' -f1) || got=""
    if [[ $got != "$want" ]]; then warn "$name is damaged: its checksum doesn't match."; bad=1; fi
  done <<< "$sums"
  for name in "${KNOWN_MEMBERS[@]}"; do
    [[ $name == SHA256SUMS ]] && continue
    has_member "$name" || continue
    if ! printf '%s\n' "$sums" | grep -Eq "^[0-9a-f]{64} [ *]${name//./\\.}$"; then
      warn "$name is in the backup but has no checksum in SHA256SUMS."; bad=1
    fi
  done
  return "$bad"
}
