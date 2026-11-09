"""Recording produces a thread-local draft before any chat turn is sent."""

from html import escape
from unittest.mock import Mock

import pytest

from app.agent.speech import SpeechError, SpeechService
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


def _recording(submission_id, *, thread_id=1, typed_text="", replace_draft_id=None):
    return {
        "id": submission_id,
        "thread_id": thread_id,
        "audio": wav(),
        "typed_text": typed_text,
        "replace_draft_id": replace_draft_id,
    }


def _submit(app, submission_id, **kwargs):
    app.session_state["pending_speech"] = _recording(submission_id, **kwargs)
    app.run(timeout=20)
    assert not app.exception


def _draft(app, thread_id=1):
    thread = next(thread for thread in app.session_state["threads"] if thread["id"] == thread_id)
    return thread.get("speech_draft")


def test_recording_is_reviewed_before_agent_turn(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    _submit(app, "first", typed_text="Please check:")
    assert _draft(app)["text"] == "Please check:\nORD-057 status"
    assert app.session_state["threads"][0]["messages"] == []
    assert client.calls == []
    assert executor.calls == []

    app.text_area(key="speech_draft_1_first").set_value("Check ORD-057")
    app.button(key="send_speech_1_first").click().run(timeout=20)
    assert not app.exception
    thread = app.session_state["threads"][0]
    assert _draft(app) is None
    assert thread["messages"][0]["content"] == "Check ORD-057"
    assert len(executor.calls) == 1

    _submit(app, "first")
    assert transcriber.calls == 1
    assert len(executor.calls) == 1
    assert _draft(app) is None
    assert not app.text_area


def test_discard_removes_draft_and_a_replayed_recording_cannot_restore_it(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch)
    _submit(app, "first")
    app.button(key="discard_speech_1_first").click().run(timeout=20)
    _submit(app, "first")
    assert _draft(app) is None
    assert transcriber.calls == 1
    assert not app.text_area
    assert not client.calls
    assert not executor.calls


def test_completed_recording_and_edited_text_stay_with_original_chat(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch)
    app.button(key="new_chat").click().run(timeout=20)
    _submit(app, "first")
    assert _draft(app)["text"] == "ORD-057 status"
    assert _draft(app, 2) is None
    assert not app.text_area

    app.button(key="thread_1").click().run(timeout=20)
    app.text_area(key="speech_draft_1_first").set_value("Edited in the original chat")
    app.button(key="thread_2").click().run(timeout=20)
    assert not app.exception
    assert not app.text_area
    app.button(key="thread_1").click().run(timeout=20)
    assert not app.exception
    assert app.text_area(key="speech_draft_1_first").value == "Edited in the original chat"
    assert transcriber.calls == 1
    assert not client.calls
    assert not executor.calls


def test_rerecord_only_replaces_edited_draft_after_success(monkeypatch, transcriber):
    app, client, _ = screen(monkeypatch)
    _submit(app, "first")
    app.text_area(key="speech_draft_1_first").set_value("Keep this edit")
    app.button(key="rerecord_speech_1").click().run(timeout=20)
    assert any("will replace this draft" in info.value for info in app.info)

    transcriber.result = SpeechError("timeout")
    _submit(app, "retry", replace_draft_id="first")
    assert app.text_area(key="speech_draft_1_first").value == "Keep this edit"
    assert any("timed out" in warning.value for warning in app.warning)
    _submit(app, "retry", replace_draft_id="first")
    assert transcriber.calls == 2

    app.button(key="rerecord_speech_1").click().run(timeout=20)
    transcriber.result = "New transcript"
    _submit(app, "replacement", typed_text="Keep this typed text", replace_draft_id="first")
    assert app.text_area(key="speech_draft_1_replacement").value == (
        "Keep this typed text\nNew transcript"
    )
    assert not any("timed out" in warning.value for warning in app.warning)
    assert not client.calls


def test_additional_recording_preserves_existing_edits(monkeypatch, transcriber):
    app, client, _ = screen(monkeypatch)
    _submit(app, "first")
    app.text_area(key="speech_draft_1_first").set_value("Keep this edit").run(timeout=20)
    _submit(app, "additional", typed_text="More typed context")
    assert app.text_area(key="speech_draft_1_additional").value == (
        "Keep this edit\nMore typed context\nORD-057 status"
    )
    assert not client.calls


@pytest.mark.parametrize(
    ("code", "english", "chinese"),
    [
        ("empty_audio", "The recording is empty", "录音为空"),
        ("invalid_audio", "could not be read", "无法读取录音"),
        ("timeout", "timed out", "语音转写超时"),
        ("unavailable", "Transcription is unavailable", "语音转写暂不可用"),
    ],
)
def test_failure_is_localized_and_keeps_typed_text(
    monkeypatch, transcriber, code, english, chinese
):
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    if code in {"empty_audio", "invalid_audio"}:
        recording = _recording("failure", typed_text="Keep my typed text")
        recording["audio"] = b"" if code == "empty_audio" else b"invalid WAV"
        app.session_state["pending_speech"] = recording
        app.run(timeout=20)
    else:
        transcriber.result = SpeechError(code)
        _submit(app, "failure", typed_text="Keep my typed text")
    assert not app.exception
    assert any(english in warning.value for warning in app.warning)
    assert _draft(app)["text"] == "Keep my typed text"
    assert not client.calls

    app.session_state["ui_language"] = "zh"
    app.run(timeout=20)
    assert any(chinese in warning.value for warning in app.warning)
    app.button(key="new_chat").click().run(timeout=20)
    assert not any(chinese in warning.value for warning in app.warning)
    assert _draft(app, 2) is None
    app.button(key="thread_1").click().run(timeout=20)
    send(app, "Check ORD-057")
    assert len(executor.calls) == 1
    assert _draft(app)["text"] == "Keep my typed text"


def test_reviewed_mixed_language_voice_text_is_displayed_verbatim(monkeypatch, transcriber):
    from app.ui import history_translation

    transcriber.result = "请检查 ORD-057，keep 800 TOPS by 2026-04-25。"
    translator = Mock()
    translator.translate.side_effect = lambda texts, target: texts
    monkeypatch.setattr(history_translation, "HistoryTranslator", Mock(return_value=translator))
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    _submit(app, "mixed")
    assert app.text_area(key="speech_draft_1_mixed").value == transcriber.result
    assert not client.calls
    approved = "  请不要取消 ORD-057。\nKeep 800 TOPS by 2026-04-25.  "
    app.text_area(key="speech_draft_1_mixed").set_value(approved)
    app.button(key="send_speech_1_mixed").click().run(timeout=20)
    assert not app.exception

    for language in ("en", "zh", "en"):
        app.selectbox(key="ui_language").set_value(language).run(timeout=20)
        assert not app.exception
        assert app.session_state["threads"][0]["messages"][0]["content"] == approved
        assert escape(approved) in app.chat_message[0].get("html")[0].proto.body
    assert len(executor.calls) == 1
    assert transcriber.calls == 1
    assert all(
        text.strip() != approved.strip()
        for call in translator.translate.call_args_list
        for text in call.args[0]
    )


@pytest.mark.parametrize(
    ("draft", "widget_text", "expected"),
    [
        ("  原稿 ORD-057\n保留 800 件。  ", None, "  原稿 ORD-057\n保留 800 件。  "),
        (
            "Original transcript",
            "  Edited ORD-057\nDo not cancel.  ",
            "  Edited ORD-057\nDo not cancel.  ",
        ),
        ("Original transcript", 123, "Original transcript"),
        ({"text": "Recoverable draft"}, None, "Recoverable draft"),
    ],
)
def test_legacy_draft_survives_reload_with_original_text(
    monkeypatch, transcriber, draft, widget_text, expected
):
    app, client, _ = screen(monkeypatch)
    app.session_state["threads"][0]["speech_draft"] = draft
    if widget_text is not None:
        app.session_state["speech_draft_1"] = widget_text
    app.run(timeout=20)
    assert not app.exception
    draft_id = _draft(app)["id"]
    widget = f"speech_draft_1_{draft_id}"
    assert app.text_area(key=widget).value == expected
    app.run(timeout=20)
    assert not app.exception
    assert _draft(app) == {"id": draft_id, "text": expected}
    assert app.text_area(key=widget).value == expected
    assert not client.calls
    assert transcriber.calls == 0


def test_migrated_draft_stays_editable_in_its_chat_and_sends_once(monkeypatch, transcriber):
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    app.session_state["threads"][0]["speech_draft"] = "Original transcript"
    app.session_state["speech_draft_1"] = "Edited before reload"
    app.button(key="new_chat").click().run(timeout=20)
    assert not app.exception
    assert not app.text_area
    assert _draft(app, 2) is None
    assert _draft(app)["text"] == "Edited before reload"
    assert not client.calls

    draft_id = _draft(app)["id"]
    widget = f"speech_draft_1_{draft_id}"
    app.button(key="thread_1").click().run(timeout=20)
    app.text_area(key=widget).set_value("  Check ORD-057, do not cancel.  ")
    app.button(key="thread_2").click().run(timeout=20)
    assert not app.exception
    assert not app.text_area
    app.button(key="thread_1").click().run(timeout=20)
    approved = app.text_area(key=widget).value
    assert approved == "  Check ORD-057, do not cancel.  "
    app.button(key=f"send_speech_1_{draft_id}").click().run(timeout=20)
    assert not app.exception
    assert _draft(app) is None

    # A leftover widget value is not evidence of an unsent draft.
    app.session_state["speech_draft_1"] = "Edited before reload"
    app.run(timeout=20)
    assert not app.exception
    assert _draft(app) is None
    assert not app.text_area
    original_chat = next(item for item in app.session_state["threads"] if item["id"] == 1)
    assert [msg["content"] for msg in original_chat["messages"] if msg["role"] == "user"] == [
        approved
    ]
    assert len(executor.calls) == 1
    assert transcriber.calls == 0


@pytest.mark.parametrize(
    ("draft", "widget_text"),
    [
        (None, None),
        ("", None),
        ("  \n ", None),
        (123, None),
        ({"id": "bad", "text": []}, None),
        ("Original transcript", ""),
    ],
)
def test_empty_or_invalid_legacy_draft_does_not_break_rendering(
    monkeypatch, transcriber, draft, widget_text
):
    app, client, _ = screen(monkeypatch)
    app.session_state["threads"][0]["speech_draft"] = draft
    if widget_text is not None:
        app.session_state["speech_draft_1"] = widget_text
    app.run(timeout=20)
    assert not app.exception
    assert _draft(app) is None
    assert not app.text_area
    assert not client.calls
    assert transcriber.calls == 0


def test_migrated_draft_can_be_rerecorded_and_discarded(monkeypatch, transcriber):
    app, client, _ = screen(monkeypatch)
    app.session_state["threads"][0]["speech_draft"] = "Original transcript"
    app.session_state["speech_draft_1"] = "Keep my legacy edit"
    app.run(timeout=20)
    assert not app.exception
    draft_id = _draft(app)["id"]

    app.button(key="rerecord_speech_1").click().run(timeout=20)
    transcriber.result = SpeechError("timeout")
    _submit(app, "failed-replacement", replace_draft_id=draft_id)
    assert app.text_area(key=f"speech_draft_1_{draft_id}").value == "Keep my legacy edit"

    app.button(key="rerecord_speech_1").click().run(timeout=20)
    transcriber.result = "New transcript"
    _submit(app, "replacement", replace_draft_id=draft_id)
    assert app.text_area(key="speech_draft_1_replacement").value == "New transcript"
    app.button(key="discard_speech_1_replacement").click().run(timeout=20)
    _submit(app, "replacement")
    assert _draft(app) is None
    assert transcriber.calls == 2
    assert not client.calls
