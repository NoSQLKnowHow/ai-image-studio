#!/usr/bin/env bash
# Tests for scripts/backup.sh and scripts/restore.sh:   bash scripts/tests/backup_restore_test.sh
# (FAILFAST=1 stops at the first failure.)
#
# Everything runs in a throw-away folder. `docker` is replaced by a stand-in that records every
# call and pretends to be a container, an image and `docker save` / `docker load`; tar, gzip, the
# checksums and the SQLite database are real. So this proves the scripts' logic (the single .tar
# file, order of steps, restarts after failures, refusals, integrity checks, round trips). It does
# not prove how real Docker behaves: do a first real backup with --dry-run, then without, on the Spark.
# shellcheck disable=SC2012  # `ls` is used on folders this test made itself, with plain names
set -uo pipefail

SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
T=$(mktemp -d)
trap 'rm -rf -- "$T"' EXIT
export STUB_STATE="$T/stub"
export HOME="$T/home"
mkdir -p "$STUB_STATE" "$T/bin" "$HOME"
PASS=0; FAIL=0

# ------------------------------------------------------------------ the stand-in commands
cat > "$T/bin/docker" <<'STUB'
#!/usr/bin/env bash
S=${STUB_STATE:?}
echo "docker $*" >> "$S/calls.log"
case "$1" in
  compose)
    case "$2" in
      ps)    [[ -f $S/container ]] && echo cid123; exit 0 ;;
      stop)  [[ -n ${STUB_FAIL_STOP:-} ]] && exit 1; echo false > "$S/running"; exit 0 ;;
      start) echo true > "$S/running"; exit 0 ;;
      up)    : > "$S/container"; echo true > "$S/running"; exit 0 ;;
    esac ;;
  inspect) cat "$S/running" 2>/dev/null || echo false; exit 0 ;;
  image)   # docker image inspect IMAGE [--format X]
    [[ -f $S/image_id ]] || exit 1
    case "${*: -1}" in
      '{{.Id}}')   cat "$S/image_id" ;;
      '{{.Size}}') echo 123456789 ;;
    esac
    exit 0 ;;
  save)
    [[ -n ${STUB_FAIL_SAVE:-} ]] && { echo "stub: save failed" >&2; exit 1; }
    echo "FAKE-IMAGE-TAR id=$(cat "$S/image_id")"; head -c 200000 /dev/urandom; exit 0 ;;
  load)
    read -r first; echo "${first##*id=}" > "$S/image_id"; cat > /dev/null; exit 0 ;;
  tag) exit 0 ;;
esac
echo "stub docker: unexpected call: $*" >&2; exit 99
STUB
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
[[ -f ${STUB_STATE:?}/status.json ]] && { cat "$STUB_STATE/status.json"; exit 0; }
exit 7
STUB
# a tar that can fail, or silently damage what it writes, on demand
cat > "$T/bin/tar" <<'STUB'
#!/usr/bin/env bash
# when STUB_LOG_TAR names a file, every tar call is logged there, so a test can check what restore.sh asked tar to extract
[[ -n ${STUB_LOG_TAR:-} ]] && echo "$*" >> "$STUB_LOG_TAR"
if [[ -n ${STUB_FAIL_TAR:-} && $* == *data.tar.gz* && $1 == -czf ]]; then echo "tar: stub failure" >&2; exit 2; fi
if [[ -n ${STUB_CORRUPT_TAR:-} && $1 == -rf && $* == *MANIFEST.txt* ]]; then   # the last-but-one piece is being appended
  /usr/bin/tar "$@"; rc=$?
  printf 'X' | dd of="$2" bs=1 seek=2000 conv=notrunc 2>/dev/null
  exit "$rc"
fi
exec /usr/bin/tar "$@"
STUB
cat > "$T/bin/df" <<'STUB'
#!/usr/bin/env bash
if [[ -n ${STUB_DF_LOW:-} && $* == *--output=avail* ]]; then printf 'Avail\n1000\n'; exit 0; fi
exec /usr/bin/df "$@"
STUB
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"

# ------------------------------------------------------------------ helpers
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [[ -n ${2:-} ]] && printf '        %s\n' "$2"; [[ -z ${FAILFAST:-} ]] || exit 1; }
check() { local desc=$1; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }
contains() { local desc=$1 text=$2 needle=$3
  if [[ $text == *"$needle"* ]]; then ok "$desc"; else bad "$desc" "wanted: $needle | got: ${text:0:300}"; fi; }
equals() { local desc=$1 got=$2 want=$3
  if [[ $got == "$want" ]]; then ok "$desc"; else bad "$desc" "wanted: $want | got: $got"; fi; }
section() { printf '\n%s\n' "$1"; }
calls() { cat "$STUB_STATE/calls.log" 2>/dev/null || true; }
line_of() { calls | grep -n -- "$1" | head -n 1 | cut -d: -f1; }
tree_hash() { (cd "$1" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1); }
human_of() { bash -c '. "$1"; human "$2"' _ "$SRC/scripts/_backup_common.sh" "$1"; }   # the scripts' own size wording, in a child process
count_in() { find "$1" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' '; }

REPO="$T/my repo"              # a space in the path, on purpose
DEST="$T/backups dir"
HFDIR="$HOME/.cache/huggingface"
MODEL="$HFDIR/hub/models--Qwen--Qwen-Image-2.1"
MUSIC="$HFDIR/hub/models--MiniMaxAI--MiniMax-Music3"
UPSCALER="$HFDIR/upscalers/RealESRGAN_x2plus.pth"
OTHER="$HFDIR/hub/models--someone--hermes-llm"      # another tool's model in the same cache: never backed up

bk() { "$REPO/scripts/backup.sh" --allow-root "$@"; }
rs() { "$REPO/scripts/restore.sh" --allow-root "$@"; }
latest() { ls "$DEST"/ai-image-studio-backup-*.tar 2>/dev/null | sort | tail -n 1; }
member() { tar -xOf "$B" -- "$1"; }          # one piece of the backup in $B, on stdout
unpack() { rm -rf "$2"; mkdir -p "$2"; tar -xf "$1" -C "$2"; }   # unpack TAR into DIR

