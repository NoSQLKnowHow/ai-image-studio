#!/usr/bin/env python3
"""Make music with MiniMax-Music3 from the command line (instrumental unless you give lyrics).

It uses the very same pipeline code the studio's Music tab uses (backend/studio/pipelines/real_music.py), so it is also
the quickest way to try the real model on the Spark, with no web page in between.

Examples (inside the studio container, which has everything installed:
  docker compose exec studio python /app/scripts/minimax_music.py ...
or in any environment with PyTorch, diffusers 0.40.0 and transformers, from the repository's root):

  # A minute of instrumental music, described with fields (the same fields as the Music tab)
  ./scripts/minimax_music.py --genre "acoustic pop" --bpm 96 --key "C major" \\
      --mood "warm and intimate, building gently" \\
      --instruments "fingerpicked guitar and soft piano; brushed drums and upright bass enter in the second half"

  # A short try first: 15 seconds
  ./scripts/minimax_music.py --genre "ambient" --mood "slow, spacious" --duration 15 --out try.wav

  # Your own description text, exactly as the model should read it
  ./scripts/minimax_music.py --prompt-file my_description.txt --seed 7

  # With lyrics (from a file; each [Verse]/[Chorus] tag on its own line) and a voice
  ./scripts/minimax_music.py --genre "indie folk" --voice "soft female lead, breathy" --lyrics @lyrics.txt

  # See exactly what would be sent, without loading anything
  ./scripts/minimax_music.py --genre "lo-fi hip hop" --bpm 80 --dry-run

The model is loaded from the local cache and only fetched if files are missing (--hub auto, like the studio's
STUDIO_LOCAL_FILES_ONLY=auto); --hub offline never touches the network. The first run downloads about 29 GB.

Output is a 16-bit stereo WAV (44.1 kHz) with a note in its file information that it is machine-generated. The model's
licence asks you to say so when you share music made with it publicly.

Exit codes: 0 ok, 1 unexpected error, 2 bad arguments, 3 environment problem (missing packages, no CUDA),
4 model load failed, 5 generation failed, 6 output (disk) problem, 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "backend", Path("/app/backend")):  # a checkout, or the studio image
    if (candidate / "studio").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
# The studio image keeps the diffusers that has the music pipeline in its own folder, which only the music worker puts
# first on its path (DESIGN.md section 26.4). This script does the same, or `python` would find the image worker's one.
_libs = os.environ.get("STUDIO_MUSIC_LIBS", "").strip()
if _libs and Path(_libs).is_dir():
    sys.path.insert(0, _libs)

from studio import presets as P  # noqa: E402
from studio.musicprompt import FIELDS, INSTRUMENTAL_LYRICS, build_description  # noqa: E402

LICENCE_REMINDER = ("Made with MiniMax-Music3 (machine-generated). If you share it publicly, say so: the model's licence "
                    "asks for that.")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_argument_group("the description (use these fields, or --prompt / --prompt-file for your own text)")
    group.add_argument("--genre", help='e.g. "acoustic pop"')
    group.add_argument("--mood", help='the emotional arc, e.g. "warm and intimate, building gently"')
    group.add_argument("--bpm", help="tempo, e.g. 96")
    group.add_argument("--key", help='e.g. "C major" or "F# minor"')
    group.add_argument("--instruments", help="instruments and how the arrangement develops")
    group.add_argument("--voice", help="the voice, only used with lyrics")
    group.add_argument("--prompt", help="the whole description as text (instead of the fields)")
    group.add_argument("--prompt-file", type=Path, help="read the whole description from a file ('-' for standard input)")
    ap.add_argument("--lyrics", help="lyrics as text, or @file to read them from a file. Without lyrics the track is instrumental")
    ap.add_argument("--duration", type=int, default=P.MUSIC_DEFAULT_SECONDS,
                    help=f"the most seconds to make, {P.MUSIC_DURATION_MIN}-{P.MUSIC_HARD_MAX_SECONDS} (default %(default)s); "
                         "the model may end sooner")
    ap.add_argument("--steps", type=int, default=P.MUSIC_DEFAULT_STEPS,
                    help=f"flow-matching steps per window, {P.MUSIC_STEPS_MIN}-{P.MUSIC_STEPS_MAX} (default %(default)s)")
    ap.add_argument("--seed", type=int, default=0, help="the seed (default %(default)s); the same seed repeats a track")
    ap.add_argument("--out", type=Path, default=Path("music.wav"), help="the WAV file to write (default %(default)s)")
    ap.add_argument("--model", default="MiniMaxAI/MiniMax-Music3", help="the Hugging Face repository or a local folder")
    ap.add_argument("--hub", choices=["auto", "offline", "online"], default="auto",
                    help="auto: the cache first, the network only for missing files; offline: never; online: always ask")
    ap.add_argument("--device", default="cuda", help=argparse.SUPPRESS)  # "cpu" is for trying the script on a tiny test model
    ap.add_argument("--dtype", default="bfloat16", help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent and stop (loads nothing)")
    return ap


def read_text(value: str) -> str:
    """Text, or the contents of a file when the value is @path."""
    if value.startswith("@"):
        return Path(value[1:]).expanduser().read_text(encoding="utf-8")
    return value


def description_and_lyrics(args: argparse.Namespace) -> tuple[str, str, bool]:
    """(description, lyrics, instrumental) from the arguments. Raises ValueError with a message for the user."""
    lyrics = read_text(args.lyrics).strip() if args.lyrics else ""
    instrumental = not lyrics
    if args.prompt_file is not None:
        text = sys.stdin.read() if str(args.prompt_file) == "-" else args.prompt_file.expanduser().read_text(encoding="utf-8")
    elif args.prompt:
        text = args.prompt
    else:
        text = build_description({name: getattr(args, name) for name in FIELDS}, instrumental)
    text = text.strip()
    if not any(getattr(args, name) for name in FIELDS) and not (args.prompt or args.prompt_file):
        raise ValueError("Describe the music: give at least --genre, --mood, --instruments (and so on), or --prompt / --prompt-file.")
    if len(text) > P.MUSIC_DESCRIPTION_MAX:
        raise ValueError(f"The description is {len(text)} characters; the studio's limit is {P.MUSIC_DESCRIPTION_MAX} "
                         "(the model's own is 5,000 tokens for the description and lyrics together).")
    if len(lyrics) > P.MUSIC_LYRICS_MAX:
        raise ValueError(f"The lyrics are {len(lyrics)} characters; the studio's limit is {P.MUSIC_LYRICS_MAX}.")
    return text, (lyrics or INSTRUMENTAL_LYRICS), instrumental


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not P.MUSIC_DURATION_MIN <= args.duration <= P.MUSIC_HARD_MAX_SECONDS:
        print(f"--duration must be between {P.MUSIC_DURATION_MIN} and {P.MUSIC_HARD_MAX_SECONDS} seconds.", file=sys.stderr)
        return 2
    if not P.MUSIC_STEPS_MIN <= args.steps <= P.MUSIC_STEPS_MAX:
        print(f"--steps must be between {P.MUSIC_STEPS_MIN} and {P.MUSIC_STEPS_MAX}.", file=sys.stderr)
        return 2
    try:
        description, lyrics, instrumental = description_and_lyrics(args)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"--- description ---\n{description}\n--- lyrics ---\n{lyrics}\n--- "
          f"{'instrumental' if instrumental else 'with lyrics'}, up to {args.duration} s, {args.steps} steps, seed {args.seed} ---",
          file=sys.stderr)
    if args.dry_run:
        return 0

    from studio import wavfile
    from studio.pipelines.base import Canceled, MusicJob, PipelineError, PipelineLoadError, PipelineUnavailable
    from studio.pipelines.real_music import RealMusicPipeline

    pipeline = RealMusicPipeline(args.model, hub_mode=args.hub, device=args.device, dtype=args.dtype)
    started = time.monotonic()
    print("loading the model (the first time this downloads about 29 GB)...", file=sys.stderr, flush=True)
    try:
        pipeline.load()
    except PipelineUnavailable as exc:
        print(f"This environment cannot run the music model: {exc.message}\n{exc.hint or ''}", file=sys.stderr)
        return 3
    except PipelineLoadError as exc:
        print(f"The model could not be loaded: {exc.message}\n{exc.hint or ''}", file=sys.stderr)
        return 4
    except PipelineError as exc:
        print(f"The model could not be loaded: {exc.message}\n{exc.hint or ''}", file=sys.stderr)
        return 4
    print(f"loaded in {time.monotonic() - started:.0f} s", file=sys.stderr, flush=True)

    last = [0.0, ""]

    def show(stage: str, step: int, total: int) -> None:
        now = time.monotonic()
        if stage != last[1] or step == total or now - last[0] >= 1.0:
            last[0], last[1] = now, stage
            label = {"compose": "composing", "render": "rendering", "finish": "finishing"}.get(stage, stage)
            print(f"\r{label}: {step}/{total}   " if stage != "finish" else "\rfinishing...            ", end="", file=sys.stderr, flush=True)

    job = MusicJob(run_id="0" * 32, mode="music", prompt=description, lyrics=lyrics, duration=args.duration, steps=args.steps,
                   seeds=[args.seed], model_id=args.model)
    started = time.monotonic()
    try:
        made = pipeline.generate(job, 0, args.seed, show)
    except Canceled:
        print("\ncanceled", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(f"\nMaking the track failed: {exc.message}\n{exc.hint or ''}", file=sys.stderr)
        return 5
    print(f"\nmade {made.seconds:.1f} s of music in {time.monotonic() - started:.0f} s", file=sys.stderr)

    try:
        wavfile.write_wav(args.out, made.pcm, made.sample_rate, made.channels, {
            "title": " ".join(description.split())[:80],
            "software": "ai-image-studio (scripts/minimax_music.py)",
            "comment": f"Generated by {args.model}: machine-generated music. If you share it publicly, say so (the model's "
                       f"licence asks for that). Seed {args.seed}. Description: {' '.join(description.split())[:600]}",
            "date": time.strftime("%Y-%m-%d"),
        })
    except OSError as exc:
        print(f"The file could not be written: {exc.strerror or exc}", file=sys.stderr)
        return 6
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)\n{LICENCE_REMINDER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
