"""IBM Granite Speech (engine = "granite"): speech to text through PyTorch,
with the vocabulary as its keyword list, so names are heard right in the
first place instead of fixed afterwards.

English, French, German, Spanish, Portuguese and Japanese; no Hungarian.
Needs requirements-granite.txt and the model files (about 4.6 GB, about the
same in VRAM).
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("iris-dictation")

INSTRUCTION = "transcribe the speech with proper punctuation and capitalization."
MAX_KEYWORDS = 60


class Granite:
    def __init__(self, path: str) -> None:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        t0 = time.time()
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(path)
        self.tokenizer = self.processor.tokenizer
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            path, dtype=torch.bfloat16).to("cuda").eval()
        log.info("granite loaded in %.1fs, %.1f GB VRAM", time.time() - t0,
                 torch.cuda.memory_allocated() / 1e9)

    def transcribe(self, samples: np.ndarray, rate: int, keywords: list[str]) -> str:
        """Text for 16 kHz mono float samples, biased towards `keywords`."""
        instruction = INSTRUCTION
        if keywords:
            instruction += " Keywords: " + ", ".join(keywords[:MAX_KEYWORDS])
        chat = [{"role": "user", "content": "<|audio|>" + instruction}]
        prompt = self.tokenizer.apply_chat_template(chat, tokenize=False,
                                                    add_generation_prompt=True)
        wav = self.torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
        inputs = self.processor(prompt, wav, device="cuda", return_tensors="pt").to("cuda")
        seconds = len(samples) / rate
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, do_sample=False, num_beams=1,
                                      max_new_tokens=int(seconds * 8) + 40)
        new = out[:, inputs["input_ids"].shape[-1]:]
        return self.tokenizer.batch_decode(new, skip_special_tokens=True)[0].strip()
