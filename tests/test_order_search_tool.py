from datetime import date

from app.tools.order_search_tool import OrderSearchTool


def test_search_factory_orders_by_customer():
    tool = OrderSearchTool()

    result = tool.execute(customer="TrendCart")

    assert result.data["count"] > 0
    assert all(
        order["customer"] == "TrendCart"
        for order in result.data["orders"]
    )
    assert len(result.evidence) == result.data["count"]


def test_search_factory_orders_with_multiple_filters():
    tool = OrderSearchTool()

    result = tool.execute(
        status="IN_PROGRESS",
        current_stage="ASSEMBLY",
    )

    assert result.data["count"] > 0
    assert all(
        order["status"] == "IN_PROGRESS"
        and order["current_stage"] == "ASSEMBLY"
        for order in result.data["orders"]
    )


def test_search_factory_orders_by_due_date():
    tool = OrderSearchTool()

    result = tool.execute(
        status="IN_PROGRESS",
        due_before=date(2026, 4, 1),
    )

    assert result.data["count"] > 0
    assert all(
        date.fromisoformat(order["due_date"]) < date(2026, 4, 1)
        for order in result.data["orders"]
    )


def test_search_factory_orders_no_match():
    tool = OrderSearchTool()

    result = tool.execute(customer="Customer That Does Not Exist")

    assert result.data["count"] == 0
    assert result.data["orders"] == []
    assert result.evidence == []
    assert result.warnings

def test_search_factory_orders_requires_filter():
    tool = OrderSearchTool()

    result = tool.execute()

    assert result.data["count"] == 0
    assert result.data["orders"] == []
    assert result.evidence == []
    assert result.warnings == [
        "At least one search filter must be provided."
    ]


def test_search_factory_orders_rejects_invalid_due_date_range():
    tool = OrderSearchTool()

    try:
        tool.execute(
            due_after=date(2026, 4, 10),
            due_before=date(2026, 4, 1),
        )
    except ValueError as error:
        assert str(error) == "due_after must be earlier than due_before."
    else:
        raise AssertionError("Expected ValueError for invalid due-date range.")