"""The real speech engines (DESIGN.md §38.1, §38.3), tested as far as they can be without their models.

Nothing here loads a model, uses a GPU or needs the network (criterion 291). What is tested is the studio's own code in each engine: how it describes itself, how it
finds and loads its files under the cache-first rule, what it asks its library for, what it does with what comes back, and how it fails. The libraries are stand-ins
that take exactly the calls their sources define (read from kokoro 0.9.4, piper-tts 1.8.0 and qwen-tts 0.1.1) and record them, so a call with a wrong name or a
missing argument fails here and not on the Spark. What the models SOUND like is a Spark check (DESIGN.md §38.14); nothing here can say.

Voxtral, which is a server of its own, is in test_speech_voxtral.py.
"""

from __future__ import annotations

import array
import io
import math
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from studio.pipelines.base import Canceled, OutOfMemory, PipelineError, PipelineLoadError, PipelineUnavailable
from studio.speech import registry
from studio.speech.engine import EngineSettings, PieceRequest, VoiceRef
from studio.speech.engines import common, kokoro, piper, qwen3

NEVER = lambda: False  # noqa: E731  (a Cancel that never comes)
REAL = list(registry.REAL_ENGINES)


def pcm_samples(pcm: bytes) -> list[int]:
    values = array.array("h")
    values.frombytes(pcm)
    return list(values)


# ---------------------------------------------------------------- every real engine describes itself consistently
@pytest.mark.parametrize("engine_id", REAL)
def test_a_real_engine_can_be_described_without_any_of_its_libraries(engine_id):
    """The API process has none of them (no torch): its module must import, and say what it is, with nothing installed. A module-level import of a library would
    break the whole Speech tab for the one engine that needs it."""
    info = registry.info(engine_id)
    assert info.id == engine_id and info.title and info.licence.name and info.model_id
    assert registry.presets(engine_id)


@pytest.mark.parametrize("engine_id", REAL)
def test_what_an_engine_claims_to_do_it_has_a_variant_for(engine_id):
    info = registry.info(engine_id)
    assert info.preset_variant in info.variants
    assert (info.clone_variant in info.variants) if info.can_clone else info.clone_variant is None
    assert (info.design_variant in info.variants) if info.can_design else info.design_variant is None
    assert info.max_chars > info.min_chars > 0 and info.sample_rate in (22050, 24000) and info.memory_gb > 0
    assert len(set(info.languages)) == len(info.languages)


@pytest.mark.parametrize("engine_id", REAL)
def test_the_preset_voices_state_only_what_a_source_states(engine_id):
    presets = registry.presets(engine_id)
    assert len({v.id for v in presets}) == len(presets) and all(v.label for v in presets)
    assert {v.gender for v in presets} <= {"female", "male", None} and all(v.accent is None or v.accent.strip() for v in presets)
    info = registry.info(engine_id)
    assert all(not v.styleable for v in presets) or info.can_style  # a voice cannot take a style the engine cannot


@pytest.mark.parametrize("engine_id", REAL)
def test_an_engine_whose_library_is_missing_says_so_and_what_to_do(engine_id):
    """In this environment none of the libraries is installed: each probe must say so in a sentence, with a hint, and not crash."""
    with pytest.raises(PipelineUnavailable) as caught:
        registry.probe_engine(engine_id)
    assert caught.value.message and caught.value.hint


def test_the_licences_are_what_the_sources_say():
    assert registry.info("voxtral").licence.noncommercial is True and "CC BY-NC" in registry.info("voxtral").licence.name  # non-commercial: the badge shows it
    assert registry.info("kokoro").licence.name == "Apache-2.0" and registry.info("qwen3-tts").licence.name == "Apache-2.0"
    piper_licence = registry.info("piper").licence
    assert "GPL" in piper_licence.name and "GPL" in piper_licence.notice and "Jenny" in piper_licence.notice  # said plainly, and goes into every WAV


def test_who_can_do_what_matches_the_specification():
    assert [(e, i.can_style, i.can_clone, i.can_design) for e in REAL if (i := registry.info(e))] == [
        ("qwen3-tts", True, True, True), ("voxtral", False, False, False), ("kokoro", False, False, False), ("piper", False, False, False)]


