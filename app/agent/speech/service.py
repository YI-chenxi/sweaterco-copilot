"""Validate a bounded WAV recording before requesting file transcription."""

import io
import os
import wave
from dataclasses import dataclass
from typing import Protocol


class SpeechError(Exception):
    """A safe, user-facing category; provider details must not reach the UI."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SpeechSettings:
    model: str = "gpt-transcribe"
    timeout_seconds: float = 30.0
    max_bytes: int = 5 * 1024 * 1024
    max_seconds: float = 60.0
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "SpeechSettings":
        from dotenv import load_dotenv

        load_dotenv()
        try:
            timeout = float(os.getenv("SPEECH_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ValueError("SPEECH_TIMEOUT_SECONDS must be numeric") from exc
        model = os.getenv("SPEECH_MODEL", "gpt-transcribe").strip()
        if not model or timeout <= 0:
            raise ValueError("SPEECH_MODEL and SPEECH_TIMEOUT_SECONDS must be valid")
        return cls(
            model=model, timeout_seconds=timeout, api_key=os.getenv("OPENAI_API_KEY") or None
        )


class Transcriber(Protocol):
    def transcribe(self, audio: bytes) -> str: ...


class OpenAITranscriber:
    def __init__(self, settings: SpeechSettings) -> None:
        if not settings.api_key:
            raise SpeechError("unavailable")
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.api_key, timeout=settings.timeout_seconds, max_retries=0
        )
        self.model = settings.model

    def transcribe(self, audio: bytes) -> str:
        try:
            response = self.client.audio.transcriptions.create(
                model=self.model, file=("recording.wav", audio, "audio/wav")
            )
            return response.text
        except Exception as exc:
            if type(exc).__name__ in {"APITimeoutError", "TimeoutException"}:
                raise SpeechError("timeout") from exc
            raise SpeechError("unavailable") from exc


class SpeechService:
    def __init__(self, transcriber: Transcriber, settings: SpeechSettings | None = None) -> None:
        self.transcriber = transcriber
        self.settings = settings or SpeechSettings()

    @classmethod
    def from_env(cls) -> "SpeechService":
        settings = SpeechSettings.from_env()
        return cls(OpenAITranscriber(settings), settings)

    def transcribe(self, audio: bytes) -> str:
        if not audio:
            raise SpeechError("empty_audio")
        if len(audio) > self.settings.max_bytes:
            raise SpeechError("too_large")
        try:
            with wave.open(io.BytesIO(audio), "rb") as recording:
                if recording.getcomptype() != "NONE" or recording.getframerate() <= 0:
                    raise SpeechError("invalid_audio")
                duration = recording.getnframes() / recording.getframerate()
                if duration <= 0:
                    raise SpeechError("empty_audio")
                if duration > self.settings.max_seconds:
                    raise SpeechError("too_long")
                # Ensure the declared frames are actually present.
                expected = (
                    recording.getnframes() * recording.getnchannels() * recording.getsampwidth()
                )
                if len(recording.readframes(recording.getnframes())) != expected:
                    raise SpeechError("invalid_audio")
        except (wave.Error, EOFError, ValueError) as exc:
            raise SpeechError("invalid_audio") from exc
        try:
            text = self.transcriber.transcribe(audio).strip()
        except SpeechError:
            raise
        except TimeoutError as exc:
            raise SpeechError("timeout") from exc
        except Exception as exc:
            raise SpeechError("unavailable") from exc
        if not text:
            raise SpeechError("no_speech")
        return text
