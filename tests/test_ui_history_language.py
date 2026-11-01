"""Generated request labels and source assumptions follow the UI locale only."""

from copy import deepcopy
from html import escape

import pytest

from app.agent.contracts import AgentReply
from app.agent.response_builder import ASSUMPTION_TRANSLATIONS
from app.ui.localized_reply import localized_assumptions
from app.ui.workflow_controls import display_thread_title, display_workflow_message, workflow_title

pytest_plugins = ["tests.test_ui_workflow_language"]


@pytest.mark.parametrize(
    "flow,values",
    [
        ("Order Status", {"order_id": "ORD-057"}),
        ("Production Health", {"stage": "ASSEMBLY"}),
        ("Morning Briefing", {"top_n": 3}),
        (
            "Check Feasibility",
            {"pieces": 800, "category": "TOPS", "requested_delivery_date": "2026-04-25"},
        ),
    ],
)
def test_legacy_generated_titles_localize_in_both_directions_without_mutation(flow, values):
    following = {"role": "assistant", "origin": "Structured workflow · deterministic tools"}
    for original, target, suffix in (
        ("zh", "en", "快捷工作流"),
        ("en", "zh", "Structured workflow"),
    ):
        text = workflow_title(flow, values, language=original) + "  ·  " + suffix
        message = {"role": "user", "content": text}
        before = deepcopy(message)
        displayed = display_workflow_message(message, following, target)
        assert workflow_title(flow, values, language=target) in displayed
        assert message == before
        title = text[:36].rstrip() + ("…" if len(text) > 36 else "")
        thread = {"title": title, "messages": [message, following]}
        assert display_thread_title(thread, target).startswith(displayed[:10])
        assert thread["title"] == title


def test_arbitrary_user_text_and_custom_titles_stay_untouched():
    message = {"role": "user", "content": "生产健康 · ASSEMBLY · 快捷工作流"}
    following = {"role": "assistant", "origin": "AI Chat · contextual tasks"}
    assert display_workflow_message(message, following, "en") == message["content"]
    thread = {"title": "My chosen title", "messages": [message, following]}
    assert display_thread_title(thread, "zh") == "My chosen title"


def test_known_assumptions_translate_both_ways_without_altering_unknown_details():
    chinese = list(ASSUMPTION_TRANSLATIONS)
    english = list(ASSUMPTION_TRANSLATIONS.values())
    assert localized_assumptions(chinese, "en") == english
    assert localized_assumptions(english, "zh") == chinese
    unknown = ["Original supplier constraint: ABC-12"]
    assert localized_assumptions(unknown, "zh") == unknown
    assert chinese == list(ASSUMPTION_TRANSLATIONS)


def test_chinese_request_title_and_assumptions_follow_english_switch(screen):
    app, guided, _ = screen
    app.selectbox(key="ui_language").set_value("zh").run()
    guided.side_effect = None
    guided.return_value = AgentReply(
        "Original Chinese result",
        assumptions=list(ASSUMPTION_TRANSLATIONS),
        metadata={"language": "zh", "intent": "order_feasibility"},
    )
    app.button(key="center_Check_Feasibility").click().run()
    app.button(key="run_workflow").click().run()
    before = deepcopy(app.session_state["threads"][0]["messages"])
    identity = app.session_state["threads"][0]["selected_reply_id"]
    app.selectbox(key="ui_language").set_value("en").run()
    assert not app.exception
    assert any("Feasibility · 800 TOPS by 2026-04-25" in m.value for m in app.markdown)
    assert "Feasibility" in app.button(key="thread_1").label
    for text in ASSUMPTION_TRANSLATIONS.values():
        assert any(escape(text) in m.value for m in app.markdown)
    assert not any(e.label == "Original assumptions (unchanged)" for e in app.expander)
    assert app.session_state["threads"][0]["messages"] == before
    assert app.session_state["threads"][0]["selected_reply_id"] == identity
    assert guided.call_count == 1
