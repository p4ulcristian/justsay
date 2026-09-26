"""Pick the language of each clip, for Canary.

Canary has no language detection: it is told which language it hears, and
when told the wrong one it translates (English speech decoded as "hu" comes
out as Hungarian). So each clip is encoded once and then written out in every
allowed language, and the version the model is most sure of wins: its mean
token log-probability. The right language scores about -0.01 to -0.06, a
translation about -0.15 to -0.3. The encoder is the expensive part, so each
extra language costs only one more decoder pass.

Reaches into onnx-asr's NeMo AED internals, so it is tied to the version
pinned in requirements.txt.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("iris-dictation")


def install(model, languages: list[str]) -> None:
    """Patch a loaded onnx-asr Canary model to pick among `languages`."""
    asr = model.asr
    for lang in languages:
        if f"<|{lang}|>" not in asr._tokens:
            raise ValueError(f"Canary does not know the language {lang!r}")
    if len(languages) == 1:
        # One language: plain forcing, one decoder pass.
        original = asr.recognize_batch
        asr.recognize_batch = lambda w, wl, /, **kw: original(
            w, wl, **{**kw, "language": languages[0]})
        return

    def recognize_batch(waveforms, waveforms_len, /, **kwargs):
        kwargs.pop("language", None)
        encoding = asr._encode(*asr._preprocessor(waveforms, waveforms_len))
        best: list[tuple] = [()] * len(waveforms)
        for lang in languages:
            for i, (ids, _, logprobs) in enumerate(asr._decoding(*encoding, language=lang, **kwargs)):
                logprobs = np.asarray(logprobs)
                score = float(logprobs.mean()) if logprobs.size else -np.inf
                if not best[i] or score > best[i][0]:
                    best[i] = (score, lang, ids, logprobs)
        log.debug("language: %s", [(b[1], round(b[0], 3)) for b in best])
        return (asr._decode_tokens(ids, None, logprobs) for _, _, ids, logprobs in best)

    asr.recognize_batch = recognize_batch
