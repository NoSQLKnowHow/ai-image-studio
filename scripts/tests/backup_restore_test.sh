#!/usr/bin/env bash
# Tests for scripts/backup.sh and scripts/restore.sh:   bash scripts/tests/backup_restore_test.sh
#
# Everything runs in a throw-away folder. `docker` is replaced by a stand-in that records every
# call and pretends to be a container, an image and `docker save` / `docker load`; tar, gzip, the
# checksums and the SQLite database are real. So this proves the scripts' logic (order of steps,
# restarts after failures, refusals, integrity checks, round trips). It does not prove how real
# Docker behaves: do a first real backup with --dry-run, then without, on the Spark.
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
cat > "$T/bin/tar" <<'STUB'
#!/usr/bin/env bash
if [[ -n ${STUB_FAIL_TAR:-} && $* == *data.tar.gz* && $1 == -czf ]]; then echo "tar: stub failure" >&2; exit 2; fi
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
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [[ -n ${2:-} ]] && printf '        %s\n' "$2"; }
check() { local desc=$1; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }
contains() { local desc=$1 text=$2 needle=$3
  if [[ $text == *"$needle"* ]]; then ok "$desc"; else bad "$desc" "wanted: $needle | got: ${text:0:300}"; fi; }
equals() { local desc=$1 got=$2 want=$3
  if [[ $got == "$want" ]]; then ok "$desc"; else bad "$desc" "wanted: $want | got: $got"; fi; }
section() { printf '\n%s\n' "$1"; }
calls() { cat "$STUB_STATE/calls.log" 2>/dev/null || true; }
line_of() { calls | grep -n -- "$1" | head -n 1 | cut -d: -f1; }
tree_hash() { (cd "$1" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1); }

REPO="$T/my repo"              # a space in the path, on purpose
DEST="$T/backups dir"
HFDIR="$HOME/.cache/huggingface"
MODEL="$HFDIR/hub/models--Qwen--Qwen-Image-2.1"

bk() { "$REPO/scripts/backup.sh" --allow-root "$@"; }
rs() { "$REPO/scripts/restore.sh" --allow-root "$@"; }
latest() { ls -d "$DEST"/ai-image-studio-backup-* 2>/dev/null | sort | tail -n 1; }

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
reset_stub() {   # container exists and runs; the image exists
  rm -rf "$STUB_STATE"; mkdir -p "$STUB_STATE"
  : > "$STUB_STATE/container"; echo true > "$STUB_STATE/running"
  echo "sha256:1111aaaa" > "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
  unset STUB_FAIL_STOP STUB_FAIL_SAVE STUB_FAIL_TAR STUB_DF_LOW
}
fresh() { make_pristine; reset_stub; }

# ================================================================== backup
section "usage and options"
fresh
out=$(bk --help 2>&1); rc=$?
equals "--help exits 0" "$rc" 0
contains "--help lists --model" "$out" "--model"
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
out=$(bk --dry-run --model "$DEST" 2>&1); rc=$?
equals "dry run exits 0" "$rc" 0
contains "dry run says so" "$out" "Dry run"
equals "dry run creates nothing in the destination" "$(ls -A "$DEST" | wc -l | tr -d ' ')" 0
check "dry run never stops the studio" bash -c "! grep -q 'compose stop' '$STUB_STATE/calls.log'"
equals "studio still running" "$(cat "$STUB_STATE/running")" true

