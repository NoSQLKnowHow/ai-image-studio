#!/usr/bin/env bash
# Try an edit with several images through the studio's API, before the web page can do it (version 1.2).
#
#   scripts/edit_via_api.sh "put the dog from image 1 into the scene from image 2" dog.jpg park.jpg
#
# It uploads each image, queues one edit (the images are image 1, image 2, ... in the order given), waits for it,
# and saves the result next to where you run it. Needs curl and python3 (both are on the Spark). It changes
# nothing except the studio's own history, where the run appears like any other.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/edit_via_api.sh [options] "PROMPT" IMAGE [IMAGE...]

  --url URL          the studio (default http://127.0.0.1:8080)
  --resolution N     1024 (default) or 2048: sizes every input and, on Auto, the result
  --size WxH         an explicit result size, e.g. 1024x768 (default: Auto, shaped like the last image)
  --shape-from N     with Auto size, make the result follow image N instead of the last one
  --steps N          denoising steps (default 40)
  --seed N           a fixed seed (default: random)
  --images N         how many results to make from the same inputs (default 1)
  --transparent      ask for a transparent (RGBA) result
  --out DIR          where to save the results (default: the current folder)
  -h, --help         this text

The images are numbered in the order you give them, and the prompt can refer to them: "image 1", "image 2".
Exit status: 0 = finished, 1 = the run failed or was canceled, 2 = a problem with the arguments or the studio.
EOF
}

die() { printf 'ERROR: %s\n' "$1" >&2; exit "${2:-2}"; }

URL="http://127.0.0.1:8080"; RESOLUTION=""; SIZE=""; SHAPE_FROM=""; STEPS=""; SEED=""; IMAGES=""; TRANSPARENT=0; OUT="."
while [[ $# -gt 0 ]]; do
  case $1 in
    --url) URL=${2:?--url needs a value}; shift 2;;
    --resolution) RESOLUTION=${2:?--resolution needs a value}; shift 2;;
    --size) SIZE=${2:?--size needs a value}; shift 2;;
    --shape-from) SHAPE_FROM=${2:?--shape-from needs a value}; shift 2;;
    --steps) STEPS=${2:?--steps needs a value}; shift 2;;
    --seed) SEED=${2:?--seed needs a value}; shift 2;;
    --images) IMAGES=${2:?--images needs a value}; shift 2;;
    --transparent) TRANSPARENT=1; shift;;
    --out) OUT=${2:?--out needs a value}; shift 2;;
    -h|--help) usage; exit 0;;
    --) shift; break;;
    -*) usage >&2; die "Unknown option $1.";;
    *) break;;
  esac
done
[[ $# -ge 2 ]] || { usage >&2; die "Give a prompt and at least one image."; }
PROMPT=$1; shift
for pair in RESOLUTION:--resolution SHAPE_FROM:--shape-from STEPS:--steps SEED:--seed IMAGES:--images; do
  name=${pair%%:*}; flag=${pair#*:}; value=${!name}
  [[ -z $value || $value =~ ^[0-9]+$ ]] || die "$flag needs a whole number, not '$value'."
done
[[ -z $SIZE || $SIZE =~ ^[0-9]+[xX][0-9]+$ ]] || die "--size needs the form 1024x768, not '$SIZE'."
command -v curl >/dev/null 2>&1 || die "curl is needed."
command -v python3 >/dev/null 2>&1 || die "python3 is needed."
for image in "$@"; do [[ -f $image ]] || die "No such file: $image"; done
mkdir -p "$OUT"
URL=${URL%/}

# json FIELD: read one field (dotted path) from the JSON on standard input; empty when absent.
json() {
  python3 -c '
import json, sys
value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)' "$1"
}

# request METHOD PATH [curl args...]: the response body on stdout; fails with the server's message on an error status.
request() {
  local method=$1 path=$2; shift 2
  local body status
  body=$(mktemp)
  status=$(curl -sS -o "$body" -w '%{http_code}' -X "$method" -H 'X-Studio-Client: 1' "$@" "$URL$path") \
    || { rm -f "$body"; die "Can't reach the studio at $URL."; }
  if [[ $status != 2* ]]; then
    local detail
    detail=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))["detail"]
    print("; ".join(e.get("msg", str(e)) for e in d) if isinstance(d, list) else d)
except Exception:
    print(open(sys.argv[1], errors="replace").read()[:300])' "$body")
    rm -f "$body"
    die "$method $path -> HTTP $status: $detail"
  fi
  cat "$body"; rm -f "$body"
}

