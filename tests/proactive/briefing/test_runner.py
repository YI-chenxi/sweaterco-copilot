"""Tests for the standalone scheduled briefing runner."""

import json
import sqlite3

from app.agent.proactive.briefing.runner import main


def test_runner_requires_database(monkeypatch, capsys):
    """Missing database configuration returns exit code 2."""
    monkeypatch.delenv("BRIEFING_DB_PATH", raising=False)

    exit_code = main([])

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "BRIEFING_DB_PATH" in captured.err


def test_runner_empty_database(tmp_path, capsys):
    """Runner initializes SQLite and handles no due tasks."""
    database = tmp_path / "briefings.db"

    exit_code = main(["--database", str(database)])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == []

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "briefing_schedules",
        "briefing_runs",
        "briefing_claims",
    }.issubset(tables)