section "a normal backup of a running studio"
fresh
out=$(bk "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
B=$(latest)
check "backup folder exists" test -d "$B"
for f in data.tar.gz image.tar.gz env.backup MANIFEST.txt SHA256SUMS; do check "has $f" test -f "$B/$f"; done
check "no model unless asked" test ! -e "$B/model-cache.tar"
equals ".env copy is private (600)" "$(stat -c %a "$B/env.backup")" 600
check "checksums verify" bash -c "cd '$B' && sha256sum -c --quiet SHA256SUMS"
check "data archive is a valid gzip" gzip -t "$B/data.tar.gz"
contains "manifest records the image id" "$(cat "$B/MANIFEST.txt")" "image_id=sha256:1111aaaa"
contains "manifest records the commit" "$(cat "$B/MANIFEST.txt")" "git_commit=$(git -C "$REPO" rev-parse HEAD)"
contains "manifest records what's included" "$(cat "$B/MANIFEST.txt")" "included=data,image,env"
mkdir -p "$T/x" && tar -xzf "$B/data.tar.gz" -C "$T/x"
equals "archived data is identical to the live data" "$(tree_hash "$T/x/data")" "$PRISTINE_DATA"
a=$(line_of "compose stop"); b=$(line_of "compose start"); c=$(line_of "docker save")
check "order: stop, then start, then save the image" test "$a" -lt "$b" -a "$b" -lt "$c"
equals "studio is running again afterwards" "$(cat "$STUB_STATE/running")" true
check "no half-written folders left" bash -c "! ls -A '$DEST' | grep -q '^\.partial'"
check "the live data was not modified" test "$(tree_hash "$REPO/data")" = "$PRISTINE_DATA"

section "--model and symlinks"
fresh
out=$(bk --model "$DEST" 2>&1); rc=$?
equals "exits 0" "$rc" 0
B=$(latest)
check "model-cache.tar exists" test -f "$B/model-cache.tar"
contains "manifest lists model" "$(cat "$B/MANIFEST.txt")" "included=data,image,env,model"
contains "symlinks are kept as symlinks" "$(tar -tvf "$B/model-cache.tar")" "model.safetensors -> ../../../blobs/abc123"
rm -rf "$MODEL"
out=$(bk --model "$DEST" 2>&1); rc=$?
equals "model missing from the cache: exits 1" "$rc" 1
contains "tells you what to do" "$out" "Generate an image once"

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
equals "no backup folder and no partial folder left" "$(ls -A "$DEST" | wc -l | tr -d ' ')" 0
contains "says the half-written folder was removed" "$out" "half-written folder was removed"
fresh; export STUB_FAIL_STOP=1
out=$(bk "$DEST" 2>&1); rc=$?
check "failing to stop it exits non-zero" test "$rc" -ne 0
equals "nothing left behind" "$(ls -A "$DEST" | wc -l | tr -d ' ')" 0

section "a failure while the studio is stopped brings it back"
fresh; export STUB_FAIL_TAR=1
out=$(bk "$DEST" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
check "the studio was stopped, then started again" bash -c "grep -q 'compose stop' '$STUB_STATE/calls.log' && grep -q 'compose start' '$STUB_STATE/calls.log'"
equals "studio is running again" "$(cat "$STUB_STATE/running")" true
contains "says it is starting it again" "$out" "Starting the studio again"
equals "nothing left in the destination" "$(ls -A "$DEST" | wc -l | tr -d ' ')" 0
unset STUB_FAIL_TAR

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
equals "both backups exist, with different names" "$(ls -d "$DEST"/ai-image-studio-backup-* | wc -l | tr -d ' ')" 2

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
check "and leaves the image out" test ! -e "$(latest)/image.tar.gz"
fresh; rm -f "$REPO/.env"
out=$(bk "$DEST" 2>&1); rc=$?
equals "no .env is only a warning" "$rc" 0
contains "warned" "$out" "no .env file"
fresh; export STUB_DF_LOW=1
out=$(bk "$DEST" 2>&1); rc=$?
equals "too little space and no one to ask: exits 3" "$rc" 3
contains "reports the shortage" "$out" "is free at the destination"
equals "nothing was stopped" "$(grep -c 'compose stop' "$STUB_STATE/calls.log" || true)" 0
out=$(bk --yes "$DEST" 2>&1); rc=$?
equals "--yes goes ahead anyway" "$rc" 0

section "relative destination, .env with quotes and comments, --keep"
fresh
printf 'HF_CACHE_DIR="%s" # my cache\nSTUDIO_MODEL=Qwen/Qwen-Image-2.1\n' "$T/other cache" > "$REPO/.env"
mkdir -p "$T/other cache/hub" && mv "$MODEL" "$T/other cache/hub/"
(cd "$T" && "$REPO/scripts/backup.sh" --allow-root --model "rel dest" >/dev/null 2>&1); rc=$?
equals "relative destination, custom cache folder: exits 0" "$rc" 0
check "the backup landed in the relative folder" test -f "$(ls -d "$T/rel dest"/ai-image-studio-backup-* | head -n 1)/model-cache.tar"
rm -rf "$T/rel dest" "$T/other cache"
fresh
for _ in 1 2 3 4; do bk --no-image "$DEST" >/dev/null 2>&1; sleep 1; done
mkdir "$DEST/ai-image-studio-backup-20200101-000000"        # right name, but not ours (no MANIFEST)
mkdir "$DEST/ai-image-studio-backup-notatimestamp"           # wrong name
out=$(bk --no-image --keep 2 "$DEST" 2>&1); rc=$?
equals "--keep exits 0" "$rc" 0
equals "only the newest 2 real backups remain" "$(for d in "$DEST"/ai-image-studio-backup-*; do [[ -f $d/MANIFEST.txt ]] && echo "$d"; done | wc -l | tr -d ' ')" 2
check "a folder without a MANIFEST is left alone" test -d "$DEST/ai-image-studio-backup-20200101-000000"
check "a folder with another name is left alone" test -d "$DEST/ai-image-studio-backup-notatimestamp"
contains "said so" "$out" "isn't one of ours"

# ================================================================== restore
section "restore: refusing and checking"
fresh; bk --model "$DEST" >/dev/null 2>&1; B=$(latest)
out=$(rs 2>&1); rc=$?
equals "no argument exits 2" "$rc" 2
out=$(rs "$T/nowhere" 2>&1); rc=$?
equals "not a folder exits 1" "$rc" 1
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

section "restore: tampered backup"
cp -r "$B" "$T/tampered"
printf 'X' | dd of="$T/tampered/data.tar.gz" bs=1 seek=100 conv=notrunc 2>/dev/null
rm -rf "$REPO/data"
out=$(rs "$T/tampered" 2>&1); rc=$?
equals "damaged backup: exits 1" "$rc" 1
contains "says it's damaged" "$out" "damaged"
check "nothing was restored" test ! -e "$REPO/data"
rm -rf "$T/tampered"

section "restore: the full round trip, on a 'new machine'"
# data, .env, the model and the image are all gone
rm -rf "$REPO/data" "$MODEL"; rm -f "$REPO/.env" "$STUB_STATE/image_id"; echo false > "$STUB_STATE/running"; : > "$STUB_STATE/calls.log"
out=$(rs "$B" 2>&1); rc=$?
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
fresh; bk --model "$DEST" >/dev/null 2>&1; B=$(latest)
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
fresh; bk --model "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data"; printf 'HF_TOKEN=different\n' > "$REPO/.env"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
contains "your .env is kept" "$out" "Keeping your current .env"
equals ".env unchanged" "$(cat "$REPO/.env")" "HF_TOKEN=different"
contains "the existing model is kept" "$out" "already in the cache"
equals "data was restored" "$(tree_hash "$REPO/data")" "$PRISTINE_DATA"

section "restore: another image is already tagged"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data"; echo "sha256:9999ffff" > "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
out=$(rs "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
contains "the current image was tagged before it was replaced" "$(calls)" "docker tag ai-image-studio:local ai-image-studio:before-restore-"
equals "the backed-up image is now ai-image-studio:local" "$(cat "$STUB_STATE/image_id")" "sha256:1111aaaa"

section "restore: --no-* flags"
fresh; bk --model "$DEST" >/dev/null 2>&1; B=$(latest)
rm -rf "$REPO/data" "$MODEL"; rm -f "$REPO/.env" "$STUB_STATE/image_id"; : > "$STUB_STATE/calls.log"
out=$(rs --no-image --no-env --no-model "$B" 2>&1); rc=$?
equals "exits 0" "$rc" 0
check "data restored" test -d "$REPO/data"
check "no .env" test ! -e "$REPO/.env"
check "no model" test ! -e "$MODEL"
check "image not loaded" bash -c "! grep -q 'docker load' '$STUB_STATE/calls.log'"

section "restore: a different code version"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
(cd "$REPO" && echo x > newfile && git add -A && git -c user.email=t@t -c user.name=t commit -q -m later)
out=$(rs --force "$B" 2>&1); rc=$?
contains "warns the code is at another commit" "$out" "was made at"
contains "says how to match it" "$out" "git checkout"

section "restore: archives that try to escape are refused"
mk_evil() {   # mk_evil NAME ENTRY...
  local d="$T/$1"; shift; rm -rf "$d"; mkdir -p "$d"
  python3 - "$d/data.tar.gz" "$@" <<'PY'
import io, sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as t:
    for name in sys.argv[2:]:
        data = b"x"; info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
PY
  printf 'format=1\ncreated_utc=now\nhost=h\nuser=u\ngit_commit=none\ngit_uncommitted_files=0\nstudio_version=t\nimage_name=x\nimage_id=\nincluded=data\nmodel_dir=\n' > "$d/MANIFEST.txt"
  (cd "$d" && sha256sum data.tar.gz MANIFEST.txt > SHA256SUMS)
}
fresh; rm -rf "$REPO/data"
mk_evil evil1 "data/ok.txt" "../evil.txt"
out=$(rs "$T/evil1" 2>&1); rc=$?
equals "a '..' entry: exits 1" "$rc" 1
contains "explained" "$out" "outside 'data/'"
check "nothing was written outside" test ! -e "$REPO/evil.txt" -a ! -e "$T/evil.txt"
check "no data folder was created" test ! -e "$REPO/data"
mk_evil evil2 "data/ok.txt" "other/file.txt"
out=$(rs "$T/evil2" 2>&1); rc=$?
equals "an entry outside data/: exits 1" "$rc" 1
mk_evil evil3 "/tmp/stub-absolute-path-test.txt"
out=$(rs "$T/evil3" 2>&1); rc=$?
equals "an absolute path: exits 1" "$rc" 1
check "the absolute path was not written" test ! -e /tmp/stub-absolute-path-test.txt
mk_evil evil4 "data/../../evil.txt"
out=$(rs "$T/evil4" 2>&1); rc=$?
equals "a path that starts in data/ but climbs out: exits 1" "$rc" 1
check "nothing was written outside" test ! -e "$T/evil.txt" -a ! -e "$REPO/../evil.txt"

section "restore: a failure leaves the studio stopped and says so"
fresh; bk "$DEST" >/dev/null 2>&1; B=$(latest)
echo "sha256:other" > "$STUB_STATE/image_id"
# make the image load fail by pointing at an unreadable image archive after verification is skipped
rm -f "$B/image.tar.gz"; printf 'not gzip data' > "$B/image.tar.gz"
rm -rf "$REPO/data"
out=$(rs --no-verify "$B" 2>&1); rc=$?
check "exits non-zero" test "$rc" -ne 0
contains "says the restore didn't finish" "$out" "did not finish"
equals "studio left stopped" "$(cat "$STUB_STATE/running")" false
check "no temp folder left behind" bash -c "! ls -A '$REPO' | grep -q '^\.restore-tmp'"

# ================================================================== result
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
