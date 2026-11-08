"""Recording produces a thread-local draft before any chat turn is sent."""

from app.agent.speech import SpeechService
from tests.test_chat_integration import payload, task
from tests.test_speech_service import wav
from tests.test_streamlit_chat_integration import screen


def test_recording_is_reviewed_before_agent_turn(monkeypatch):
    app, client, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    monkeypatch.setattr(
        SpeechService,
        "from_env",
        classmethod(lambda cls: SpeechService(_Transcriber())),
    )
    app.session_state["pending_speech"] = {
        "thread_id": 1,
        "audio": wav(),
        "typed_text": "Please check:",
    }
    app.run(timeout=20)
    assert not app.exception
    thread = app.session_state["threads"][0]
    assert thread["speech_draft"] == "Please check:\nORD-057 status"
    assert thread["messages"] == []
    assert client.calls == []
    assert executor.calls == []
    app.session_state["speech_draft_1"] = "Check ORD-057"
    app.button(key="send_speech_1").click().run(timeout=20)
    thread = app.session_state["threads"][0]
    assert "speech_draft" not in thread
    assert thread["messages"][0]["content"] == "Check ORD-057"
    assert len(executor.calls) == 1
    app.run(timeout=20)
    assert len(executor.calls) == 1


def test_discard_never_calls_agent(monkeypatch):
    app, client, executor = screen(monkeypatch)
    app.session_state["threads"][0]["speech_draft"] = "Cancel ORD-057"
    app.session_state["speech_draft_1"] = "Cancel ORD-057"
    app.run(timeout=20)
    app.button(key="discard_speech_1").click().run(timeout=20)
    assert "speech_draft" not in app.session_state["threads"][0]
    assert not client.calls
    assert not executor.calls


def test_completed_recording_stays_with_original_chat(monkeypatch):
    app, client, executor = screen(monkeypatch)
    monkeypatch.setattr(
        SpeechService,
        "from_env",
        classmethod(lambda cls: SpeechService(_Transcriber())),
    )
    app.session_state["threads"].insert(0, {"id": 2, "title": "Other", "messages": []})
    app.session_state["active_thread"] = 2
    app.session_state["pending_speech"] = {
        "thread_id": 1,
        "audio": wav(),
        "typed_text": "",
    }
    app.run(timeout=20)
    threads = {thread["id"]: thread for thread in app.session_state["threads"]}
    assert threads[1]["speech_draft"] == "ORD-057 status"
    assert "speech_draft" not in threads[2]
    assert not client.calls
    assert not executor.calls


class _Transcriber:
    def transcribe(self, audio):
        return "ORD-057 status"