def test_the_catalogue_of_voices_is_what_the_cards_list():
    assert len(registry.presets("qwen3-tts")) == 9 and len(registry.presets("voxtral")) == 20 and len(registry.presets("kokoro")) == 28
    assert [v.id for v in registry.presets("piper")] == ["jenny_dioco"]
    qwen = {v.id: v for v in registry.presets("qwen3-tts")}
    assert [v.id for v in qwen.values() if v.gender == "female"] == ["Vivian", "Serena", "Ono_Anna", "Sohee"]  # four, as the README's table says
    assert qwen["Aiden"].accent == "American" and qwen["Ryan"].accent is None  # only Aiden's accent is stated; Ryan's is not guessed
    assert all(v.styleable for v in qwen.values())
    kokoro_voices = {v.id: v for v in registry.presets("kokoro")}
    assert sum(1 for v in kokoro_voices.values() if v.id.startswith("af_")) == 11 and sum(1 for v in kokoro_voices.values() if v.id.startswith("am_")) == 9
    assert sum(1 for v in kokoro_voices.values() if v.id.startswith("bf_")) == 4 and sum(1 for v in kokoro_voices.values() if v.id.startswith("bm_")) == 4
    assert kokoro_voices["af_bella"].accent == "American" and kokoro_voices["bf_emma"].accent == "British" and "A-" in kokoro_voices["af_bella"].description
    voxtral = registry.presets("voxtral")
    assert sum(1 for v in voxtral if v.gender == "female") == 10 and all(v.accent is None for v in voxtral)  # the card states no accent for any
    jenny = registry.presets("piper")[0]
    assert (jenny.gender, jenny.accent, jenny.language) == ("female", "Irish", "English")


# ---------------------------------------------------------------- the shared helpers
def test_floats_become_16_bit_samples_clipped_and_never_not_a_number():
    pcm = common.pcm16([0.0, 0.5, -0.5, 1.0, -1.0, 2.5, -9.0, float("nan")])
    assert pcm_samples(pcm) == [0, 16384, -16384, 32767, -32767, 32767, -32767, 0]


def test_numpy_and_tensor_like_input_gives_the_same_samples_as_a_list():
    np = pytest.importorskip("numpy")
    values = [0.0, 0.25, -0.25, 1.0, 3.0, float("nan")]
    expected = common.pcm16(values)
    assert common.pcm16(np.array(values, dtype=np.float32)) == expected

    class Tensor:  # what a torch tensor offers that matters here: detach().cpu().float().numpy()
        def __init__(self, data): self.data = data
        def detach(self): return self
        def cpu(self): return self
        def float(self): return self
        def numpy(self): return np.array(self.data, dtype=np.float32)

    assert common.pcm16(Tensor(values)) == expected
    stereo = common.pcm16(np.array([[0.5, 0.5, 0.5], [-0.5, -0.5, -0.5]], dtype=np.float32))
    assert pcm_samples(stereo) == [0, 0, 0]  # two channels are averaged to one


def test_no_audio_at_all_is_an_error_with_advice_and_not_a_silent_piece():
    for empty in ([], ()):
        with pytest.raises(PipelineError, match="no audio") as caught:
            common.pcm16(empty)
        assert caught.value.hint


def wav_bytes(samples: list[float], rate: int = 24000, channels: int = 1, bits: int = 16, kind: str = "int", extensible: bool = False, others: float | None = None) -> bytes:
    """A WAV in the form an engine's server might send: integer samples of 16, 24 or 32 bits, or 32-bit float, any number of channels. Every channel carries the
    sample, except that when `others` is given the channels after the first carry that value instead (so that taking one channel and averaging them differ)."""
    frames = []
    for s in samples:
        for channel in range(channels):
            value = s if channel == 0 or others is None else others
            frames.append(struct.pack("<f", value) if kind == "float" else int(value * (2 ** (bits - 1) - 1)).to_bytes(bits // 8, "little", signed=True))
    data = b"".join(frames)
    code = 3 if kind == "float" else 1
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits, 22, bits, 0) + struct.pack("<H", code) + bytes(14)
    else:
        fmt = struct.pack("<HHIIHH", code, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize("kwargs", [
    {"bits": 16}, {"bits": 24}, {"bits": 32}, {"kind": "float", "bits": 32}, {"bits": 16, "extensible": True}, {"kind": "float", "bits": 32, "extensible": True},
])
def test_a_wav_from_a_server_is_read_whatever_its_sample_format(kwargs):
    pcm, rate = common.wav_to_pcm16(wav_bytes([0.0, 0.5, -0.5, 0.25], **kwargs))
    assert rate == 24000 and [round(v / 32767, 2) for v in pcm_samples(pcm)] == [0.0, 0.5, -0.5, 0.25]


def test_the_channels_of_a_wav_from_a_server_are_averaged_into_one():
    same = wav_bytes([0.5, -0.5], channels=2)  # the same value in both channels: the average is that value
    assert [round(v / 32767, 2) for v in pcm_samples(common.wav_to_pcm16(same)[0])] == [0.5, -0.5]
    different = wav_bytes([1.0, 0.5], channels=2, others=0.0)  # sound on the left only: the average is half, and taking just the left would be all of it
    assert [round(v / 32767, 2) for v in pcm_samples(common.wav_to_pcm16(different)[0])] == [0.5, 0.25]


def test_a_wav_with_an_unknown_length_is_read_to_its_end():
    data = bytearray(wav_bytes([0.5] * 100))
    data[40:44] = struct.pack("<I", 0xFFFFFFFF)  # the length a streaming server writes before it knows
    assert len(pcm_samples(common.wav_to_pcm16(bytes(data))[0])) == 100


@pytest.mark.parametrize("junk", [b"", b"not a wav at all, really not", b"RIFF" + bytes(60), wav_bytes([0.5])[:30]])
def test_something_that_is_not_a_wav_is_an_error_naming_the_server(junk):
    with pytest.raises(PipelineError, match="WAV"):
        common.wav_to_pcm16(junk)


def test_a_failure_while_speaking_is_worded_for_the_person_and_out_of_memory_is_its_own_kind():
    class Oom(RuntimeError):
        pass

    torch = SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=Oom, empty_cache=lambda: None))
    assert isinstance(common.speaking_failed(Oom("CUDA out of memory. Tried to allocate 2 GiB"), "Kokoro", torch), OutOfMemory)
    assert isinstance(common.speaking_failed(RuntimeError("CUDA error: out of memory"), "Kokoro", None), OutOfMemory)  # by its words, with no torch to ask
    other = common.speaking_failed(ValueError("bad token"), "Kokoro", torch)
    assert type(other) is PipelineError and "Kokoro could not speak this paragraph: ValueError: bad token" in other.message and other.hint
    already = PipelineError("its own words", hint="its own hint")
    assert common.speaking_failed(already, "Kokoro", torch) is already  # an engine's own error is not reworded


