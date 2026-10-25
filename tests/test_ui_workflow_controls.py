"""Regression tests for Quick Workflows state and title formatting."""
import pytest

from app.ui.workflow_controls import (
    workflow_title, select_workflow, toggle_workflow_panel,
    collapse_after_response,
)


@pytest.mark.parametrize("name,values,expected", [
    ("Order Status", {"order_id": "ORD-057"}, "Order status · ORD-057"),
    ("Production Health", {"stage": "ASSEMBLY"}, "Production health · ASSEMBLY"),
    ("Morning Briefing", {"top_n": 3}, "Morning briefing · top 3"),
    ("Check Feasibility", {
        "pieces": 800, "category": "TOPS", "requested_delivery_date": "2026-04-25"
    }, "Feasibility · 800 TOPS by 2026-04-25"),
])
def test_each_workflow_reads_only_its_own_fields(name, values, expected):
    assert workflow_title(name, values) == expected


def test_unknown_workflow_is_rejected():
    with pytest.raises(ValueError):
        workflow_title("Other", {})


def test_select_workflow_opens_panel_and_picks_form():
    state = {"workflow": None, "workflow_panel_expanded": False}
    select_workflow(state, "Check Feasibility")
    assert state == {"workflow": "Check Feasibility", "workflow_panel_expanded": True}


def test_collapse_after_reply_hides_form_but_keeps_launcher_available():
    state = {"workflow": "Order Status", "workflow_panel_expanded": True}
    collapse_after_response(state)
    assert state == {"workflow": None, "workflow_panel_expanded": False}
    toggle_workflow_panel(state)
    assert state["workflow_panel_expanded"] is True
    assert state["workflow"] is None


def test_manual_collapse_hides_active_form():
    state = {"workflow": "Production Health", "workflow_panel_expanded": True}
    toggle_workflow_panel(state)
    assert state == {"workflow": None, "workflow_panel_expanded": False}
