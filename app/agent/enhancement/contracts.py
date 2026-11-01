"""Shared contracts for natural-language enhancement modules.

These contracts extend the existing agent without modifying
the original IntentDecision, AgentReply, or business tools.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.agent.intents import IntentDecision, ResponseLanguage


class InterpretationIssueType(str, Enum):
    """Machine-readable reasons that understanding needs clarification."""

    POSSIBLE_TYPO = "possible_typo"
    INVALID_FORMAT = "invalid_format"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    TOO_MANY_TASKS = "too_many_tasks"


@dataclass(frozen=True)
class InterpretationIssue:
    """One structured understanding problem for the clarification module."""

    issue_type: InterpretationIssueType
    field: str | None = None
    original_value: Any | None = None
    suggested_value: Any | None = None
    decision_index: int | None = None
    task_id: str | None = None


@dataclass
class UnderstandingResult:
    """Structured interpretation of a user message."""

    decisions: list[IntentDecision] = field(default_factory=list)
    is_ambiguous: bool = False
    language: ResponseLanguage = ResponseLanguage.ENGLISH
    issues: list[InterpretationIssue] = field(default_factory=list)
    slot_updates: list["SlotUpdate"] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None
    direct_response: str | None = None


class TaskStatus(str, Enum):
    """Lifecycle status of an individual conversation task."""

    PENDING = "pending"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskState:
    """One independently tracked business task."""

    task_id: str
    decision: IntentDecision
    status: TaskStatus = TaskStatus.PENDING
    issues: list[InterpretationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class SlotUpdate:
    """A structured update targeting one existing task."""

    task_id: str
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    """Session-specific state for multiple business tasks."""

    tasks: list[TaskState] = field(default_factory=list)
    language: ResponseLanguage = ResponseLanguage.ENGLISH


@dataclass
class TaskPlan:
    """Ordered business tasks selected for execution."""

    tasks: list[IntentDecision] = field(default_factory=list)
    requires_clarification: bool = False