def test_seeding_reaches_torch_and_the_gpu_only_where_there_is_one():
    calls: list[tuple[str, int]] = []
    cuda = SimpleNamespace(is_available=lambda: True, manual_seed_all=lambda s: calls.append(("cuda", s)))
    common.seed_torch(SimpleNamespace(manual_seed=lambda s: calls.append(("cpu", s)), cuda=cuda), 7)
    assert calls == [("cpu", 7), ("cuda", 7)]
    calls.clear()
    common.seed_torch(SimpleNamespace(manual_seed=lambda s: calls.append(("cpu", s)), cuda=SimpleNamespace(is_available=lambda: False)), 9)
    assert calls == [("cpu", 9)]


# ---------------------------------------------------------------- the stand-in libraries
class CacheMiss(Exception):
    """Named as huggingface_hub names the error for a file that is not in the cache: the studio recognises the class name."""


CacheMiss.__name__ = "LocalEntryNotFoundError"


class Hub:
    """A stand-in for huggingface_hub.hf_hub_download: records every call, and has only the files named in `cached` until `online` is allowed."""

    def __init__(self, cached: set[str] | None = None, allow_online: bool = True):
        self.cached, self.allow_online, self.calls = cached, allow_online, []

    def __call__(self, repo_id: str, filename: str, local_files_only: bool = False) -> str:
        self.calls.append((repo_id, filename, local_files_only))
        if local_files_only and self.cached is not None and filename not in self.cached:
            raise CacheMiss(f"{filename} is not in the cache")
        if not local_files_only and not self.allow_online:
            raise ConnectionError("Cannot reach Hugging Face")
        return f"/cache/{repo_id}/{filename}"


def fake_torch(cuda: bool = True) -> SimpleNamespace:
    class OutOfMemoryError(RuntimeError):
        pass

    seeds: list[int] = []
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda, OutOfMemoryError=OutOfMemoryError, empty_cache=lambda: None,
                                                manual_seed_all=lambda s: None), manual_seed=seeds.append, seeds=seeds, bfloat16="bfloat16")


def settings(variant: str = "default", model: str = "", hub_mode: str = "auto") -> EngineSettings:
    return EngineSettings(variant=variant, model=model, hub_mode=hub_mode)


def request(text: str = "A paragraph to speak.", voice: str = "", **fields: Any) -> PieceRequest:
    return PieceRequest(text=text, voice=voice, **fields)


# ---------------------------------------------------------------- Kokoro
class FakeKModel:
    made: list["FakeKModel"] = []

    def __init__(self, repo_id=None, config=None, model=None):
        self.repo_id, self.config, self.model, self.device = repo_id, config, model, None
        FakeKModel.made.append(self)

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self


class FakeKPipeline:
    made: list["FakeKPipeline"] = []
    chunks: list[Any] = [[0.1, -0.1, 0.2]]

    def __init__(self, lang_code, repo_id=None, model=None):
        self.lang_code, self.repo_id, self.model, self.calls = lang_code, repo_id, model, []
        FakeKPipeline.made.append(self)

    def __call__(self, text, voice=None, speed=1, split_pattern=None):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "split_pattern": split_pattern})
        for audio in FakeKPipeline.chunks:
            yield SimpleNamespace(graphemes=text, phonemes="", audio=audio)


