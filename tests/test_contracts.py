from app.agent.contracts import Evidence, ToolResult


def test_tool_result_keeps_traceable_evidence() -> None:
    evidence = Evidence(source="orders.csv", reference="ORD-001", detail="Order is in packing")
    result = ToolResult(data={"order_id": "ORD-001"}, evidence=[evidence])

    assert result.evidence[0].reference == "ORD-001"
