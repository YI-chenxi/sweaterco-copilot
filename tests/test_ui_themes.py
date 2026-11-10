"""Palette changes must stay local to UI state and preserve workflow results."""

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from app.ui.themes import MODES, theme_styles
from tests.test_ui_evidence_selection import execute
from tests.test_ui_workflow_language import screen as screen
from tests.ui_events import dismiss_workflow_dialog


@pytest.mark.parametrize("mode", MODES)
def test_theme_switch_retains_conversation_results_and_saved_form(screen, mode):
    app, guided, service = screen
    execute(app)
    thread = app.session_state["threads"][0]
    before = deepcopy(thread["messages"])
    original_table = app.dataframe[0].value.copy(deep=True)
    session = deepcopy(thread["agent_session"])
    selected = thread["selected_reply_id"]
    app.button(key="expand_workflows").click().run()
    app.button(key="center_Order_Status").click().run()
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-102").run()
    dismiss_workflow_dialog(app)
    app.button(key="theme_" + mode).click().run()
    assert not app.exception
    assert app.session_state["ui_theme"] == mode
    pd.testing.assert_frame_equal(app.dataframe[0].value, original_table)
    assert thread["messages"] == before
    assert thread["agent_session"] == session
    assert thread["selected_reply_id"] == selected
    app.button(key="center_Order_Status").click().run()
    assert app.text_input(key="wf_1_Order_Status_order_id").value == "ORD-102"
    guided.assert_called_once()
    service.assert_not_called()


def test_mode_labels_follow_locale_and_theme_survives_new_conversation(screen):
    app, guided, service = screen
    app.button(key="theme_night").click().run()
    app.selectbox(key="ui_language").set_value("zh").run()
    labels = {"day": "日常模式", "green": "护眼模式", "night": "黑夜模式"}
    for mode, label in labels.items():
        button = app.button(key="theme_" + mode)
        assert button.label == label
        assert button.proto.help == label
    app.button(key="new_chat").click().run()
    assert app.session_state["ui_theme"] == "night"
    assert app.session_state["ui_language"] == "zh"
    guided.assert_not_called()
    service.assert_not_called()


def test_green_uses_the_exact_approved_stylesheet():
    original = (Path(__file__).parents[1] / "app/ui/styles.css").read_text(encoding="utf-8")
    assert theme_styles(original, "green") == original
