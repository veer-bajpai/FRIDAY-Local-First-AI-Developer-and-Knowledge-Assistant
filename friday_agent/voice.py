"""Optional local microphone capture and speech-to-text."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel("base", compute_type="int8")


def listen(seconds: int = 8) -> str:
    try:
        import sounddevice as sd
        import numpy as np
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Voice input is optional. Install backend/requirements-agent-optional.txt to enable it."
        ) from exc
    if not 1 <= seconds <= 30:
        raise ValueError("Recording duration must be between 1 and 30 seconds.")
    audio = sd.rec(int(seconds * 16_000), samplerate=16_000, channels=1, dtype="float32")
    sd.wait()
    segments, _ = _model().transcribe(np.asarray(audio).reshape(-1))
    return " ".join(segment.text.strip() for segment in segments).strip()
