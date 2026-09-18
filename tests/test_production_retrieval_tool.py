from datetime import date

import pytest

from app.tools.production_retrieval_tool import ProductionRetrievalTool


def test_retrieve_factory_production_by_stage_and_date():
    tool = ProductionRetrievalTool()

    result = tool.execute(
        stage="PACKING",
        production_date=date(2026, 3, 31),
    )

    assert result.data["count"] == 1
    assert result.data["records"][0]["stage"] == "PACKING"
    assert result.data["records"][0]["date"] == "2026-03-31"
    assert result.data["records"][0]["pieces_completed"] == "657"
    assert len(result.evidence) == 1


def test_retrieve_factory_production_all_stages_on_date():
    tool = ProductionRetrievalTool()

    result = tool.execute(
        production_date=date(2026, 3, 31),
    )

    assert result.data["count"] == 4
    assert {
        record["stage"]
        for record in result.data["records"]
    } == {"KNITTING", "ASSEMBLY", "WASHING", "PACKING"}


def test_retrieve_factory_production_date_range():
    tool = ProductionRetrievalTool()

    result = tool.execute(
        stage="ASSEMBLY",
        start_date=date(2026, 3, 30),
        end_date=date(2026, 3, 31),
    )

    assert result.data["count"] == 2
    assert all(
        record["stage"] == "ASSEMBLY"
        for record in result.data["records"]
    )


def test_retrieve_factory_production_summary():
    tool = ProductionRetrievalTool()

    result = tool.execute(
        stage="PACKING",
        start_date=date(2026, 3, 30),
        end_date=date(2026, 3, 31),
    )

    assert result.data["summary"]["total_pieces"] == 1282
    assert result.data["summary"]["average_pieces"] == 641
    assert result.data["summary"]["min_pieces"] == 625
    assert result.data["summary"]["max_pieces"] == 657


def test_retrieve_factory_production_no_match():
    tool = ProductionRetrievalTool()

    result = tool.execute(
        production_date=date(2030, 1, 1),
    )

    assert result.data["count"] == 0
    assert result.data["records"] == []
    assert result.data["summary"] == {}
    assert result.evidence == []
    assert result.warnings


def test_retrieve_factory_production_requires_filter():
    tool = ProductionRetrievalTool()

    result = tool.execute()

    assert result.data["count"] == 0
    assert result.data["records"] == []
    assert result.warnings == [
        "At least one production search filter must be provided."
    ]


def test_retrieve_factory_production_rejects_conflicting_dates():
    tool = ProductionRetrievalTool()

    with pytest.raises(
        ValueError,
        match="production_date cannot be combined",
    ):
        tool.execute(
            production_date=date(2026, 3, 31),
            start_date=date(2026, 3, 30),
        )


def test_retrieve_factory_production_rejects_invalid_date_range():
    tool = ProductionRetrievalTool()

    with pytest.raises(
        ValueError,
        match="start_date must not be later",
    ):
        tool.execute(
            start_date=date(2026, 4, 1),
            end_date=date(2026, 3, 1),
        )