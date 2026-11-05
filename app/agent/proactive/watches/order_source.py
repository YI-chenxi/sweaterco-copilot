"""Injected P5 order-source boundary. The default adapter reads a local CSV snapshot."""

from typing import Protocol

from app.agent.contracts import ToolResult
from app.agent.proactive.watches.repository import validate_workspace
from app.tools.order_lookup import OrderLookupTool


class OrderSource(Protocol):
    def fetch(self, workspace_id: str, order_id: str) -> ToolResult: ...


class OrderLookupSource:
    """One explicit workspace per tool/dataset; no cross-workspace fallback."""

    def __init__(self, workspace_id: str, tool: OrderLookupTool | None = None):
        validate_workspace(workspace_id)
        self._workspace_id = workspace_id
        self._tool = tool if tool is not None else OrderLookupTool()

    def fetch(self, workspace_id: str, order_id: str) -> ToolResult:
        validate_workspace(workspace_id)
        if workspace_id != self._workspace_id:
            raise ValueError("Order source is not bound to this workspace")
        return self._tool.execute(order_id=order_id)
