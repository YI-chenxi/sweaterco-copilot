from app.agent.contracts import Evidence, ToolResult
from app.tools.order_lookup import OrderLookupTool


def test_tool_result_keeps_traceable_evidence() -> None:
    evidence = Evidence(source="orders.csv", reference="ORD-001", detail="Order is in packing")
    result = ToolResult(data={"order_id": "ORD-001"}, evidence=[evidence])

    assert result.evidence[0].reference == "ORD-001"


def test_order_lookup_returns_source_evidence() -> None:
    result = OrderLookupTool().execute(order_id="ord-057")

    assert result.data["order_id"] == "ORD-057"
    assert result.evidence[0].source == "orders.csv"


def test_order_lookup_reports_missing_order() -> None:
    result = OrderLookupTool().execute(order_id="ORD-999")

    assert result.data == {}
    assert result.warnings == ["No order with ID ORD-999 exists in orders.csv."]
