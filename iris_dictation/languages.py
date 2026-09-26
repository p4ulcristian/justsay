"""Hold Whisper to a short list of languages.

onnx-asr can either force one language or let Whisper pick from all 99. On a
one second clip the free pick is a coin toss and Russian comes up. This
replaces the pick with the most likely language out of an allowed set.

Reaches into onnx-asr's Whisper internals, so it is tied to the version
pinned in requirements.txt.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("iris-dictation")


def restrict(model, languages: list[str]) -> None:
    """Patch a loaded onnx-asr Whisper model to only pick from `languages`."""
    asr = model.asr
    if not hasattr(asr, "_decode"):
        log.info("model has no language detection, ignoring languages=%s", languages)
        return
    if len(languages) == 1:
        # One language: plain forcing, no detection pass at all.
        original = asr.recognize_batch
        asr.recognize_batch = lambda w, wl, /, **kw: original(
            w, wl, **{**kw, "language": languages[0]})
        return

    ids = np.array([asr._tokens[f"<|{lang}|>"] for lang in languages])

    def recognize_batch(waveforms, waveforms_len, /, **kwargs):
        encoding = asr._encode(waveforms, waveforms_len)
        start = np.repeat(asr._detect_lang_input, len(waveforms), axis=0)
        logits, _ = asr._decode(start, asr._create_state(), encoding)
        picked = ids[logits[:, -1, ids].argmax(axis=-1)]
        log.debug("language: %s", [asr._vocab[i] for i in picked])
        tokens = np.repeat(asr._transcribe_input, len(waveforms), axis=0)
        tokens[:, 1] = picked
        return map(asr._decode_tokens, asr._decoding(encoding, tokens))

    asr.recognize_batch = recognize_batch