# Builds a fresh fake machine before each group of tests: a repo folder (with git history, as the real one has); ./data with two
# pictures and a SQLite database in WAL mode (the reason the studio must be stopped to copy it); and a Hugging Face cache holding the
# image model laid out as the real cache is: blobs, plus snapshot files that are symlinks to them. The PRISTINE_* hashes remember
# what everything looked like, so a test can prove that a restore gave back identical bytes.
make_pristine() {
  rm -rf "$REPO" "$DEST" "$HFDIR"
  mkdir -p "$REPO/scripts" "$REPO/backend/studio" "$REPO/data/images/run1" "$REPO/data/thumbs/run1" "$DEST"
  cp "$SRC"/scripts/backup.sh "$SRC"/scripts/restore.sh "$SRC"/scripts/_backup_common.sh "$REPO/scripts/"
  cp "$SRC/compose.yaml" "$REPO/"
  echo '__version__ = "0.0.0-test"' > "$REPO/backend/studio/__init__.py"
  printf 'STUDIO_PORT=8080\nHF_TOKEN=secret-token\n' > "$REPO/.env"
  head -c 300000 /dev/urandom > "$REPO/data/images/run1/0.png"
  head -c 5000 /dev/urandom > "$REPO/data/thumbs/run1/0.webp"
  python3 - "$REPO/data/studio.sqlite" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.execute("PRAGMA journal_mode=WAL")
c.execute("CREATE TABLE runs (id TEXT, prompt TEXT)")
c.executemany("INSERT INTO runs VALUES (?, ?)", [(f"r{i}", f"prompt number {i}") for i in range(50)])
c.commit(); c.close()
PY
  mkdir -p "$MODEL/blobs" "$MODEL/snapshots/rev1/transformer" "$MODEL/refs"
  head -c 400000 /dev/urandom > "$MODEL/blobs/abc123"
  ln -s ../../../blobs/abc123 "$MODEL/snapshots/rev1/transformer/model.safetensors"
  echo rev1 > "$MODEL/refs/main"
  (cd "$REPO" && git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -q -m init) >/dev/null
  PRISTINE_DATA=$(tree_hash "$REPO/data")
  PRISTINE_ENV=$(sha256sum "$REPO/.env" | cut -d' ' -f1)
  PRISTINE_MODEL=$(tree_hash "$MODEL")
}
# the music model, the Enlarge upscaler file and another tool's model, next to the image model that make_pristine made
# The music model has the same blobs-and-symlinks layout as the image model; the Enlarge upscaler is one plain file in upscalers/.
# OTHER stands for a model that another tool (Hermes' vLLM, say) keeps in the same cache: no backup may ever contain it.
add_models() {
  mkdir -p "$MUSIC/blobs" "$MUSIC/snapshots/rev9/audio" "$MUSIC/refs" "$OTHER/blobs" "$HFDIR/upscalers"
  head -c 300000 /dev/urandom > "$MUSIC/blobs/def456"
  ln -s ../../../blobs/def456 "$MUSIC/snapshots/rev9/audio/model.safetensors"
  echo rev9 > "$MUSIC/refs/main"
  head -c 100000 /dev/urandom > "$UPSCALER"
  head -c 200000 /dev/urandom > "$OTHER/blobs/zzz"
  PRISTINE_MUSIC=$(tree_hash "$MUSIC"); PRISTINE_UP=$(sha256sum "$UPSCALER" | cut -d' ' -f1); PRISTINE_OTHER=$(tree_hash "$OTHER")
}
reset_stub() {   # container exists and runs; the image exists
  rm -rf "$STUB_STATE"; mkdir -p "$STUB_STATE"
  : > "$STUB_STATE/container"; echo true > "$STUB_STATE/running"
  echo "sha256:1111aaaa" > "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
  # clear every failure switch, so a test that set one cannot leak it into the next
  unset STUB_FAIL_STOP STUB_FAIL_SAVE STUB_FAIL_TAR STUB_CORRUPT_TAR STUB_DF_LOW
}
fresh() { make_pristine; reset_stub; }

# a backup .tar made by hand, to test what restore does with files that are NOT from backup.sh:
#   mk_backup NAME INNER_DATA_ENTRY...  makes $T/NAME.tar with a valid manifest and checksums
mk_backup() {
  local d="$T/mk-$1"; shift; rm -rf "$d"; mkdir -p "$d"
  python3 - "$d/data.tar.gz" "$@" <<'PY'
import io, sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as t:
    for name in sys.argv[2:]:
        data = b"x"; info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
PY
  printf 'format=1\ncreated_utc=now\nhost=h\nuser=u\ngit_commit=none\ngit_uncommitted_files=0\nstudio_version=t\nimage_name=x\nimage_id=\nincluded=data\nmodel_dir=\n' > "$d/MANIFEST.txt"
  (cd "$d" && sha256sum data.tar.gz MANIFEST.txt > SHA256SUMS && tar -cf "$d.tar" data.tar.gz MANIFEST.txt SHA256SUMS)
}

# a backup as the OLD backup.sh made it: format 1, one model, named by model_dir
# Real backups in this format exist (every backup made before models became part of the default), so restore.sh must keep
# reading them. The contents are made by plain tar calls, exactly as the old backup.sh did it.
mk_legacy() {   # mk_legacy [MODEL_DIR]
  local d="$T/legacy" model_dir=${1:-models--Qwen--Qwen-Image-2.1}; rm -rf "$d" "$d.tar"; mkdir -p "$d"
  tar -czf "$d/data.tar.gz" -C "$REPO" data
  tar -cf "$d/model-cache.tar" -C "$HFDIR" hub/models--Qwen--Qwen-Image-2.1
  printf 'format=1\ncreated_utc=now\nhost=h\nuser=u\ngit_commit=none\ngit_uncommitted_files=0\nstudio_version=t\nimage_name=x\nimage_id=\nincluded=data,model\nmodel_dir=%s\n' "$model_dir" > "$d/MANIFEST.txt"
  (cd "$d" && sha256sum data.tar.gz model-cache.tar MANIFEST.txt > SHA256SUMS && tar -cf "$d.tar" data.tar.gz model-cache.tar MANIFEST.txt SHA256SUMS)
}
#   mk_model_backup NAME FORMAT MODEL_PATHS ENTRY...: a hand-made backup whose model-cache.tar holds the given entries
# the files inside model-cache.tar are one byte each: only their NAMES matter to what these tests check (which paths are accepted)
mk_model_backup() {
  local d="$T/mm-$1" format=$2 paths=$3; shift 3; rm -rf "$d" "$d.tar"; mkdir -p "$d"
  python3 - "$d/data.tar.gz" "$d/model-cache.tar" "$@" <<'PY'
import io, sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as t:
    info = tarfile.TarInfo("data/ok.txt"); info.size = 1; t.addfile(info, io.BytesIO(b"x"))
with tarfile.open(sys.argv[2], "w") as t:
    for name in sys.argv[3:]:
        info = tarfile.TarInfo(name); info.size = 1; t.addfile(info, io.BytesIO(b"m"))
PY
  printf 'format=%s\ncreated_utc=now\nhost=h\nuser=u\ngit_commit=none\ngit_uncommitted_files=0\nstudio_version=t\nimage_name=x\nimage_id=\nincluded=data,model\nmodel_paths=%s\n' "$format" "$paths" > "$d/MANIFEST.txt"
  (cd "$d" && sha256sum data.tar.gz model-cache.tar MANIFEST.txt > SHA256SUMS && tar -cf "$d.tar" data.tar.gz model-cache.tar MANIFEST.txt SHA256SUMS)
}

# ================================================================== backup
section "usage and options"
fresh
out=$(bk --help 2>&1); rc=$?
equals "--help exits 0" "$rc" 0
contains "--help mentions the single .tar" "$out" ".tar"
contains "--help lists --no-model" "$out" "--no-model"
contains "--help says the models are the three the studio uses" "$out" "Enlarge upscaler"
out=$(bk 2>&1); rc=$?
equals "no destination exits 2" "$rc" 2
out=$(bk --nonsense "$DEST" 2>&1); rc=$?
equals "unknown option exits 2" "$rc" 2
contains "unknown option is named" "$out" "--nonsense"
out=$(bk "$DEST" "$DEST/two" 2>&1); rc=$?
equals "two destinations exits 2" "$rc" 2
out=$(bk --keep 0 "$DEST" 2>&1); rc=$?
equals "--keep 0 exits 2" "$rc" 2
if [[ $(id -u) -eq 0 ]]; then
  out=$("$REPO/scripts/backup.sh" "$DEST" 2>&1); rc=$?
  equals "refuses to run as root without --allow-root" "$rc" 2
  contains "says why" "$out" "not as root"
