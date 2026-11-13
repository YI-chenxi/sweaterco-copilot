from app.tools.order_lookup import OrderLookupTool


def test_lookup_factory_order_returns_exact_order():
    tool = OrderLookupTool()

    result = tool.execute(order_id="ORD-057")

    assert result.data["order_id"] == "ORD-057"
    assert result.evidence
    assert result.evidence[0].source == "orders.csv"
    assert result.evidence[0].reference == "ORD-057"


def test_lookup_factory_order_returns_typed_data():
    tool = OrderLookupTool()

    result = tool.execute(order_id="ORD-057")

    assert isinstance(result.data["pieces"], int)
    assert isinstance(result.data["days_late"], float)


def test_lookup_factory_order_handles_case_and_spaces():
    tool = OrderLookupTool()

    result = tool.execute(order_id=" ord-057 ")

    assert result.data["order_id"] == "ORD-057"


def test_lookup_factory_order_unknown_id():
    tool = OrderLookupTool()

    result = tool.execute(order_id="ORD-999")

    assert result.data == {}
    assert result.evidence == []
    assert result.warnings


def test_lookup_factory_order_rejects_empty_id():
    tool = OrderLookupTool()

    try:
        tool.execute(order_id="   ")
    except ValueError as error:
        assert str(error) == "order_id must not be empty"
    else:
        raise AssertionError("Expected ValueError for empty order_id.")