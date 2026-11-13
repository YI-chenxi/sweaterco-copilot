"""Speech fills the native composer; only normal submit starts an Agent turn."""

from html import escape

import pytest

from app.agent.speech import SpeechError, SpeechService
from app.ui.composer_draft import apply_draft_event
from tests.test_chat_integration import payload, task
from tests.test_speech_service import wav
from tests.test_streamlit_chat_integration import screen, send


class _Transcriber:
    def __init__(self):
        self.calls = 0
        self.result = "ORD-057 status"

    def transcribe(self, audio):
        self.calls += 1
        if isinstance(self.result, SpeechError):
            raise self.result
        return self.result


@pytest.fixture
def transcriber(monkeypatch):
    fake = _Transcriber()
    monkeypatch.setattr(SpeechService, "from_env", classmethod(lambda cls: SpeechService(fake)))
    return fake


def _thread(app, identifier=1):
    return next(t for t in app.session_state["threads"] if t["id"] == identifier)


def _submit(app, recording_id, *, thread_id=1, typed_text=""):
    app.session_state["pending_speech"] = {
        "id": recording_id,
        "thread_id": thread_id,
        "audio": wav(),
        "typed_text": typed_text,
    }
    app.run(timeout=20)
    assert not app.exception


def _edit(app, text, identifier=1):
    thread = _thread(app, identifier)
    apply_draft_event(
        thread,
        {
            "thread": identifier,
            "token": thread["composer_token"],
            "sequence": thread.get("composer_sequence", 0) + 1,
            "text": text,
        },
    )


def test_recording_fills_original_input_and_normal_send_runs_once(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    _submit(app, "first", typed_text="Please check:")
    assert app.chat_input[0].proto.value == "Please check:\nORD-057 status"
    assert app.chat_input[0].proto.set_value
    assert not app.text_area and not client.calls and not executor.calls
    assert not _thread(app)["messages"]
    removed = {"Re-record", "Send reviewed text", "Discard transcription"}
    assert not any(b.label in removed for b in app.button)
    send(app, "Check ORD-057")
    assert _thread(app)["messages"][0]["content"] == "Check ORD-057"
    assert not _thread(app).get("speech_draft") and len(executor.calls) == 1
    _submit(app, "first")
    assert transcriber.calls == 1 and len(executor.calls) == 1
    assert not app.chat_input[0].proto.set_value


def test_deleting_input_does_not_resurrect_old_recording(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch)
    _submit(app, "first")
    _edit(app, "")
    assert not _thread(app).get("speech_draft")
    app.button(key="new_chat").click().run(timeout=20)
    assert len(app.session_state["threads"]) == 1
    _submit(app, "first")
    assert _thread(app)["composer_draft"] == ""
    assert transcriber.calls == 1 and not client.calls and not executor.calls


def test_second_recording_uses_current_edited_input(monkeypatch, transcriber):
    app, client, _ = screen(monkeypatch)
    _submit(app, "first")
    _edit(app, "Manually edited opening")
    transcriber.result = "Additional spoken text"
    _submit(app, "second", typed_text="Manually edited opening")
    assert app.chat_input[0].proto.value == "Manually edited opening\nAdditional spoken text"
    _edit(app, "")
    transcriber.result = "Replacement after manual deletion"
    _submit(app, "third")
    assert app.chat_input[0].proto.value == "Replacement after manual deletion"
    assert not client.calls and transcriber.calls == 3


def test_unsent_edits_stay_in_original_conversation(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch)
    _submit(app, "first")
    _edit(app, "Edited original draft")
    app.button(key="new_chat").click().run(timeout=20)
    assert app.chat_input[0].proto.value == ""
    assert _thread(app, 2).get("composer_draft", "") == ""
    app.button(key="thread_1").click().run(timeout=20)
    assert app.chat_input[0].proto.value == "Edited original draft"
    assert not client.calls and not executor.calls and transcriber.calls == 1


@pytest.mark.parametrize("code", ["empty_audio", "invalid_audio", "timeout", "unavailable"])
def test_failed_transcription_keeps_input_and_never_executes(monkeypatch, transcriber, code):
    app, client, executor = screen(monkeypatch)
    transcriber.result = SpeechError(code)
    _submit(app, "failed", typed_text="Keep my typed words")
    assert app.chat_input[0].proto.value == "Keep my typed words"
    assert app.warning and not app.text_area
    assert not client.calls and not executor.calls
    app.session_state["ui_language"] = "zh"
    app.run(timeout=20)
    assert app.warning and not app.exception


def test_submitted_voice_text_stays_exact_when_language_changes(monkeypatch, transcriber):
    app, _, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    _submit(app, "first")
    approved = "  查询 ORD-057 <keep literal>\nKeep 800 TOPS by 2026-04-25.  "
    send(app, approved)
    for language in ("zh", "en"):
        app.selectbox(key="ui_language").set_value(language).run(timeout=20)
        assert _thread(app)["messages"][0]["content"] == approved
        assert any(escape(approved) in m.value for m in app.chat_message[0].markdown)
    assert len(executor.calls) == 1 and transcriber.calls == 1


@pytest.mark.parametrize(
    "draft,expected",
    [
        ("Old transcript", "Old transcript"),
        ({"id": "old", "text": "Legacy edited words"}, "Legacy edited words"),
        ({"text": "Recovered words"}, "Recovered words"),
    ],
)
def test_old_draft_migrates_into_native_composer(monkeypatch, transcriber, draft, expected):
    app, client, _ = screen(monkeypatch)
    _thread(app).pop("composer_draft", None)
    _thread(app)["speech_draft"] = draft
    app.run(timeout=20)
    assert not app.exception and app.chat_input[0].proto.value == expected
    assert not app.text_area and not client.calls and transcriber.calls == 0


def test_late_input_event_cannot_replace_new_transcription(monkeypatch, transcriber):
    app, _, _ = screen(monkeypatch)
    old_token = _thread(app)["composer_token"]
    _submit(app, "first")
    assert not apply_draft_event(
        _thread(app),
        {
            "thread": 1,
            "token": old_token,
            "sequence": 999,
            "text": "stale old input",
        },
    )
    assert _thread(app)["composer_draft"] == "ORD-057 status"