fi

section "dry run changes nothing"
fresh
add_models
out=$(bk --dry-run "$DEST" 2>&1); rc=$?
equals "dry run exits 0" "$rc" 0
# a dry run must list the models it found, so you can see what a real run would pack before committing to tens of GB
contains "dry run lists the models it would pack" "$out" "pack the models (hub/models--Qwen--Qwen-Image-2.1 hub/models--MiniMaxAI--MiniMax-Music3 upscalers/RealESRGAN_x2plus.pth)"
contains "dry run says so" "$out" "Dry run"
contains "dry run names the .tar it would make" "$out" "ai-image-studio-backup-"
equals "dry run creates nothing in the destination" "$(count_in "$DEST")" 0
check "dry run never stops the studio" bash -c "! grep -q 'compose stop' '$STUB_STATE/calls.log'"
equals "studio still running" "$(cat "$STUB_STATE/running")" true

section "a normal backup of a running studio makes ONE .tar file"
fresh
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
B=$(latest)
check "the result is a regular file ending in .tar" test -f "$B" -a "${B##*.}" = tar
equals "the destination holds that one file and nothing else (no folder, no leftovers)" "$(count_in "$DEST")" 1
equals "it is a plain, uncompressed tar (ustar header)" "$(dd if="$B" bs=1 skip=257 count=5 2>/dev/null)" ustar
equals "readable only by you (600)" "$(stat -c %a "$B")" 600
# the models are part of every backup now, so model-cache.tar is one of the pieces
equals "it holds exactly these pieces" "$(tar -tf "$B" | LC_ALL=C sort | tr '\n' ' ')" "MANIFEST.txt SHA256SUMS data.tar.gz env.backup image.tar.gz model-cache.tar "
unpack "$B" "$T/x"
check "checksums verify after unpacking it by hand" bash -c "cd '$T/x' && sha256sum -c --quiet SHA256SUMS"
check "the data piece is a valid gzip" bash -c "member() { tar -xOf '$B' -- \"\$1\"; }; member data.tar.gz | gzip -t"
contains "manifest records the image id" "$(member MANIFEST.txt)" "image_id=sha256:1111aaaa"
contains "manifest records the commit" "$(member MANIFEST.txt)" "git_commit=$(git -C "$REPO" rev-parse HEAD)"
# this backup has models, so the manifest says so, and it is format 2 (a backup without models stays format 1)
equals "manifest records what's included, the models too" "$(member MANIFEST.txt | grep '^included=')" "included=data,image,env,model"
equals "a backup with models is format 2" "$(member MANIFEST.txt | grep '^format=')" "format=2"
rm -rf "$T/xd"; mkdir -p "$T/xd"; member data.tar.gz | tar -xz -C "$T/xd"
equals "archived data is identical to the live data" "$(tree_hash "$T/xd/data")" "$PRISTINE_DATA"
equals "the .env copy keeps mode 600 inside the tar" "$(tar -tvf "$B" env.backup | cut -c1-10)" "-rw-------"
a=$(line_of "compose stop"); b=$(line_of "compose start"); c=$(line_of "docker save")
check "order: stop, then start, then save the image" test "$a" -lt "$b" -a "$b" -lt "$c"
equals "studio is running again afterwards" "$(cat "$STUB_STATE/running")" true
check "the live data was not modified" test "$(tree_hash "$REPO/data")" = "$PRISTINE_DATA"
contains "the summary says how to copy it to a NAS" "$out" "NAS"
contains "the summary says how to check the copy" "$out" "restore.sh --verify"

# The new default. With all three models in the cache, a plain backup holds all three, in a fixed order, and nothing else from the
# cache (another tool's model in the same folder must stay out).
section "the models are in every backup, by default"
fresh; add_models
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
B=$(latest)
check "model-cache.tar is inside the .tar" bash -c "tar -tf '$B' | grep -qx model-cache.tar"
equals "the manifest lists the three models, the image model first" "$(member MANIFEST.txt | grep '^model_paths=')" "model_paths=hub/models--Qwen--Qwen-Image-2.1,hub/models--MiniMaxAI--MiniMax-Music3,upscalers/RealESRGAN_x2plus.pth"
# what is inside model-cache.tar, checked three ways: the wanted paths are there, another tool's model is not, and nothing else is
listing=$(member model-cache.tar | tar -tf -)
contains "the image model is in it" "$listing" "hub/models--Qwen--Qwen-Image-2.1/blobs/abc123"
contains "the music model is in it" "$listing" "hub/models--MiniMaxAI--MiniMax-Music3/blobs/def456"
contains "the upscaler file is in it" "$listing" "upscalers/RealESRGAN_x2plus.pth"
equals "another tool's model in the same cache is NOT in it" "$(grep -c 'hermes' <<< "$listing" || true)" 0
equals "and nothing else is: every entry is under one of the three" "$(grep -Evc '^(hub/models--Qwen--Qwen-Image-2\.1|hub/models--MiniMaxAI--MiniMax-Music3|upscalers/RealESRGAN_x2plus\.pth)(/|$)' <<< "$listing" || true)" 0
# symlinks must be stored as symlinks, not followed: a snapshot file points at a blob, and following it would store every blob
# twice (and a restored cache would no longer have the layout the Hugging Face libraries expect)
verbose=$(member model-cache.tar | tar -tvf -)
contains "the image model's symlinks are kept as symlinks" "$verbose" "model.safetensors -> ../../../blobs/abc123"
contains "so are the music model's" "$verbose" "model.safetensors -> ../../../blobs/def456"
equals "each blob is stored once (a folder's links are not followed)" "$(grep -c 'blobs/abc123$' <<< "$listing")" 1
equals "no warning about a missing model" "$(grep -c 'Leaving' <<< "$out" || true)" 0
equals "the backup left another tool's model alone" "$(tree_hash "$OTHER")" "$PRISTINE_OTHER"
contains "the summary names the models" "$out" "music model: hub/models--MiniMaxAI--MiniMax-Music3"
# the total in the summary must be the models' real size (du counts each blob once, as tar stores it)
bytes=$(( $(du -sb "$MODEL" | cut -f1) + $(du -sb "$MUSIC" | cut -f1) + $(du -sbL "$UPSCALER" | cut -f1) ))
contains "and their total size" "$out" "models:  yes ($(human_of "$bytes"))"
equals "still just one file in the destination" "$(count_in "$DEST")" 1
# unpack it by hand, as someone without these scripts would, and let sha256sum check every piece
check "the checksums verify" bash -c "tar -xf '$B' -C '$T' --one-top-level=cks && cd '$T/cks' && sha256sum -c --quiet SHA256SUMS"
rm -rf "$T/cks"

