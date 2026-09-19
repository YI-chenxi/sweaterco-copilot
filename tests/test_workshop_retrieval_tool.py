from app.tools.workshop_retrieval_tool import WorkshopRetrievalTool


def test_retrieve_factory_workshop_by_id():
    tool = WorkshopRetrievalTool()

    result = tool.execute(workshop_id="W5")

    assert result.data["count"] == 1
    assert result.data["workshops"][0]["workshop_id"] == "W5"
    assert len(result.evidence) == 1


def test_retrieve_active_factory_workshops():
    tool = WorkshopRetrievalTool()

    result = tool.execute(status="ACTIVE")

    assert result.data["count"] == 7
    assert all(
        workshop["status"] == "ACTIVE"
        for workshop in result.data["workshops"]
    )


def test_retrieve_workshops_by_category():
    tool = WorkshopRetrievalTool()

    result = tool.execute(category="ACCESSORIES")

    assert result.data["count"] > 0
    assert all(
        "ACCESSORIES" in workshop["makes"].split("+")
        for workshop in result.data["workshops"]
    )


def test_retrieve_workshops_with_multiple_filters():
    tool = WorkshopRetrievalTool()

    result = tool.execute(
        status="ACTIVE",
        category="ACCESSORIES",
    )

    assert result.data["count"] > 0
    assert all(
        workshop["status"] == "ACTIVE"
        and "ACCESSORIES" in workshop["makes"].split("+")
        for workshop in result.data["workshops"]
    )


def test_retrieve_factory_workshops_handles_case_and_spaces():
    tool = WorkshopRetrievalTool()

    result = tool.execute(
        status=" active ",
        category=" accessories ",
    )

    assert result.data["count"] > 0
    assert all(
        workshop["status"] == "ACTIVE"
        for workshop in result.data["workshops"]
    )


def test_retrieve_factory_workshops_no_match():
    tool = WorkshopRetrievalTool()

    result = tool.execute(workshop_id="W999")

    assert result.data["count"] == 0
    assert result.data["workshops"] == []
    assert result.evidence == []
    assert result.warnings


def test_retrieve_factory_workshops_requires_filter():
    tool = WorkshopRetrievalTool()

    result = tool.execute()

    assert result.data["count"] == 0
    assert result.data["workshops"] == []
    assert result.evidence == []
    assert result.warnings == [
        "At least one workshop search filter must be provided."
    ]

def test_retrieve_factory_workshops_returns_typed_data():
    tool = WorkshopRetrievalTool()

    result = tool.execute(workshop_id="W8")

    workshop = result.data["workshops"][0]

    assert isinstance(workshop["capacity_pieces_per_day"], int)
    assert isinstance(workshop["pickup_lead_days"], int)
    assert isinstance(workshop["defect_rate"], float)
    assert isinstance(workshop["cost_per_piece"], float)
    assert isinstance(workshop["current_queue_days"], float)
    assert isinstance(workshop["max_batch_pieces"], int)

def test_retrieve_factory_workshops_converts_missing_batch_limit_to_none():
    tool = WorkshopRetrievalTool()

    result = tool.execute(workshop_id="W1")

    workshop = result.data["workshops"][0]

    assert workshop["max_batch_pieces"] is None