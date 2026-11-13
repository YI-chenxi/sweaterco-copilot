"""Speech validation and provider failure boundaries, without live API calls."""

import io
import wave

import pytest

from app.agent.speech import SpeechError, SpeechService, SpeechSettings


class FakeTranscriber:
    def __init__(self, answer="检查 ORD-057"):
        self.answer = answer
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def wav(seconds=1, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\0\0" * (seconds * rate))
    return output.getvalue()


def test_valid_audio_returns_transcript():
    fake = FakeTranscriber()
    assert SpeechService(fake).transcribe(wav()) == "检查 ORD-057"
    assert fake.calls == 1


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("empty", "empty_audio"),
        ("invalid", "invalid_audio"),
        ("long", "too_long"),
        ("zero", "empty_audio"),
    ],
)
def test_invalid_audio_never_reaches_provider(case, code):
    audio = {
        "empty": lambda: b"",
        "invalid": lambda: b"not a wav",
        "long": lambda: wav(61),
        "zero": lambda: wav(0),
    }[case]()
    fake = FakeTranscriber()
    with pytest.raises(SpeechError, match=code):
        SpeechService(fake).transcribe(audio)
    assert fake.calls == 0


def test_size_limit_and_no_speech():
    fake = FakeTranscriber("  ")
    service = SpeechService(fake, SpeechSettings(max_bytes=100))
    with pytest.raises(SpeechError, match="too_large"):
        service.transcribe(wav())
    assert fake.calls == 0
    with pytest.raises(SpeechError, match="no_speech"):
        SpeechService(fake).transcribe(wav())


def test_truncated_wav_is_rejected_before_provider():
    fake = FakeTranscriber()
    with pytest.raises(SpeechError, match="invalid_audio"):
        SpeechService(fake).transcribe(wav()[:-100])
    assert fake.calls == 0


@pytest.mark.parametrize(
    "failure, code", [(TimeoutError(), "timeout"), (RuntimeError(), "unavailable")]
)
def test_provider_failure_is_sanitized(failure, code):
    with pytest.raises(SpeechError, match=code):
        SpeechService(FakeTranscriber(failure)).transcribe(wav())
