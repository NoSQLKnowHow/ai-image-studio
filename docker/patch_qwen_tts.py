"""Make one import of qwen-tts optional, in the studio's own copy of the library (DESIGN.md section 38.3, item 72 of 38.18).

    python docker/patch_qwen_tts.py [FOLDER]        FOLDER is where qwen-tts was installed; the default is the image's /opt/speech-libs/qwen3-tts

qwen-tts 0.1.1 begins `qwen_tts/core/tokenizer_25hz/vq/speech_vq.py` with `import torchaudio.compliance.kaldi as kaldi`, and the package loads that file whenever it is
imported (`qwen_tts/core/__init__.py` imports both of its speech tokenizers). Only the OLD 25 Hz tokenizer calls it, once, to make filter-bank features. The checkpoints the
studio uses (Qwen3-TTS-12Hz-1.7B-CustomVoice, -VoiceDesign and -Base) use the 12 Hz tokenizer, which has no use for it. NVIDIA's PyTorch image has no torchaudio for the DGX
Spark, and a torchaudio from PyPI is built for a PyTorch that is not NVIDIA's (it would have to replace it), so without this the whole library fails to import with
`No module named 'torchaudio'` (seen on a Spark, 2026-11-09) although nothing the studio runs would use it.

The patch turns that one line into a try: where torchaudio exists it is used exactly as before, and where it does not, `kaldi` is a stand-in whose `fbank` says why it cannot
work. Nothing else in the library is touched, and the file is only changed in the folder it is given, which only the Qwen3-TTS worker puts on its Python path.

It does nothing twice, and refuses (exit code 1, the file left as it was) a file that is not the one it was written for: a different version of qwen-tts is a reason to look
again, not to patch blind. A failure is only a note in the build (the engine then says why it cannot run); it cannot fail the build.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_FOLDER = "/opt/speech-libs/qwen3-tts"
RELATIVE_FILE = "qwen_tts/core/tokenizer_25hz/vq/speech_vq.py"
ORIGINAL = "import torchaudio.compliance.kaldi as kaldi"
MARKER = "patched by AI Image Studio"
REPLACEMENT = f'''try:  # {MARKER} (docker/patch_qwen_tts.py): only the 25 Hz tokenizer uses it, and NVIDIA's image has no torchaudio for the Spark
    import torchaudio.compliance.kaldi as kaldi
except ImportError:
    class kaldi:  # noqa: N801  (a stand-in with the one name this file uses, which says why it cannot work)
        @staticmethod
        def fbank(*args, **kwargs):
            raise RuntimeError("torchaudio is not installed in this image, and the 25 Hz tokenizer of Qwen3-TTS needs it; the 12 Hz models the studio uses do not.")'''


def patch(folder: Path) -> str:
    """Patch the file under `folder`. Returns what was done ("patched" or "already patched"); raises ValueError, with the reason, when it cannot."""
    path = folder / RELATIVE_FILE
    if not path.is_file():
        raise ValueError(f"{path} is not there; is qwen-tts installed in {folder}?")
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return "already patched"
    count = text.splitlines().count(ORIGINAL)  # a whole line, as it is in 0.1.1: a line that merely contains it is not the one
    if count != 1:
        raise ValueError(f"{path} has {count} lines that read {ORIGINAL!r}, where qwen-tts 0.1.1 has exactly one; this is another version, so it is left as it is.")
    patched = "\n".join(REPLACEMENT if line == ORIGINAL else line for line in text.split("\n"))
    ast.parse(patched, filename=str(path))  # never leave a file that cannot be read
    path.write_text(patched, encoding="utf-8")
    return "patched"


def main(argv: list[str]) -> int:
    folder = Path(argv[1] if len(argv) > 1 else DEFAULT_FOLDER)
    try:
        print(f"patch_qwen_tts: {patch(folder)}: {folder / RELATIVE_FILE}")
    except ValueError as exc:
        print(f"patch_qwen_tts: not done: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