@pytest.fixture
def kokoro_libs(monkeypatch):
    FakeKModel.made, FakeKPipeline.made, FakeKPipeline.chunks = [], [], [[0.1, -0.1, 0.2]]
    libs = SimpleNamespace(torch=fake_torch(), KModel=FakeKModel, KPipeline=FakeKPipeline, hf_hub_download=Hub())
    monkeypatch.setattr(kokoro, "_libs", lambda: libs)
    return libs


def loaded_kokoro(**kw) -> kokoro.Kokoro:
    engine = kokoro.Kokoro(settings(**kw))
    engine.load()
    return engine


def test_kokoro_loads_the_model_from_the_cache_by_the_files_it_names(kokoro_libs):
    engine = kokoro.Kokoro(settings())
    assert engine.load() == {"device": "cuda"}
    made = FakeKModel.made[0]
    assert (made.repo_id, made.config, made.model, made.device) == ("hexgrad/Kokoro-82M", "/cache/hexgrad/Kokoro-82M/config.json", "/cache/hexgrad/Kokoro-82M/kokoro-v1_0.pth", "cuda")
    assert [(f, local) for _, f, local in kokoro_libs.hf_hub_download.calls] == [("config.json", True), ("kokoro-v1_0.pth", True)]  # the cache first, as for every model


def test_kokoro_goes_online_only_for_files_the_cache_lacks_and_not_when_told_to_stay_offline(kokoro_libs):
    kokoro_libs.hf_hub_download = Hub(cached=set())
    loaded_kokoro()
    assert [local for _, _, local in kokoro_libs.hf_hub_download.calls] == [True, False, True, False] or [local for _, _, local in kokoro_libs.hf_hub_download.calls][:2] == [True, False]
    kokoro_libs.hf_hub_download = Hub(cached=set())
    with pytest.raises(PipelineLoadError, match="not in the local cache") as caught:
        loaded_kokoro(hub_mode="offline")
    assert caught.value.hint and all(local for _, _, local in kokoro_libs.hf_hub_download.calls)  # offline never asks the network


def test_kokoro_says_when_it_cannot_reach_the_hub(kokoro_libs):
    kokoro_libs.hf_hub_download = Hub(cached=set(), allow_online=False)
    with pytest.raises(PipelineLoadError, match="Cannot reach Hugging Face") as caught:
        loaded_kokoro()
    assert "internet connection" in caught.value.hint


def test_kokoro_runs_on_the_cpu_where_there_is_no_gpu(kokoro_libs):
    kokoro_libs.torch = fake_torch(cuda=False)
    assert loaded_kokoro().load() == {"device": "cpu"} and FakeKModel.made[-1].device == "cpu"


