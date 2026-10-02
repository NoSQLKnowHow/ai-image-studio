# shellcheck shell=bash
# shellcheck disable=SC2034  # the names below are used by the scripts that source this file
# Helpers shared by backup.sh and restore.sh. Sourced by them; not meant to be run.

IMAGE="ai-image-studio:local"      # the image name in compose.yaml
SERVICE="studio"                   # the service name in compose.yaml
BACKUP_PREFIX="ai-image-studio-backup-"
DEFAULT_MODEL="Qwen/Qwen-Image-2.1"

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

# The model's folder inside that cache ("Qwen/Qwen-Image-2.1" -> "models--Qwen--Qwen-Image-2.1").
# Fails for a model given as a path on disk, which is not in the cache at all.
model_dir_name() {
  local model
  model=$(env_value STUDIO_MODEL "$DEFAULT_MODEL")
  case $model in /*|./*|../*) return 1;; esac
  printf 'models--%s' "${model//\//--}"
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

# check_archive_paths ARCHIVE PREFIX: refuse an archive with any entry outside PREFIX/, with an
# absolute path, or with a ".." in it. Backups made by backup.sh always pass; this is a safety net
# for a damaged or tampered archive, checked before anything is extracted.
check_archive_paths() {
  local archive=$1 prefix=$2 flag="-tf" listing bad
  [[ $archive == *.gz ]] && flag="-tzf"
  listing=$(tar "$flag" "$archive") || die "Could not read $archive."
  bad=$(printf '%s\n' "$listing" | grep -Ev "^${prefix}(/|$)" | head -n 3 || true)
  [[ -z $bad ]] || die "Refusing to unpack $archive: it holds entries outside '$prefix/' (for example: $(printf '%s' "$bad" | head -n 1))."
  if printf '%s\n' "$listing" | grep -Eq '(^|/)\.\.(/|$)'; then
    die "Refusing to unpack $archive: it holds a path containing '..'."
  fi
}
