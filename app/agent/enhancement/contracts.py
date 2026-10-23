
"""Shared contracts for natural-language enhancement modules.

These contracts extend the existing agent without modifying
the original IntentDecision, AgentReply, or business tools.
"""

from dataclasses import dataclass, field
from typing import Any

from app.agent.intents import IntentDecision, ResponseLanguage


@dataclass
class UnderstandingResult:
    """Structured interpretation of a user message."""

    decisions: list[IntentDecision] = field(default_factory=list)
    is_ambiguous: bool = False
    language: ResponseLanguage = ResponseLanguage.ENGLISH


@dataclass
class ConversationState:
    """Session-specific state for multi-turn interactions."""

    pending_tasks: list[IntentDecision] = field(default_factory=list)
    collected_slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    language: ResponseLanguage = ResponseLanguage.ENGLISH


@dataclass
class TaskPlan:
    """Ordered business tasks selected for execution."""

    tasks: list[IntentDecision] = field(default_factory=list)
    requires_clarification: bool = False
