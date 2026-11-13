"""Stable boundary between deterministic tools, the agent, and the UI."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Evidence:
    """A source row or calculation that supports a user-visible claim."""

    source: str
    reference: str
    detail: str


@dataclass
class ToolResult:
    """Every deterministic tool returns data plus traceable evidence."""

    data: dict[str, Any]
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Tool(Protocol):
    """Contract implemented by A's tools and called by B's agent."""

    name: str
    description: str

    def execute(self, **kwargs: Any) -> ToolResult:
        """Run deterministic retrieval or calculation; never call an LLM."""


@dataclass
class AgentReply:
    """A user-facing answer plus the trace information needed by the UI."""

    text: str
    evidence: list[Evidence] = field(default_factory=list)
    needs_clarification: bool = False
    suggested_question: str | None = None
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
