"""Translation transport and UI integration use mock clients, never real API calls."""

import json
from copy import deepcopy
from html import escape
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent.contracts import AgentReply, Evidence
from app.agent.settings import LLMSettings
from app.ui.history_translation import HistoryTranslator, cache_key, display, mask, prepare, restore
from tests.test_streamlit_chat_integration import screen as chat_screen
from tests.test_streamlit_chat_integration import send
from tests.test_ui_evidence_selection import execute
from tests.ui_events import analysis_copy_payloads

pytest_plugins = ["tests.test_ui_workflow_language"]


def test_sdk_uses_existing_model_structured_output_and_protects_values():
    client = Mock()

    def respond(**kwargs):
        texts = json.loads(kwargs["input"][1]["content"])
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps(
                {
                    "translations": [
                        s.replace("Order", "订单").replace("requires", "需要") for s in texts
                    ]
                }
            ),
        )

    client.responses.create.side_effect = respond
    translator = HistoryTranslator(LLMSettings(provider="openai", model="configured-model"), client)
    text = "Order ORD-057 requires 800 TOPS by 2026-04-25; margin -45.1%."
    result = translator.translate([text], "zh")[0]
    assert "ORD-057" in result and "800 TOPS" in result and "2026-04-25" in result
    assert "-45.1%" in result
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["model"] == "configured-model"
    assert kwargs["store"] is False
    assert "tools" not in kwargs
    assert kwargs["text"]["format"]["strict"] is True
    assert "untrusted" in kwargs["input"][0]["content"]


def test_missing_protected_value_is_rejected():
    _, prefix, saved = mask("Order ORD-057: 800 pieces")
    with pytest.raises(ValueError):
        restore("订单", prefix, saved)


def test_dedup_cache_failure_and_explicit_retry():
    translator = Mock()
    translator.translate.return_value = ["历史问题"]
    cache = {}
    prepare(["Historical question"] * 3, "zh", cache, translator)
    prepare(["Historical question"], "zh", cache, translator)
    assert translator.translate.call_count == 1
    assert display("Historical question", "zh", cache) == "历史问题"
    translator.translate.side_effect = RuntimeError("sensitive provider error")
    prepare(["Another question"], "zh", cache, translator)
    prepare(["Another question"], "zh", cache, translator)
    assert translator.translate.call_count == 2
    assert display("Another question", "zh", cache) == "翻译暂不可用"
    del cache[cache_key("Another question", "zh")]
    translator.translate.side_effect = None
    translator.translate.return_value = ["另一问题"]
    prepare(["Another question"], "zh", cache, translator)
    assert display("Another question", "zh", cache) == "另一问题"


def test_long_text_is_complete_and_bounded():
    translator = Mock()
    translator.translate.side_effect = lambda texts, target: texts
    text = "long question " * 3000
    cache = {}
    prepare([text], "zh", cache, translator)
    assert display(text, "zh", cache) == text
    assert all(len(t) <= 4000 for call in translator.translate.call_args_list for t in call.args[0])


def test_settings_loader_never_reads_dotenv(monkeypatch):
    load = Mock(return_value=LLMSettings())
    monkeypatch.setattr(LLMSettings, "from_env", load)
    with pytest.raises(ValueError):
        HistoryTranslator()
    load.assert_called_once_with(load_dotenv_file=False)


def test_reply_translation_preserves_user_text_selection_and_cache(screen, monkeypatch):
    from app.ui import history_translation

    app, guided, service = screen
    guided.side_effect = None
    guided.return_value = AgentReply(
        "Review the capacity constraint.",
        warnings=["Capacity requires review."],
        assumptions=["No additional overtime."],
        evidence=[Evidence("orders.csv", "ORD-057", "Recorded order detail.")],
        metadata={"language": "en", "intent": "unknown"},
    )
    execute(app)
    thread = app.session_state["threads"][0]
    thread["messages"][0] = {"role": "user", "content": "What needs attention?"}
    thread["title"] = "Capacity discussion"
    snapshot = deepcopy(thread["messages"])
    identity = thread["selected_reply_id"]
    translations = {
        "Review the capacity constraint.": "请查看产能限制。",
        "Capacity requires review.": "产能需要复核。",
        "No additional overtime.": "没有额外加班。",
        "Recorded order detail.": "已记录的订单详情。",
    }
    translator = Mock()
    translator.translate.side_effect = lambda texts, target: [translations[t] for t in texts]
    monkeypatch.setattr(history_translation, "HistoryTranslator", Mock(return_value=translator))
    monkeypatch.setattr(
        LLMSettings,
        "from_env",
        Mock(return_value=SimpleNamespace(provider="openai", api_key="test-only")),
    )
    app.selectbox(key="ui_language").set_value("zh").run()
    app.button(key="full_" + identity).click().run()
    assert not app.exception
    assert any(m.value == "请查看产能限制。" for m in app.markdown)
    assert "What needs attention?" in app.chat_message[0].get("markdown")[0].value
    assert all(
        "What needs attention?" not in call.args[0] for call in translator.translate.call_args_list
    )
    assert analysis_copy_payloads(app) == {identity: {"text": "请查看产能限制。", "language": "zh"}}
    assert any(w.value == "产能需要复核。" for w in app.warning)
    count = translator.translate.call_count
    for language in ("en", "zh"):
        app.selectbox(key="ui_language").set_value(language).run()
        assert "What needs attention?" in app.chat_message[0].get("markdown")[0].value
    assert translator.translate.call_count == count
    assert thread["messages"] == snapshot
    assert thread["selected_reply_id"] == identity
    assert thread["expanded_answers"][identity]
    assert guided.call_count == 1
    service.assert_not_called()


@pytest.mark.parametrize(
    ("initial_language", "submitted"),
    [
        ("en", "  请不要取消 ORD-057。\n请保留 800 件，交期 2026-04-25。  "),
        ("zh", "  Do not cancel ORD-057.\nKeep **800 TOPS** by 2026-04-25 <check>.  "),
    ],
)
def test_keyboard_message_preserves_exact_text_across_language_switches(
    monkeypatch, initial_language, submitted
):
    from app.ui import history_translation

    translator = Mock()
    translator.translate.side_effect = lambda texts, target: texts
    monkeypatch.setattr(history_translation, "HistoryTranslator", Mock(return_value=translator))
    app, _, _ = chat_screen(monkeypatch)
    app.selectbox(key="ui_language").set_value(initial_language).run()
    send(app, submitted)
    original = deepcopy(app.session_state["threads"][0]["messages"])
    assert original[0]["content"] == submitted

    for language in ("en", "zh"):
        app.selectbox(key="ui_language").set_value(language).run()
        assert not app.exception
        assert escape(submitted) in app.chat_message[0].get("markdown")[0].value
        assert app.session_state["threads"][0]["messages"] == original
    assert all(
        text.strip() != submitted.strip()
        for call in translator.translate.call_args_list
        for text in call.args[0]
    )
