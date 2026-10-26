"""Shared contracts for natural-language enhancement modules.

These contracts extend the existing agent without modifying
the original IntentDecision, AgentReply, or business tools.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.agent.intents import IntentDecision, ResponseLanguage


@dataclass
class UnderstandingResult:
    """Structured interpretation of a user message."""

    decisions: list[IntentDecision] = field(default_factory=list)
    is_ambiguous: bool = False
    language: ResponseLanguage = ResponseLanguage.ENGLISH


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