# Opting out. --no-model gives exactly the old, model-free backup (format 1). --model, which older command lines and cron jobs still use,
# must keep working and change nothing.
section "--no-model leaves them out, and --model is still accepted"
fresh; add_models
out=$(bk --no-model "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
B=$(latest)
equals "no model piece" "$(tar -tf "$B" | LC_ALL=C sort | tr '\n' ' ')" "MANIFEST.txt SHA256SUMS data.tar.gz env.backup image.tar.gz "
equals "the manifest says so" "$(member MANIFEST.txt | grep '^included=')" "included=data,image,env"
equals "and it is format 1, as it always was without a model" "$(member MANIFEST.txt | grep '^format=')" "format=1"
equals "with no model_paths line" "$(member MANIFEST.txt | grep -c '^model_paths=' || true)" 0
contains "the summary says no models, and why" "$out" "models:  no (--no-model)"
out=$(bk --model "$DEST" 2>&1); rc=$?
equals "--model is accepted: exits 0" "$rc" 0
B=$(latest)
check "and it changes nothing: the models are in" bash -c "tar -tf '$B' | grep -qx model-cache.tar"

# A model that isn't in the cache is a warning, never a failure: a fresh install, or a model that was never used, must not stop every
# (scheduled) backup. The manifest must list only what really is inside.
section "a model that is not there is skipped with a warning, never an error"
fresh      # only the image model exists
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
contains "names the music model" "$out" "Leaving the music model out of the backup"
contains "names the upscaler" "$out" "Leaving the Enlarge upscaler out of the backup"
equals "but not the image model, which is there" "$(grep -c 'Leaving the image model' <<< "$out" || true)" 0
B=$(latest)
equals "the manifest lists only what is inside" "$(member MANIFEST.txt | grep '^model_paths=')" "model_paths=hub/models--Qwen--Qwen-Image-2.1"
equals "included still says model" "$(member MANIFEST.txt | grep '^included=')" "included=data,image,env,model"
# the extreme case: nothing in the cache at all. The backup still succeeds, and says plainly that it holds no model
fresh; rm -rf "$HFDIR"
out=$(bk "$DEST" 2>&1); rc=$?
equals "no model at all: exits 0" "$rc" 0
contains "says that none was found" "$out" "None of the studio's models was found in the cache"
B=$(latest)
equals "no model piece" "$(tar -tf "$B" | LC_ALL=C sort | tr '\n' ' ')" "MANIFEST.txt SHA256SUMS data.tar.gz env.backup image.tar.gz "
equals "included has no model" "$(member MANIFEST.txt | grep '^included=')" "included=data,image,env"
equals "and it is format 1" "$(member MANIFEST.txt | grep '^format=')" "format=1"
contains "the summary says why" "$out" "models:  no (none was found in the cache)"
check "the backup is still a good one" bash -c "'$REPO/scripts/restore.sh' --allow-root --verify '$B'"

# The studio's own settings decide which models are backed up. Each odd setting below must be skipped with a warning that names the
# setting, while the other models are still backed up.
section "settings that point somewhere unusual"
fresh; add_models
# STUDIO_MODEL is a folder on disk, not a Hugging Face model name: it isn't in the cache, so this backup can't hold it
printf 'STUDIO_MODEL=/models/qwen-local\n' > "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "STUDIO_MODEL as a folder on disk: exits 0" "$rc" 0
contains "warns, naming the setting" "$out" "STUDIO_MODEL is a folder on disk"
B=$(latest)
equals "the other two are still in" "$(member MANIFEST.txt | grep '^model_paths=')" "model_paths=hub/models--MiniMaxAI--MiniMax-Music3,upscalers/RealESRGAN_x2plus.pth"
fresh; add_models
# relative paths (./x, ../x) are folders on disk too, not model names
printf 'STUDIO_MODEL=./qwen-local\nSTUDIO_MUSIC_MODEL=../music-local\n' > "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "model settings that are relative folders: exits 0" "$rc" 0
contains "./folder is a folder on disk" "$out" "STUDIO_MODEL is a folder on disk"
contains "and so is ../folder" "$out" "STUDIO_MUSIC_MODEL is a folder on disk"
fresh; add_models
mkdir -p "$HFDIR/custom" && mv "$UPSCALER" "$HFDIR/custom/up2x.pth"
# a different upscaler file inside the cache is backed up INSTEAD of the default one
printf 'STUDIO_UPSCALER_MODEL=/models/custom/up2x.pth\n' > "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "another upscaler file inside the cache: exits 0" "$rc" 0
B=$(latest)
contains "it is the one that is included" "$(member MANIFEST.txt | grep '^model_paths=')" ",custom/up2x.pth"
fresh; add_models
# an upscaler outside /models is not in the cache the container sees, so it can't be included
printf 'STUDIO_UPSCALER_MODEL=/opt/elsewhere/up.pth\n' > "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "an upscaler outside /models: exits 0" "$rc" 0
contains "warns that it is not in the cache" "$out" "STUDIO_UPSCALER_MODEL is outside the model cache"
B=$(latest)
equals "and only the two models are in" "$(member MANIFEST.txt | grep '^model_paths=')" "model_paths=hub/models--Qwen--Qwen-Image-2.1,hub/models--MiniMaxAI--MiniMax-Music3"
fresh; add_models
# a model name that restore.sh would refuse (spaces, odd characters) must be caught at backup time, not discovered at restore time
printf 'STUDIO_MUSIC_MODEL=bad name/with space\n' > "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "a model name a restore would refuse: exits 0" "$rc" 0
contains "warns about the name" "$out" "has a name that a restore would refuse"
fresh; add_models
# an upscaler that is itself a symlink is stored as the real file: a restored symlink would point nowhere on another machine
mv "$UPSCALER" "$T/real-upscaler.pth"; ln -s "$T/real-upscaler.pth" "$UPSCALER"
out=$(bk "$DEST" 2>&1); rc=$?
B=$(latest)
equals "an upscaler file that is itself a link: exits 0" "$rc" 0
equals "it is stored as the file, not as a link that would point nowhere" "$(member model-cache.tar | tar -tvf - upscalers/RealESRGAN_x2plus.pth | cut -c1-1)" "-"
equals "with the file's own bytes" "$(member model-cache.tar | tar -xOf - upscalers/RealESRGAN_x2plus.pth | sha256sum | cut -d' ' -f1)" "$(sha256sum "$T/real-upscaler.pth" | cut -d' ' -f1)"
rm -f "$T/real-upscaler.pth"

section "a studio that was already stopped stays stopped"
fresh; echo false > "$STUB_STATE/running"
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "never started it" bash -c "! grep -q 'compose start' '$STUB_STATE/calls.log'"
equals "still stopped" "$(cat "$STUB_STATE/running")" false

section "a failure part-way restarts the studio and leaves no mess"
fresh; export STUB_FAIL_SAVE=1
out=$(bk "$DEST" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
equals "studio is running again" "$(cat "$STUB_STATE/running")" true
equals "nothing left in the destination: no .tar, no staging folder" "$(count_in "$DEST")" 0
contains "says the backup did not finish, and that nothing was kept" "$out" "did not finish. Whatever had been written so far was removed"
fresh; export STUB_FAIL_STOP=1
out=$(bk "$DEST" 2>&1); rc=$?
check "failing to stop it exits non-zero" test "$rc" -ne 0
equals "nothing left behind" "$(count_in "$DEST")" 0

section "a failure while the studio is stopped brings it back"
fresh; export STUB_FAIL_TAR=1
out=$(bk "$DEST" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
check "the studio was stopped, then started again" bash -c "grep -q 'compose stop' '$STUB_STATE/calls.log' && grep -q 'compose start' '$STUB_STATE/calls.log'"
equals "studio is running again" "$(cat "$STUB_STATE/running")" true
contains "says it is starting it again" "$out" "Starting the studio again"
equals "nothing left in the destination" "$(count_in "$DEST")" 0
unset STUB_FAIL_TAR

section "a .tar that is damaged as it is written is caught, and never kept"
fresh; export STUB_CORRUPT_TAR=1
out=$(bk "$DEST" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
contains "says the checksum check failed" "$out" "failed its own checksum check"
equals "nothing kept: no .tar and no staging folder" "$(count_in "$DEST")" 0
equals "studio is running" "$(cat "$STUB_STATE/running")" true
out=$(bk --no-verify "$DEST" 2>&1); rc=$?
equals "--no-verify skips that check (so the damage is not noticed)" "$rc" 0
B=$(latest)
out=$(rs --verify "$B" 2>&1); rc=$?
equals "but 'restore.sh --verify' notices it later" "$rc" 1
contains "and names the damaged piece" "$out" "data.tar.gz is damaged"
unset STUB_CORRUPT_TAR

section "a busy studio is not interrupted"
fresh
echo '{"queue":{"running":"abc","queued":2,"cap":10}}' > "$STUB_STATE/status.json"
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 3" "$rc" 3
contains "says what is going on" "$out" "an image is being generated and 2 job(s) are waiting"
check "did not stop it" bash -c "! grep -q 'compose stop' '$STUB_STATE/calls.log'"
out=$(bk --interrupt "$DEST" 2>&1); rc=$?
equals "--interrupt goes ahead" "$rc" 0
echo '{"queue":{"running":null,"queued":0,"cap":10}}' > "$STUB_STATE/status.json"
out=$(bk "$DEST" 2>&1); rc=$?
equals "an idle studio is backed up normally (even straight after another backup)" "$rc" 0
equals "both backups exist, with different names" "$(ls "$DEST"/ai-image-studio-backup-*.tar | wc -l | tr -d ' ')" 2

section "problems found before anything is touched"
fresh; rm -rf "$REPO/data"
out=$(bk "$DEST" 2>&1); rc=$?
equals "no data folder: exits 1" "$rc" 1
contains "explains" "$out" "nothing to back up"
fresh; rm -f "$STUB_STATE/image_id"
out=$(bk "$DEST" 2>&1); rc=$?
equals "no image: exits 1" "$rc" 1
contains "suggests --no-image" "$out" "--no-image"
out=$(bk --no-image "$DEST" 2>&1); rc=$?
equals "--no-image works" "$rc" 0
check "and leaves the image out" bash -c "! tar -tf '$(latest)' | grep -q image.tar.gz"
fresh; rm -f "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "no .env is only a warning" "$rc" 0
contains "warned" "$out" "no .env file"
fresh; export STUB_DF_LOW=1
out=$(bk "$DEST" 2>&1); rc=$?
equals "too little space and no one to ask: exits 3" "$rc" 3
contains "reports the shortage" "$out" "is free"
equals "nothing was stopped" "$(grep -c 'compose stop' "$STUB_STATE/calls.log" || true)" 0
out=$(bk --yes "$DEST" 2>&1); rc=$?
equals "--yes goes ahead anyway" "$rc" 0

section "relative destination, .env with quotes and comments"
fresh
printf 'HF_CACHE_DIR="%s" # my cache\nSTUDIO_MODEL=Qwen/Qwen-Image-2.1\n' "$T/other cache" > "$REPO/.env"
mkdir -p "$T/other cache/hub" && mv "$MODEL" "$T/other cache/hub/"
(cd "$T" && "$REPO/scripts/backup.sh" --allow-root "rel dest" >/dev/null 2>&1); rc=$?
equals "relative destination, custom cache folder: exits 0" "$rc" 0
B=$(ls "$T/rel dest"/ai-image-studio-backup-*.tar | head -n 1)
check "the models were found in the custom cache folder" bash -c "tar -tf '$B' | grep -qx model-cache.tar"
rm -rf "$T/rel dest" "$T/other cache"

section "--keep prunes old backups, and only ours"
fresh
for _ in 1 2 3 4; do bk --no-image "$DEST" >/dev/null 2>&1; sleep 1; done
echo "not a backup" > "$DEST/ai-image-studio-backup-20200101-000000.tar"     # right name, but junk
mkdir "$T/decoy" && echo hi > "$T/decoy/file" && tar -cf "$DEST/ai-image-studio-backup-20200102-000000.tar" -C "$T/decoy" file   # a tar, but not ours
echo keep > "$DEST/ai-image-studio-backup-notatimestamp.tar"                  # wrong name
out=$(bk --no-image --keep 2 "$DEST" 2>&1); rc=$?
equals "--keep exits 0" "$rc" 0
real=0; for f in "$DEST"/*.tar; do
  [[ $(basename "$f") =~ ^ai-image-studio-backup-[0-9]{8}-[0-9]{6}\.tar$ ]] || continue
  tar -tf "$f" MANIFEST.txt >/dev/null 2>&1 && real=$((real + 1)); done
equals "only the newest 2 real backups remain" "$real" 2
check "a junk file with a backup's name is left alone" test -f "$DEST/ai-image-studio-backup-20200101-000000.tar"
check "a tar that isn't ours is left alone" test -f "$DEST/ai-image-studio-backup-20200102-000000.tar"
check "a file with another name is left alone" test -f "$DEST/ai-image-studio-backup-notatimestamp.tar"
contains "said so" "$out" "doesn't look like one of ours"

# ================================================================== verify (no restoring)
section "restore.sh --verify checks a .tar without restoring, and without Docker"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
: > "$STUB_STATE/calls.log"
# a PATH with the basic tools and no docker at all (a real docker binary may exist on this machine)
MINBIN="$T/minbin"; mkdir -p "$MINBIN"
for tool in bash env dirname basename cat grep sed cut tr head tail sort wc date mktemp rm ls stat tar gzip gunzip sha256sum awk id hostname; do
  p=$(PATH=/usr/bin:/bin command -v "$tool" 2>/dev/null) && ln -sf "$p" "$MINBIN/$tool"
done
check "(precondition) docker really is not on the PATH used below" env PATH="$MINBIN" bash -c '! command -v docker'
out=$(env PATH="$MINBIN" "$REPO/scripts/restore.sh" --allow-root --verify "$B" 2>&1); rc=$?
equals "an intact backup: exits 0, even with no docker on the PATH" "$rc" 0
contains "says it is intact" "$out" "This backup is intact"
contains "shows what it holds" "$out" "holds:  data,image,env,model"
contains "and which models" "$out" "models: hub/models--Qwen--Qwen-Image-2.1"
equals "it did not touch Docker" "$(calls)" ""
equals "or the data" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"
# a copy somewhere else (a NAS, say) is still verifiable
mkdir -p "$T/nas share" && cp "$B" "$T/nas share/"
out=$(rs --verify "$T/nas share/$(basename "$B")" 2>&1); rc=$?
equals "a copy in another folder verifies too" "$rc" 0
# damage inside the data piece
cp "$B" "$T/damaged.tar"; printf 'X' | dd of="$T/damaged.tar" bs=1 seek=100000 conv=notrunc 2>/dev/null
out=$(rs --verify "$T/damaged.tar" 2>&1); rc=$?
equals "a damaged copy: exits 1" "$rc" 1
contains "names the damaged piece" "$out" "data.tar.gz is damaged"
# truncated (a copy that stopped half way)
head -c 60000 "$B" > "$T/truncated.tar"
out=$(rs --verify "$T/truncated.tar" 2>&1); rc=$?
equals "a truncated copy: exits 1" "$rc" 1
rm -f "$T/damaged.tar" "$T/truncated.tar"
out=$(rs --verify "$T/not-there.tar" 2>&1); rc=$?
equals "a file that doesn't exist: exits 1" "$rc" 1
echo "just text" > "$T/notatar.tar"
out=$(rs --verify "$T/notatar.tar" 2>&1); rc=$?
equals "a file that isn't a tar: exits 1" "$rc" 1

section "restore refuses a .tar that isn't what backup.sh makes"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest); unpack "$B" "$T/u"
echo 'echo hi' > "$T/u/evil.sh"; tar -cf "$T/extra.tar" -C "$T/u" MANIFEST.txt SHA256SUMS data.tar.gz evil.sh
out=$(rs --verify "$T/extra.tar" 2>&1); rc=$?
equals "an unknown piece: exits 1" "$rc" 1
contains "says so" "$out" "never contains"
tar -cf "$T/dup.tar" -C "$T/u" MANIFEST.txt SHA256SUMS data.tar.gz && tar -rf "$T/dup.tar" -C "$T/u" data.tar.gz
out=$(rs --verify "$T/dup.tar" 2>&1); rc=$?
equals "a piece that appears twice: exits 1" "$rc" 1
contains "says so" "$out" "twice"
tar -cf "$T/nosums.tar" -C "$T/u" MANIFEST.txt data.tar.gz
out=$(rs --verify "$T/nosums.tar" 2>&1); rc=$?
equals "no SHA256SUMS: exits 1" "$rc" 1
contains "says it isn't a backup" "$out" "doesn't look like a backup"
grep -v '^.*env.backup' "$T/u/SHA256SUMS" > "$T/u/SHA256SUMS.new" && mv "$T/u/SHA256SUMS.new" "$T/u/SHA256SUMS"
tar -cf "$T/unlisted.tar" -C "$T/u" MANIFEST.txt SHA256SUMS data.tar.gz env.backup image.tar.gz
out=$(rs --verify "$T/unlisted.tar" 2>&1); rc=$?
equals "a piece with no checksum: exits 1" "$rc" 1
contains "says so" "$out" "has no checksum"
unpack "$B" "$T/u"; rm -f "$T/u/image.tar.gz"
tar -cf "$T/missing.tar" -C "$T/u" MANIFEST.txt SHA256SUMS data.tar.gz env.backup
out=$(rs --verify "$T/missing.tar" 2>&1); rc=$?
equals "a listed piece that is missing: exits 1" "$rc" 1
contains "says so" "$out" "missing from the backup"
# a .tar repacked by another tool, with ./ in front of every name, is fine
unpack "$B" "$T/u"; tar -cf "$T/dotted.tar" -C "$T/u" .
out=$(rs --verify "$T/dotted.tar" 2>&1); rc=$?
equals "a .tar repacked with ./ names verifies" "$rc" 0
# and an unpacked folder works too
out=$(rs --verify "$T/u" 2>&1); rc=$?
equals "so does the unpacked folder" "$rc" 0
rm -rf "$T/u" "$T"/*.tar

# ================================================================== restore
section "restore: refusing and checking"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
out=$(rs 2>&1); rc=$?
equals "no argument exits 2" "$rc" 2
out=$(rs "$T/nowhere.tar" 2>&1); rc=$?
equals "a path that doesn't exist exits 1" "$rc" 1
mkdir "$T/empty"; out=$(rs "$T/empty" 2>&1); rc=$?
equals "a folder that isn't a backup exits 1" "$rc" 1
contains "explains" "$out" "doesn't look like a backup"
out=$(rs "$B" 2>&1); rc=$?
equals "existing data without --force: exits 3" "$rc" 3
contains "explains" "$out" "already has files"
equals "and the data was not touched" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"

section "restore: dry run"
: > "$STUB_STATE/calls.log"
out=$(rs --dry-run --force "$B" 2>&1); rc=$?
equals "dry run exits 0" "$rc" 0
contains "dry run says so" "$out" "Dry run"
equals "data untouched by a dry run" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"
check "no stop call" bash -c "! grep -q 'compose stop' '$STUB_STATE/calls.log'"

section "restore: a damaged backup is refused before anything changes"
cp "$B" "$T/tampered.tar"
printf 'X' | dd of="$T/tampered.tar" bs=1 seek=100000 conv=notrunc 2>/dev/null
rm -rf "$REPO/data"
out=$(rs "$T/tampered.tar" 2>&1); rc=$?
equals "damaged backup: exits 1" "$rc" 1
contains "says it's damaged" "$out" "damaged"
check "nothing was restored" test ! -e "$REPO/data"
rm -f "$T/tampered.tar"

section "restore: the full round trip, on a 'new machine', from a copy on a 'NAS'"
mkdir -p "$T/nas share"; cp "$B" "$T/nas share/"; NASB="$T/nas share/$(basename "$B")"
rm -rf "$REPO/data" "$MODEL"; rm -f "$REPO/.env" "$STUB_STATE/image_id"; echo false > "$STUB_STATE/running"; : > "$STUB_STATE/calls.log"
out=$(rs "$NASB" 2>&1); rc=$?
equals "exits 0" "$rc" 0
equals "data identical to the original" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"
equals "SQLite database passes its integrity check" "$(python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('SELECT count(*) FROM runs').fetchone()[0])" "$REPO/data/studio.sqlite")" "ok 50"
equals ".env identical" "$(sha256sum "$REPO/.env" | cut -d' ' -f1)" "$PRISTINE_ENV"
equals ".env is private (600)" "$(stat -c %a "$REPO/.env")" 600
equals "model cache identical" "$(tree_hash "$MODEL")" "$PRISTINE_MODEL"
equals "model symlinks restored as symlinks" "$(readlink "$MODEL/snapshots/rev1/transformer/model.safetensors")" "../../../blobs/abc123"
equals "image loaded, with the original id" "$(cat "$STUB_STATE/image_id")" "sha256:1111aaaa"
contains "docker load was called" "$(calls)" "docker load"
check "not started: it wasn't running before" bash -c "! grep -q 'compose up' '$STUB_STATE/calls.log'"
contains "tells you how to start it" "$out" "docker compose up -d --no-build"
check "no temp folder left behind" bash -c "! ls -A '$REPO' | grep -q '^\.restore-tmp'"
check "the .tar itself was only read, never changed" test "$(sha256sum "$NASB" | cut -d' ' -f1)" = "$(sha256sum "$B" | cut -d' ' -f1)"

section "restore: from an unpacked folder"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest); unpack "$B" "$T/unpacked"
rm -rf "$REPO/data"
out=$(rs "$T/unpacked" 2>&1); rc=$?
equals "exits 0" "$rc" 0
equals "data identical" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"
rm -rf "$T/unpacked"

section "restore: --start, and a studio that was running"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
: > "$STUB_STATE/calls.log"
out=$(rs --force "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "stopped it first" bash -c "grep -q 'compose stop' '$STUB_STATE/calls.log'"
check "brought it back with up --no-build (so it uses the loaded image)" bash -c "grep -q 'compose up -d --no-build' '$STUB_STATE/calls.log'"
check "same image already here: not loaded again" bash -c "! grep -q 'docker load' '$STUB_STATE/calls.log'"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest); echo false > "$STUB_STATE/running"; : > "$STUB_STATE/calls.log"
out=$(rs --force --start "$B" 2>&1); rc=$?
check "--start starts a stopped studio" bash -c "grep -q 'compose up -d --no-build' '$STUB_STATE/calls.log'"

section "restore: --force moves things aside and never deletes"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
echo "newer history" > "$REPO/data/newer.txt"
printf 'HF_TOKEN=different\n' > "$REPO/.env"
echo "extra" > "$MODEL/extra.txt"
out=$(rs "$B" --force 2>&1); rc=$?
equals "exits 0" "$rc" 0
aside=$(ls -d "$REPO"/data.before-restore-* 2>/dev/null | head -n 1)
check "the old data folder was kept" test -f "$aside/newer.txt"
equals "data is the backed-up data" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"
check "the old .env was kept" test -f "$(ls "$REPO"/.env.before-restore-* | head -n 1)"
equals ".env is the backed-up one" "$(sha256sum "$REPO/.env" | cut -d' ' -f1)" "$PRISTINE_ENV"
check "the old model folder was kept" test -f "$(ls -d "$MODEL".before-restore-* | head -n 1)/extra.txt"
equals "the model is the backed-up one" "$(tree_hash "$MODEL")" "$PRISTINE_MODEL"

section "restore: a different .env and an existing model without --force"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data"; printf 'HF_TOKEN=different\n' > "$REPO/.env"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
contains "your .env is kept" "$out" "Keeping your current .env"
equals ".env unchanged" "$(cat "$REPO/.env")" "HF_TOKEN=different"
contains "the existing model is kept" "$out" "already in the cache"
equals "and with every model already there, there is no model step at all" "$(grep -c 'Restoring the models' <<< "$out" || true)" 0
equals "data was restored" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"

section "restore: another image is already tagged"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data"; echo "sha256:9999ffff" > "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
contains "the current image was tagged before it was replaced" "$(calls)" "docker tag ai-image-studio:local ai-image-studio:before-restore-"
equals "the backed-up image is now ai-image-studio:local" "$(cat "$STUB_STATE/image_id")" "sha256:1111aaaa"

section "restore: --no-* flags"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data" "$MODEL"; rm -f "$REPO/.env" "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
out=$(rs --no-image --no-env --no-model "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "data restored" test -d "$REPO/data"
check "no .env" test ! -e "$REPO/.env"
check "no model" test ! -e "$MODEL"
check "image not loaded" bash -c "! grep -q 'docker load' '$STUB_STATE/calls.log'"

# Round trip. Back up all three models, wipe the machine, restore: every model must come back byte for byte, symlinks included, and
# the other tool's model (never in the backup) must not appear.
section "restore: all the models, on a 'new machine'"
fresh; add_models; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data" "$HFDIR"; rm -f "$REPO/.env"; echo false > "$STUB_STATE/running"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
equals "the image model is identical" "$(tree_hash "$MODEL")" "$PRISTINE_MODEL"
equals "the music model is identical" "$(tree_hash "$MUSIC")" "$PRISTINE_MUSIC"
equals "the upscaler file is identical" "$(sha256sum "$UPSCALER" | cut -d' ' -f1)" "$PRISTINE_UP"
check "another tool's model is not there: it was never in the backup" test ! -e "$OTHER"
equals "the music model's symlinks are symlinks" "$(readlink "$MUSIC/snapshots/rev9/audio/model.safetensors")" "../../../blobs/def456"
contains "it said which models it was restoring" "$out" "restore the model $HFDIR/hub/models--MiniMaxAI--MiniMax-Music3"
check "no temp folder left behind" bash -c "! ls -A '$REPO' | grep -q '^\.restore-tmp'"

# Restore decides model by model. One already in the cache is left alone (the extra file in it must survive), the missing one is
# restored, and only the missing one is even unpacked: the tar stub's log shows which names restore.sh asked tar to extract.
section "restore: a model already in the cache is left alone, the others are restored"
fresh; add_models; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data" "$MUSIC"; echo mine > "$MODEL/extra.txt"
export STUB_LOG_TAR="$T/tar.log"; : > "$STUB_LOG_TAR"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
extract=$(grep -- '-xf - -C .*/model -- ' "$STUB_LOG_TAR" || true); unset STUB_LOG_TAR
equals "only one extraction of models was made" "$(grep -c . <<< "$extract")" 1
contains "and it was only for the music model" "$extract" "-- hub/models--MiniMaxAI--MiniMax-Music3"
equals "not the image model or the upscaler, which are already there" "$(grep -c 'Qwen\|upscalers' <<< "$extract" || true)" 0
contains "says the image model is already there" "$out" "hub/models--Qwen--Qwen-Image-2.1 is already in the cache"
contains "and the upscaler" "$out" "upscalers/RealESRGAN_x2plus.pth is already in the cache"
check "the image model was not touched" test -f "$MODEL/extra.txt"
equals "the music model, which was missing, was restored" "$(tree_hash "$MUSIC")" "$PRISTINE_MUSIC"
equals "the upscaler file is as it was" "$(sha256sum "$UPSCALER" | cut -d' ' -f1)" "$PRISTINE_UP"
equals "nothing was moved aside" "$(find "$HFDIR" -name '*before-restore*' | wc -l | tr -d ' ')" 0

# With --force a model in the way is moved aside, never deleted: each old copy must still exist, with its old contents, next to the
# restored one. This is the rule that makes a restore safe to try.
section "restore --force: each model in the way is moved aside, never deleted"
fresh; add_models; bk "$DEST" >/dev/null 2>&1; B=$(latest)
echo mine > "$MODEL/extra.txt"; echo mine > "$MUSIC/extra.txt"; echo changed > "$UPSCALER"
out=$(rs --force "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "the image model's old copy is kept" test -f "$(ls -d "$MODEL".before-restore-* | head -n 1)/extra.txt"
check "the music model's old copy is kept" test -f "$(ls -d "$MUSIC".before-restore-* | head -n 1)/extra.txt"
equals "the upscaler's old file is kept" "$(cat "$(ls "$UPSCALER".before-restore-* | head -n 1)")" "changed"
equals "the image model is the backed-up one" "$(tree_hash "$MODEL")" "$PRISTINE_MODEL"
equals "the music model is the backed-up one" "$(tree_hash "$MUSIC")" "$PRISTINE_MUSIC"
equals "the upscaler file is the backed-up one" "$(sha256sum "$UPSCALER" | cut -d' ' -f1)" "$PRISTINE_UP"
contains "the plan said the old copies are moved aside" "$out" "the current copy is moved aside first"

# --no-model on restore: the data comes back, no model does, and the cache folder isn't even created
section "restore: --no-model restores none of them"
fresh; add_models; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data" "$HFDIR"
out=$(rs --no-model "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "no cache folder was made" test ! -e "$HFDIR"
check "data was restored" test -d "$REPO/data"

# Backups made before this change (format 1, one model named by model_dir) exist in the wild: restore must keep reading them.
# The second half checks that a hostile model_dir in such a backup is refused too.
section "restore: a backup made by the older script (one model, format 1) still restores"
fresh; mk_legacy
rm -rf "$REPO/data" "$HFDIR"
out=$(rs "$T/legacy.tar" 2>&1); rc=$?
equals "exits 0" "$rc" 0
equals "its model is restored" "$(tree_hash "$MODEL")" "$PRISTINE_MODEL"
check "and nothing else appears in the cache" test ! -e "$MUSIC" -a ! -e "$HFDIR/upscalers"
mk_legacy "../../evil"
rm -rf "$REPO/data" "$HFDIR"
out=$(rs "$T/legacy.tar" 2>&1); rc=$?
equals "(an older-format backup whose model folder climbs out of the cache) exits 1" "$rc" 1
contains "says it doesn't trust it" "$out" "model folder I don't trust"
check "and restored nothing" test ! -e "$REPO/data" -a ! -e "$HFDIR"
rm -rf "$T/legacy" "$T/legacy.tar"

# Hostile or damaged backups. Restore must refuse, BEFORE changing anything, any manifest path or archive entry that could write
# outside the model folders the manifest declares. The control case first shows that the same kind of hand-made backup restores fine
# when it is well-formed, so the refusals after it are for the reason they claim.
section "restore: a manifest or archive that reaches outside what it lists is refused"
fresh
mk_model_backup ok 2 "hub/models--A,upscalers/up.pth" "hub/models--A/file" "upscalers/up.pth"
rm -rf "$REPO/data" "$HFDIR"
out=$(rs "$T/mm-ok.tar" 2>&1); rc=$?
equals "(control) a well-formed hand-made backup restores" "$rc" 0
check "its model folder arrived" test -f "$HFDIR/hub/models--A/file"
check "and its upscaler file" test -f "$HFDIR/upscalers/up.pth"
# each call below builds a hand-made hostile backup, runs restore on it, and checks three things: exit code 1, the stated reason, and that nothing was created
refused() {   # refused NAME DESCRIPTION WANTED_TEXT FORMAT PATHS ENTRY...: restore must exit 1, say WANTED_TEXT, and touch nothing
  local name=$1 desc=$2 want=$3 format=$4 paths=$5; shift 5
  mk_model_backup "$name" "$format" "$paths" "$@"
  rm -rf "$REPO/data" "$HFDIR"
  out=$(rs "$T/mm-$name.tar" 2>&1); rc=$?
  equals "$desc: exits 1" "$rc" 1
  contains "$desc: says why" "$out" "$want"
  check "$desc: nothing was restored" test ! -e "$REPO/data" -a ! -e "$HFDIR" -a ! -e "$T/evil.txt"
}
refused b1 "an entry outside the listed paths" "outside the paths the backup lists" 2 "hub/models--A" "hub/models--A/file" "evil.txt"
refused b2 "a path that climbs out of the cache" "model path I don't trust" 2 "../../evil/x" "x"
refused b3 "the whole hub folder named as a model" "model path I don't trust" 2 "hub" "hub/x"
refused b4 "an absolute path" "model path I don't trust" 2 "/abs/dir/file" "x"
refused b5 "an entry with a '..' inside a listed path" "containing '..'" 2 "hub/models--A" "hub/models--A/../../evil"
refused b6 "an empty path in the list" "model path I don't trust" 2 "hub/models--A,,upscalers/up.pth" "hub/models--A/file"
refused b7 "a format this version doesn't know" "format (3)" 3 "hub/models--A" "hub/models--A/file"
refused b8 "a folder in hub/ that is not a model" "model path I don't trust" 2 "hub/some-other-tool" "hub/some-other-tool/file"
refused b9 "an entry that only starts like a listed path" "outside the paths the backup lists" 2 "hub/models--A" "hub/models--A/file" "hub/models--Abc/file"

section "restore: a different code version"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
(cd "$REPO" && echo x > newfile && git add -A && git -c user.email=t@t -c user.name=t commit -q -m later)
out=$(rs --force "$B" 2>&1); rc=$?
contains "warns the code is at another commit" "$out" "was made at"
contains "says how to match it" "$out" "git checkout"

section "restore: archives that try to escape are refused"
fresh; rm -rf "$REPO/data"
mk_backup evil1 "data/ok.txt" "../evil.txt"
out=$(rs "$T/mk-evil1.tar" 2>&1); rc=$?
equals "a '..' entry: exits 1" "$rc" 1
contains "explained" "$out" "outside 'data/'"
check "nothing was written outside" test ! -e "$REPO/evil.txt" -a ! -e "$T/evil.txt"
check "no data folder was created" test ! -e "$REPO/data"
mk_backup evil2 "data/ok.txt" "other/file.txt"
out=$(rs "$T/mk-evil2.tar" 2>&1); rc=$?
equals "an entry outside data/: exits 1" "$rc" 1
mk_backup evil3 "/tmp/stub-absolute-path-test.txt"
out=$(rs "$T/mk-evil3.tar" 2>&1); rc=$?
equals "an absolute path: exits 1" "$rc" 1
check "the absolute path was not written" test ! -e /tmp/stub-absolute-path-test.txt
mk_backup evil4 "data/../../evil.txt"
out=$(rs "$T/mk-evil4.tar" 2>&1); rc=$?
equals "a path that starts in data/ but climbs out: exits 1" "$rc" 1
check "nothing was written outside" test ! -e "$T/evil.txt" -a ! -e "$REPO/../evil.txt"
mk_backup fine "data/ok.txt"
out=$(rs "$T/mk-fine.tar" 2>&1); rc=$?
equals "(control) a well-formed hand-made backup restores" "$rc" 0
check "and its file arrived" test -f "$REPO/data/ok.txt"

section "restore: a failure leaves the studio stopped and says so"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
echo "sha256:other" > "$STUB_STATE/image_id"
unpack "$B" "$T/u"; printf 'not gzip data' > "$T/u/image.tar.gz"
tar -cf "$T/brokenimage.tar" -C "$T/u" MANIFEST.txt SHA256SUMS data.tar.gz env.backup image.tar.gz
rm -rf "$REPO/data"
out=$(rs --no-verify "$T/brokenimage.tar" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
contains "says the restore didn't finish" "$out" "did not finish"
equals "studio left stopped" "$(cat "$STUB_STATE/running")" false
check "no temp folder left behind" bash -c "! ls -A '$REPO' | grep -q '^\.restore-tmp'"
out=$(rs "$T/brokenimage.tar" 2>&1); rc=$?
equals "(and with checksums on, the same file is refused up front)" "$rc" 1

# ================================================================== result
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