def test_kokoro_reads_a_local_copy_from_the_folder_the_person_named(kokoro_libs, tmp_path):
    for name in ("config.json", "kokoro-v1_0.pth", "voices/af_heart.pt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    engine = loaded_kokoro(model=str(tmp_path))
    assert FakeKModel.made[0].config == str(tmp_path / "config.json") and FakeKModel.made[0].model == str(tmp_path / "kokoro-v1_0.pth")
    engine.synthesize(request(voice="af_heart"), NEVER)
    assert FakeKPipeline.made[0].calls[0]["voice"] == str(tmp_path / "voices" / "af_heart.pt") and kokoro_libs.hf_hub_download.calls == []  # nothing from the hub


def test_a_local_folder_missing_a_file_says_which(kokoro_libs, tmp_path):
    (tmp_path / "config.json").write_bytes(b"x")
    with pytest.raises(PipelineLoadError, match="kokoro-v1_0.pth is not there"):
        loaded_kokoro(model=str(tmp_path))


def test_kokoro_speaks_a_piece_with_the_voice_file_at_24_khz(kokoro_libs):
    engine = loaded_kokoro()
    speech = engine.synthesize(request("Good evening.", voice="af_bella", seed=5), NEVER)
    assert speech.sample_rate == 24000 and pcm_samples(speech.pcm) == [3277, -3277, 6553]
    call = FakeKPipeline.made[0].calls[0]
    assert call == {"text": "Good evening.", "voice": "/cache/hexgrad/Kokoro-82M/voices/af_bella.pt", "speed": 1.0, "split_pattern": None}
    assert FakeKPipeline.made[0].repo_id == "hexgrad/Kokoro-82M" and FakeKPipeline.made[0].model is FakeKModel.made[0]


def test_kokoro_makes_a_pipeline_for_each_language_and_shares_the_one_model(kokoro_libs):
    engine = loaded_kokoro()
    for voice in ("af_heart", "af_bella", "bf_emma", "am_adam", "bm_george"):
        engine.synthesize(request(voice=voice), NEVER)
    assert sorted(p.lang_code for p in FakeKPipeline.made) == ["a", "b"]  # American and British, once each
    assert len(FakeKModel.made) == 1 and all(p.model is FakeKModel.made[0] for p in FakeKPipeline.made)
    assert [c["voice"].rsplit("/", 1)[1] for p in FakeKPipeline.made for c in p.calls] in (
        ["af_heart.pt", "af_bella.pt", "am_adam.pt", "bf_emma.pt", "bm_george.pt"], ["af_heart.pt", "af_bella.pt", "bf_emma.pt", "am_adam.pt", "bm_george.pt"])


def test_kokoro_joins_the_chunks_the_library_gives_for_a_long_piece(kokoro_libs):
    FakeKPipeline.chunks = [[0.5, 0.5], None, [-0.5]]  # a chunk with no audio (the library can give one) is skipped
    assert pcm_samples(loaded_kokoro().synthesize(request(voice="af_heart"), NEVER).pcm) == [16384, 16384, -16384]


def test_kokoro_stops_between_chunks_when_cancel_comes(kokoro_libs):
    FakeKPipeline.chunks = [[0.1], [0.2], [0.3]]
    asked = []

    def cancel_after_first() -> bool:
        asked.append(1)
        return len(asked) > 2

    with pytest.raises(Canceled):
        loaded_kokoro().synthesize(request(voice="af_heart"), cancel_after_first)


def test_kokoro_refuses_a_voice_it_does_not_have_and_a_piece_before_it_is_loaded(kokoro_libs):
    engine = loaded_kokoro()
    for voice in ("", "nobody", "af_heart.pt", "../af_heart", "AF_HEART"):
        with pytest.raises(PipelineError, match="no voice called"):
            engine.synthesize(request(voice=voice), NEVER)
    with pytest.raises(PipelineError, match="not loaded"):
        kokoro.Kokoro(settings()).synthesize(request(voice="af_heart"), NEVER)
    with pytest.raises(Canceled):
        engine.synthesize(request(voice="af_heart"), lambda: True)


def test_kokoro_reports_a_piece_it_could_make_nothing_of(kokoro_libs):
    FakeKPipeline.chunks = [None]
    with pytest.raises(PipelineError, match="no audio") as caught:
        loaded_kokoro().synthesize(request("...", voice="af_heart"), NEVER)
    assert caught.value.hint


def test_kokoro_running_out_of_memory_is_its_own_kind_of_failure(kokoro_libs):
    def boom(*a, **k):
        raise kokoro_libs.torch.cuda.OutOfMemoryError("CUDA out of memory")

    engine = loaded_kokoro()
    engine._pipeline = lambda lang: boom  # type: ignore[method-assign]
    with pytest.raises(OutOfMemory):
        engine.synthesize(request(voice="af_heart"), NEVER)


def test_kokoros_probe_reports_the_device_and_a_note_when_espeak_is_missing(kokoro_libs, monkeypatch):
    monkeypatch.setattr(kokoro, "_espeak_fallback_works", lambda: False)
    result = kokoro.Kokoro.probe()
    assert result["device"] == "cuda" and any("espeak-ng" in note for note in result["notes"])  # a note, not a refusal: English works without it
    monkeypatch.setattr(kokoro, "_espeak_fallback_works", lambda: True)
    assert kokoro.Kokoro.probe()["notes"] == []


def test_kokoros_probe_is_unavailable_without_its_library(monkeypatch):
    def missing():
        raise ImportError("No module named 'kokoro'")

    monkeypatch.setattr(kokoro, "_libs", missing)
    with pytest.raises(PipelineUnavailable, match="No module named 'kokoro'"):
        kokoro.Kokoro.probe()


# ---------------------------------------------------------------- Piper
class FakePiperVoice:
    loads: list[tuple[str, bool]] = []
    sentences: list[bytes] = [b"\x01\x00\x02\x00", b"\x03\x00"]
    rate, config_rate, channels, width = 22050, 22050, 1, 2  # (the rate of the chunks, and the rate the voice's config file says: normally the same)

    def __init__(self):
        self.config = SimpleNamespace(sample_rate=FakePiperVoice.config_rate)
        self.texts: list[str] = []

    @staticmethod
    def load(model_path, use_cuda=False):
        FakePiperVoice.loads.append((str(model_path), use_cuda))
        return FakePiperVoice()

    def synthesize(self, text):
        self.texts.append(text)
        for audio in FakePiperVoice.sentences:
            yield SimpleNamespace(sample_rate=FakePiperVoice.rate, sample_width=FakePiperVoice.width, sample_channels=FakePiperVoice.channels, audio_int16_bytes=audio)


@pytest.fixture
def piper_libs(monkeypatch):
    FakePiperVoice.loads, FakePiperVoice.sentences = [], [b"\x01\x00\x02\x00", b"\x03\x00"]
    FakePiperVoice.rate, FakePiperVoice.config_rate, FakePiperVoice.channels, FakePiperVoice.width = 22050, 22050, 1, 2
    libs = SimpleNamespace(PiperVoice=FakePiperVoice, hf_hub_download=Hub())
    monkeypatch.setattr(piper, "_libs", lambda: libs)
    return libs


def test_piper_fetches_the_voice_and_its_config_and_loads_it_on_the_cpu(piper_libs):
    engine = piper.Piper(settings())
    assert engine.load() == {"device": "cpu", "sample_rate": 22050}
    base = "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx"
    assert piper_libs.hf_hub_download.calls == [("rhasspy/piper-voices", base, True), ("rhasspy/piper-voices", base + ".json", True)]  # both files, cache first
    assert FakePiperVoice.loads == [(f"/cache/rhasspy/piper-voices/{base}", False)]  # (never CUDA: it is a CPU engine)


def test_piper_loads_another_voice_from_a_local_onnx_file(piper_libs, tmp_path):
    (tmp_path / "other.onnx").write_bytes(b"x")
    with pytest.raises(PipelineLoadError, match="must both be there"):
        piper.Piper(settings(model=str(tmp_path / "other.onnx"))).load()
    (tmp_path / "other.onnx.json").write_text("{}")
    piper.Piper(settings(model=str(tmp_path / "other.onnx"))).load()
    assert FakePiperVoice.loads == [(str(tmp_path / "other.onnx"), False)] and piper_libs.hf_hub_download.calls == []


def test_piper_stays_offline_when_told_and_says_what_is_missing(piper_libs):
    piper_libs.hf_hub_download = Hub(cached=set())
    with pytest.raises(PipelineLoadError, match="not in the local cache"):
        piper.Piper(settings(hub_mode="offline")).load()


def test_piper_joins_the_sentences_it_gives_and_reports_the_rate_it_made(piper_libs):
    engine = piper.Piper(settings())
    engine.load()
    speech = engine.synthesize(request("Two sentences. Here.", voice="jenny_dioco", seed=1), NEVER)
    assert speech.pcm == b"\x01\x00\x02\x00\x03\x00" and speech.sample_rate == 22050 and engine._voice.texts == ["Two sentences. Here."]


def test_piper_trusts_the_rate_the_audio_has_and_not_the_one_it_expected(piper_libs):
    FakePiperVoice.rate = 16000  # the audio says 16 kHz; the voice's config file (and the engine's own figure) say 22,050
    engine = piper.Piper(settings())
    assert engine.load()["sample_rate"] == 22050
    assert engine.synthesize(request(voice="jenny_dioco"), NEVER).sample_rate == 16000


def test_piper_can_be_cancelled_between_its_sentences(piper_libs):
    engine = piper.Piper(settings())
    engine.load()
    asked = []

    def after_first_sentence() -> bool:
        asked.append(1)
        return len(asked) >= 2

    with pytest.raises(Canceled):
        engine.synthesize(request(voice="jenny_dioco"), after_first_sentence)


@pytest.mark.parametrize("channels,width", [(2, 2), (1, 4)])
def test_piper_refuses_audio_that_is_not_16_bit_mono(piper_libs, channels, width):
    FakePiperVoice.channels, FakePiperVoice.width = channels, width
    engine = piper.Piper(settings())
    engine.load()
    with pytest.raises(PipelineError, match="16-bit mono"):
        engine.synthesize(request(voice="jenny_dioco"), NEVER)


def test_piper_reports_a_piece_that_made_no_audio_and_a_voice_it_does_not_have(piper_libs):
    FakePiperVoice.sentences = []
    engine = piper.Piper(settings())
    engine.load()
    with pytest.raises(PipelineError, match="no audio"):
        engine.synthesize(request("...", voice="jenny_dioco"), NEVER)
    with pytest.raises(PipelineError, match="not loaded"):
        piper.Piper(settings()).synthesize(request(voice="jenny_dioco"), NEVER)


def test_pipers_probe_needs_its_libraries_and_otherwise_runs_on_the_cpu(piper_libs, monkeypatch):
    assert piper.Piper.probe() == {"device": "cpu"}

    def missing():
        raise ImportError("No module named 'onnxruntime'")

    monkeypatch.setattr(piper, "_libs", missing)
    with pytest.raises(PipelineUnavailable, match="onnxruntime"):
        piper.Piper.probe()


# ---------------------------------------------------------------- Qwen3-TTS
class FakeQwenModel:
    def __init__(self, path: str, kwargs: dict[str, Any]):
        self.path, self.kwargs, self.calls, self.prompts = path, kwargs, [], 0

    def _wav(self):
        return [[0.25, -0.25, 0.5]], 24000

    def generate_custom_voice(self, **kw):
        self.calls.append(("custom", kw))
        return self._wav()

    def generate_voice_design(self, **kw):
        self.calls.append(("design", kw))
        return self._wav()

    def create_voice_clone_prompt(self, **kw):
        self.prompts += 1
        self.calls.append(("prompt", kw))
        return f"prompt-{self.prompts}"

    def generate_voice_clone(self, **kw):
        self.calls.append(("clone", kw))
        return self._wav()


class FakeQwenClass:
    loaded: list[FakeQwenModel] = []
    fail_local = False

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        if kwargs.get("local_files_only") and cls.fail_local:
            raise CacheMiss("not in the cache")
        model = FakeQwenModel(path, kwargs)
        cls.loaded.append(model)
        return model


@pytest.fixture
def qwen_libs(monkeypatch):
    FakeQwenClass.loaded, FakeQwenClass.fail_local = [], False
    libs = SimpleNamespace(torch=fake_torch(), Qwen3TTSModel=FakeQwenClass)
    monkeypatch.setattr(qwen3, "_libs", lambda: libs)
    return libs


def loaded_qwen(variant: str, **kw) -> qwen3.Qwen3TTS:
    engine = qwen3.Qwen3TTS(settings(variant=variant, **kw))
    engine.load()
    return engine


@pytest.mark.parametrize("variant,checkpoint", [("custom", "Qwen3-TTS-12Hz-1.7B-CustomVoice"), ("design", "Qwen3-TTS-12Hz-1.7B-VoiceDesign"), ("clone", "Qwen3-TTS-12Hz-1.7B-Base")])
def test_each_variant_loads_its_own_checkpoint_the_way_the_readme_does(qwen_libs, variant, checkpoint):
    engine = qwen3.Qwen3TTS(settings(variant=variant))
    facts = engine.load()
    model = FakeQwenClass.loaded[0]
    assert model.path == f"Qwen/{checkpoint}" and facts == {"device": "cuda:0", "checkpoint": f"Qwen/{checkpoint}"}
    assert model.kwargs == {"device_map": "cuda:0", "dtype": "bfloat16", "attn_implementation": "sdpa", "local_files_only": True}  # the README's call, with the cache first


def test_a_checkpoint_missing_from_the_cache_is_fetched_once_and_not_when_offline(qwen_libs):
    FakeQwenClass.fail_local = True
    loaded_qwen("custom")
    assert [m.kwargs["local_files_only"] for m in FakeQwenClass.loaded] == [False]  # the first try (local) failed with a cache miss; the second went online
    FakeQwenClass.loaded = []
    with pytest.raises(PipelineLoadError, match="not in the local cache"):
        loaded_qwen("custom", hub_mode="offline")


def test_a_folder_or_namespace_set_by_the_person_holds_the_three_checkpoints(qwen_libs, tmp_path):
    loaded_qwen("clone", model=str(tmp_path))  # a folder
    assert FakeQwenClass.loaded[-1].path == str(tmp_path / "Qwen3-TTS-12Hz-1.7B-Base")
    loaded_qwen("design", model="my-org/")  # a namespace on the hub
    assert FakeQwenClass.loaded[-1].path == "my-org/Qwen3-TTS-12Hz-1.7B-VoiceDesign"


def test_a_variant_that_does_not_exist_is_refused_at_load(qwen_libs):
    with pytest.raises(PipelineLoadError, match="no variant"):
        qwen3.Qwen3TTS(settings(variant="base")).load()


def test_a_preset_voice_is_spoken_with_its_name_the_style_and_a_seeded_generator(qwen_libs):
    engine = loaded_qwen("custom")
    speech = engine.synthesize(request("Hello there.", voice="Aiden", style="calm and warm", language="English", seed=42), NEVER)
    assert speech.sample_rate == 24000 and pcm_samples(speech.pcm) == [8192, -8192, 16384]
    assert FakeQwenClass.loaded[0].calls == [("custom", {"text": "Hello there.", "speaker": "Aiden", "instruct": "calm and warm", "language": "English", "max_new_tokens": 1536})]
    assert qwen_libs.torch.seeds == [42]  # the library takes no seed, so torch is seeded before the piece


def test_no_style_and_no_language_are_none_so_the_library_chooses(qwen_libs):
    engine = loaded_qwen("custom")
    engine.synthesize(request(voice="Ryan", style="", language=None), NEVER)
    call = FakeQwenClass.loaded[0].calls[0][1]
    assert call["instruct"] is None and call["language"] is None  # "Auto" is the library's answer to a language of None


def test_a_designed_voice_is_invented_from_the_description(qwen_libs):
    engine = loaded_qwen("design")
    engine.synthesize(request("The passage.", design="a warm Irish woman in her forties", seed=3), NEVER)
    assert FakeQwenClass.loaded[0].calls == [("design", {"text": "The passage.", "instruct": "a warm Irish woman in her forties", "language": None, "max_new_tokens": 1536})]
    with pytest.raises(PipelineError, match="needs a description"):
        engine.synthesize(request("The passage."), NEVER)


def test_a_saved_voice_is_spoken_from_a_prompt_built_once_from_its_clip_and_its_words(qwen_libs):
    engine = loaded_qwen("clone")
    mine, other = VoiceRef("/data/speech/voices/a/reference.wav", "the words said", "cloned"), VoiceRef("/data/speech/voices/b/reference.wav", "other words", "designed")
    for text in ("one", "two", "three"):
        engine.synthesize(request(text, ref=mine, seed=1), NEVER)
    engine.synthesize(request("four", ref=other), NEVER)
    model = FakeQwenClass.loaded[0]
    prompts = [kw for kind, kw in model.calls if kind == "prompt"]
    assert prompts == [{"ref_audio": mine.path, "ref_text": "the words said", "x_vector_only_mode": False},
                       {"ref_audio": other.path, "ref_text": "other words", "x_vector_only_mode": False}]  # one for each saved voice, however many pieces
    clones = [kw for kind, kw in model.calls if kind == "clone"]
    assert [c["voice_clone_prompt"] for c in clones] == ["prompt-1", "prompt-1", "prompt-1", "prompt-2"] and all("style" not in c and "instruct" not in c for c in clones)
    with pytest.raises(PipelineError, match="needs its reference clip"):
        engine.synthesize(request("no clip"), NEVER)


def test_a_voice_the_checkpoint_does_not_have_is_refused_before_the_library_is_asked(qwen_libs):
    engine = loaded_qwen("custom")
    for voice in ("", "Nobody", "aiden"):  # (the names are as the README spells them; the library lower-cases them itself, but the studio's list is the list)
        with pytest.raises(PipelineError, match="no voice called"):
            engine.synthesize(request(voice=voice), NEVER)
    assert FakeQwenClass.loaded[0].calls == []


def test_qwen_errors_while_speaking_are_worded_and_cancel_is_honoured_between_pieces(qwen_libs):
    engine = loaded_qwen("custom")
    with pytest.raises(Canceled):
        engine.synthesize(request(voice="Ryan"), lambda: True)

    def explode(**kw):
        raise ValueError("Unsupported languages: ['klingon']")

    engine._model.generate_custom_voice = explode
    with pytest.raises(PipelineError, match="Qwen3-TTS could not speak this paragraph: ValueError: Unsupported languages") as caught:
        engine.synthesize(request(voice="Ryan", language="klingon"), NEVER)
    assert caught.value.hint
    engine._model.generate_custom_voice = lambda **kw: ([], 24000)
    with pytest.raises(PipelineError, match="no audio"):
        engine.synthesize(request(voice="Ryan"), NEVER)
    with pytest.raises(PipelineError, match="not loaded"):
        qwen3.Qwen3TTS(settings(variant="custom")).synthesize(request(voice="Ryan"), NEVER)


def test_unloading_lets_go_of_the_model_and_the_prompts(qwen_libs):
    engine = loaded_qwen("clone")
    engine.synthesize(request(ref=VoiceRef("/x.wav", "words", "cloned")), NEVER)
    engine.unload()
    assert engine._model is None and engine._prompts == {}
    with pytest.raises(PipelineError, match="not loaded"):
        engine.synthesize(request(ref=VoiceRef("/x.wav", "words", "cloned")), NEVER)


def test_qwens_probe_needs_the_library_and_a_gpu(qwen_libs):
    assert qwen3.Qwen3TTS.probe() == {"device": "cuda"}
    qwen_libs.torch = fake_torch(cuda=False)
    with pytest.raises(PipelineUnavailable, match="needs a GPU") as caught:
        qwen3.Qwen3TTS.probe()
    assert "gpus" in caught.value.hint


def test_the_wav_of_a_real_engines_piece_is_one_the_studio_can_store(qwen_libs, tmp_path):
    """What an engine returns goes through the same path as the fake engines' audio: written as a WAV by the worker, read back by the API."""
    from studio import wavfile

    speech = loaded_qwen("custom").synthesize(request(voice="Ryan"), NEVER)
    path = tmp_path / "piece.wav"
    wavfile.write_wav(path, speech.pcm, speech.sample_rate, channels=1)
    info = wavfile.read_wav(path)
    assert (info.channels, info.sample_rate, info.frames) == (1, 24000, 3)