echo "Uploading ${#} image(s) to $URL ..."
UPLOAD_IDS=()
n=0
for image in "$@"; do
  n=$((n + 1))
  reply=$(request POST /api/uploads --data-binary "@$image")
  id=$(printf '%s' "$reply" | json upload_id)
  alpha_note=""
  if [[ $(printf '%s' "$reply" | json has_alpha) == True ]]; then alpha_note=", transparent"; fi
  printf '  image %d: %s (%sx%s%s)\n' "$n" "$image" "$(printf '%s' "$reply" | json width)" "$(printf '%s' "$reply" | json height)" "$alpha_note"
  UPLOAD_IDS+=("$id")
done

body=$(PROMPT=$PROMPT RESOLUTION=$RESOLUTION SIZE=$SIZE SHAPE_FROM=$SHAPE_FROM STEPS=$STEPS SEED=$SEED IMAGES=$IMAGES \
       TRANSPARENT=$TRANSPARENT python3 -c '
import json, os, sys
e = os.environ
options = {}
if e["RESOLUTION"]: options["resolution"] = int(e["RESOLUTION"])
if e["SIZE"]:
    w, h = e["SIZE"].lower().split("x"); options["width"], options["height"] = int(w), int(h)
if e["SHAPE_FROM"]: options["shape_from"] = int(e["SHAPE_FROM"])
if e["STEPS"]: options["steps"] = int(e["STEPS"])
if e["SEED"]: options["seed"] = int(e["SEED"])
if e["IMAGES"]: options["num_images"] = int(e["IMAGES"])
if e["TRANSPARENT"] == "1": options["transparent"] = True
print(json.dumps({"mode": "edit", "prompt": e["PROMPT"], "options": options,
                  "input_images": [{"upload_id": u} for u in sys.argv[1:]]}))' "${UPLOAD_IDS[@]}") \
  || die "Could not build the request."

run=$(request POST /api/runs -H 'Content-Type: application/json' -d "$body")
RUN_ID=$(printf '%s' "$run" | json id)
echo "Queued run $RUN_ID. Waiting (the first run after a quiet spell loads the model, which takes a while)..."

last=""
while :; do
  run=$(request GET "/api/runs/$RUN_ID")
  status=$(printf '%s' "$run" | json status)
  progress=$(printf '%s' "$run" | python3 -c '
import json, sys
r = json.load(sys.stdin); p = r.get("progress")
print("%s%s" % (r["status"], (" - image %s of %s, step %s of %s" % (p["image"], p["of"], p["step"], p["steps"])) if p else ""))')
  if [[ $progress != "$last" ]]; then echo "  $progress"; last=$progress; fi
  case $status in done|failed|canceled) break;; esac
  sleep 2
done

if [[ $status != "done" ]]; then
  message=$(printf '%s' "$run" | json error.message)
  hint=$(printf '%s' "$run" | json error.hint)
  echo "The run ended as: $status${message:+. $message}${hint:+ ($hint)}" >&2
  exit 1
fi

echo "Done. Results:"
index=0
for image_id in $(printf '%s' "$run" | python3 -c 'import json, sys; print(" ".join(i["id"] for i in json.load(sys.stdin)["images"]))'); do
  index=$((index + 1))
  file="$OUT/edit-${RUN_ID:0:8}-$index.png"
  curl -sS -f -o "$file" "$URL/api/images/$image_id" || die "Could not download result $index."
  printf '  %s\n' "$file"
done
printf 'Size and settings are on the run'"'"'s card in the web page. Inputs: %s image(s), prompt: "%s"\n' "$#" "$PROMPT"
