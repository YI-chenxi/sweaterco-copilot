"""Construction and ownership of the A1 tools used by B orchestration."""

from dataclasses import dataclass

from app.tools.order_lookup import OrderLookupTool
from app.tools.order_search_tool import OrderSearchTool
from app.tools.production_retrieval_tool import ProductionRetrievalTool
from app.tools.workshop_retrieval_tool import WorkshopRetrievalTool


@dataclass
class AgentToolRegistry:
    """A dependency container for the existing deterministic retrieval tools."""

    order_lookup: OrderLookupTool
    order_search: OrderSearchTool
    production_retrieval: ProductionRetrievalTool
    workshop_retrieval: WorkshopRetrievalTool

    @classmethod
    def from_project_data(cls) -> "AgentToolRegistry":
        """Build tools that read the project's standard CSV data files."""

        return cls(
            order_lookup=OrderLookupTool(),
            order_search=OrderSearchTool(),
            production_retrieval=ProductionRetrievalTool(),
            workshop_retrieval=WorkshopRetrievalTool(),
        )
