"""A tiny, random-weight copy of MiniMax-Music3 for testing the real music pipeline without the model or a GPU
(DESIGN.md §26.4). Every component is the real `diffusers` or `transformers` class with a few dozen dimensions instead
of thousands, so the real pipeline code (the frame loop, the windows, the vocoder) runs end to end on a CPU in a few
seconds. It makes noise, not music: it proves the plumbing around the model, not the model.

The tokenizer is a stand-in (one token per character) with the same special tokens (the pipeline hard-codes ids for a few of them, but only
needs the text to become tokens below the language model's vocabulary size)."""

from __future__ import annotations

import json
from pathlib import Path

HIDDEN = 32
VOCAB = 200_000  # the real language model's vocabulary: the pipeline hard-codes token ids up to about 168,000
SPECIAL = ["<|im_start|>", "<|im_end|>", "<|caption_start|>", "<|caption_end|>", "<|lyrics_start|>", "<|lyrics_end|>", "<|audio_start|>"]

COMPONENTS = {  # name -> (library, class)
    "condition_encoder": ("diffusers", "MiniMaxMusic3ConditionEncoder"),
    "language_model": ("transformers", "Qwen3ForCausalLM"),
    "rvq_depth_decoder": ("diffusers", "MiniMaxMusic3RVQDepthDecoder"),
    "scheduler": ("diffusers", "FlowMatchEulerDiscreteScheduler"),
    "tokenizer": ("transformers", "Qwen2Tokenizer"),
    "transformer": ("diffusers", "MiniMaxMusic3Transformer1DModel"),
    "vocoder": ("diffusers", "MiniMaxMusic3Vocoder"),
}


def build_tiny_music_repo(root: Path) -> Path:
    import torch
    from diffusers import (
        FlowMatchEulerDiscreteScheduler, MiniMaxMusic3ConditionEncoder, MiniMaxMusic3RVQDepthDecoder,
        MiniMaxMusic3Transformer1DModel, MiniMaxMusic3Vocoder,
    )
    from tokenizers import Regex, Tokenizer, models, pre_tokenizers
    from transformers import Qwen3Config, Qwen3ForCausalLM

    torch.manual_seed(0)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    language_model = Qwen3ForCausalLM(Qwen3Config(
        vocab_size=VOCAB, hidden_size=HIDDEN, intermediate_size=64, num_hidden_layers=2, num_attention_heads=4,
        num_key_value_heads=2, head_dim=8, max_position_embeddings=2048, tie_word_embeddings=False))
    language_model.save_pretrained(root / "language_model")

    MiniMaxMusic3RVQDepthDecoder(hidden_size=HIDDEN, num_layers=1, num_attention_heads=2, intermediate_size=64,
                                 audio_vocab_size=1024, num_codebooks=8, max_position_embeddings=16).save_pretrained(root / "rvq_depth_decoder")
    MiniMaxMusic3ConditionEncoder(condition_hidden_dim=HIDDEN, num_condition_layers=8, out_dim=16, input_sampling_rate=24000,
                                  input_hop_length=960, output_sampling_rate=44100, output_hop_length=512).save_pretrained(root / "condition_encoder")
    MiniMaxMusic3Transformer1DModel(in_channels=16, condition_dim=16, num_layers=2, num_attention_heads=2, attention_head_dim=8,
                                    ff_inner_dim=32, rotary_dim=4, fourier_embedding_dim=16).save_pretrained(root / "transformer")
    MiniMaxMusic3Vocoder(latent_channels=16, decoder_input_dim=8, decoder_hidden_dim=32, upsampling_ratios=(8, 8, 4, 2),
                         sampling_rate=44100).save_pretrained(root / "vocoder")
    FlowMatchEulerDiscreteScheduler(  # the real checkpoint's scheduler settings
        base_image_seq_len=256, base_shift=0.5, invert_sigmas=True, max_image_seq_len=4096, max_shift=1.15, num_train_timesteps=1,
        shift=1.0, shift_terminal=None, stochastic_sampling=False, time_shift_type="exponential", use_beta_sigmas=False,
        use_dynamic_shifting=False, use_exponential_sigmas=False, use_karras_sigmas=False).save_pretrained(root / "scheduler")

    folder = root / "tokenizer"
    folder.mkdir(exist_ok=True)
    vocab = {"<unk>": 0, **{chr(code): code - 31 for code in range(32, 127)}}  # one token per character: different text, different ids
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex("."), behavior="isolated")
    tokenizer.add_special_tokens(SPECIAL)
    tokenizer.save(str(folder / "tokenizer.json"))
    (folder / "tokenizer_config.json").write_text(json.dumps({"tokenizer_class": "Qwen2Tokenizer", "unk_token": "<unk>"}))

    index = {"_blocks_class_name": "MiniMaxMusic3Blocks", "_class_name": "MiniMaxMusic3ModularPipeline", "_diffusers_version": "0.40.0"}
    for name, (library, cls) in COMPONENTS.items():
        index[name] = [library, cls, {"pretrained_model_name_or_path": str(root), "revision": None, "subfolder": name,
                                      "type_hint": [library, cls], "variant": None}]
    (root / "modular_model_index.json").write_text(json.dumps(index, indent=2))
    return root
