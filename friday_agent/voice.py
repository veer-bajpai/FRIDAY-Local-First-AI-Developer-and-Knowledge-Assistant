"""Optional local voice input integration."""
from __future__ import annotations


def listen(timeout: float = 5.0, phrase_timeout: float = 8.0) -> str:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise RuntimeError(
            "Voice input is unavailable. Install the optional voice dependencies."
        ) from exc

    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=min(timeout, 3.0))
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_timeout)
        return recognizer.recognize_google(audio).strip()
    except sr.WaitTimeoutError as exc:
        raise RuntimeError("No speech was detected before the timeout.") from exc
    except sr.UnknownValueError as exc:
        raise RuntimeError("Speech was not understood.") from exc
    except sr.RequestError as exc:
        raise RuntimeError(f"Speech recognition service request failed: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not access the microphone: {exc}") from exc
