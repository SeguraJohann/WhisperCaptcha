import asyncio
import os
import string
import tempfile

import whisper as whisper_lib

_model = None


async def load(model_name: str) -> None:
    await asyncio.to_thread(_load_model, model_name)


async def transcribe(audio: bytes) -> str:
    return await asyncio.to_thread(_run_transcription, audio)


def _load_model(model_name: str) -> None:
    global _model
    _model = whisper_lib.load_model(model_name)


def _run_transcription(audio: bytes) -> str:
    if _model is None:
        raise RuntimeError("Transcriber not loaded")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        tmp_path = f.name
    try:
        result = _model.transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)
    return _clean(result["text"])


def _clean(text: str) -> str:
    return text.strip().lower().translate(str.maketrans("", "", string.punctuation))
