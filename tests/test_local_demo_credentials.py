"""Regression coverage for the disposable loopback-only Sprint 3 demo accounts."""

from pathlib import Path

import pytest
from streamlit import config
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "tools" / "review_accounts_voice.py"
DEMO_PASSWORD = "sweaterco"


@pytest.mark.parametrize("username", ["preview_alice", "preview_bob", "Ellie"])
def test_local_demo_account_can_sign_in(monkeypatch, tmp_path, username):
    monkeypatch.setenv("SWEATERCO_LOCAL_REVIEW", "1")
    monkeypatch.setenv("PROACTIVE_DB_PATH", str(tmp_path / "proactive.sqlite3"))
    config.set_option("server.address", "127.0.0.1")

    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    app.text_input(key="account_login_username").set_value(username)
    app.text_input(key="account_login_password").set_value(DEMO_PASSWORD)
    app.button(key="FormSubmitter:account_login-Sign in").click().run(timeout=30)

    assert not app.exception
    labels = {button.label for button in app.button}
    assert "Sign out" in labels
    assert {"AI Co-Pilot", "Morning Briefing", "Watches & Alerts", "Actions"} <= labels
    assert len(app.chat_input) == 1
