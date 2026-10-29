"""Stateless turn integration over the five existing enhancement modules."""

from copy import deepcopy
from dataclasses import dataclass, field, replace

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskStatus,
)
from app.agent.enhancement.conversation.clarification import ValidationPolicy
from app.agent.enhancement.conversation.clarification_adapter import (
    apply_validated_update,
    run_validated_tasks,
)
from app.agent.enhancement.conversation.memory import add_task
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.intents import Intent, IntentDecision
from app.agent.orchestrator import AgentOrchestrator


@dataclass
class ChatSession:
    """Owned by ONE UI thread, never by a cached service or parser."""

    state: ConversationState = field(default_factory=ConversationState)
    active_task_ids: list[str] = field(default_factory=list)
    global_issues: list[InterpretationIssue] = field(default_factory=list)
    next_task_number: int = 1


@dataclass(frozen=True)
class TurnResult:
    reply: AgentReply
    session: ChatSession


def reset_request(session: ChatSession) -> ChatSession:
    """Explicitly discard unresolved context while retaining unique task IDs.

    The caller keeps the visible message/reply history. No business tool runs.
    This is also the explicit resolution path for unbound global blockers.
    """

    return ChatSession(next_task_number=session.next_task_number)


def _remember_issue(session: ChatSession, issue: InterpretationIssue) -> None:
    if issue not in session.global_issues:
        session.global_issues.append(deepcopy(issue))


class ConversationTurnService:
    """Parse, validate, plan and execute a turn without retaining caller state."""

    def __init__(self, parser: EnhancedIntentParser, orchestrator: AgentOrchestrator) -> None:
        self.parser = parser
        self.orchestrator = orchestrator
        self.policy = ValidationPolicy(reference_date=orchestrator.settings.reference_date)

    def handle_turn(self, text: str, session: ChatSession | None = None) -> TurnResult:
        current = deepcopy(session) if session is not None else ChatSession()
        understood = self.parser.parse(text, context=deepcopy(current.state))
        if understood.direct_response is not None:
            unchanged = deepcopy(session) if session is not None else ChatSession()
            reply = AgentReply(
                text=understood.direct_response,
                metadata={
                    "intent": "conversation",
                    "language": understood.language.value,
                    "conversation_mode": "direct",
                },
            )
            return TurnResult(reply, unchanged)
        current.state.language = understood.language
        new_ids = []
        for decision in understood.decisions:
            existing = {task.task_id for task in current.state.tasks}
            while f"task-{current.next_task_number:03d}" in existing:
                current.next_task_number += 1
            task_id = f"task-{current.next_task_number:03d}"
            current.next_task_number += 1
            current.state = add_task(current.state, decision, task_id)
            new_ids.append(task_id)

        # Persist task issues before validated updates can resolve matching fields.
        issue_targets = []
        for issue in understood.issues:
            target = issue.task_id
            if target is None and issue.decision_index is not None:
                index = issue.decision_index
                if 0 <= index < len(new_ids):
                    target = new_ids[index]
            task = next((t for t in current.state.tasks if t.task_id == target), None)
            if (
                task is not None
                and task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}
                and issue.issue_type != InterpretationIssueType.TOO_MANY_TASKS
            ):
                bound = replace(deepcopy(issue), task_id=target, decision_index=None)
                if bound not in task.issues:
                    task.issues.append(bound)
                task.status = TaskStatus.PENDING
                issue_targets.append(target)
            else:
                _remember_issue(current, replace(issue, decision_index=None))

        if understood.is_ambiguous and not understood.issues:
            _remember_issue(
                current, InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE)
            )

        updated_ids = []
        for update in understood.slot_updates:
            target = next(
                (task for task in current.state.tasks if task.task_id == update.task_id), None
            )
            if target is None or target.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                # A follow-up cannot silently reopen or replay a completed task.
                _remember_issue(
                    current,
                    InterpretationIssue(
                        InterpretationIssueType.AMBIGUOUS_REFERENCE,
                        task_id=update.task_id,
                    ),
                )
                continue
            current.state, _ = apply_validated_update(
                current.state, update, policy=self.policy, language=understood.language
            )
            updated_ids.append(update.task_id)

        touched = list(dict.fromkeys([*updated_ids, *issue_targets]))
        if new_ids:
            requested = list(dict.fromkeys([*new_ids, *touched]))
        elif touched:
            # Keep the original two-task order when completing that same request.
            if set(touched).issubset(current.active_task_ids):
                requested = list(current.active_task_ids)
            else:
                requested = touched
        else:
            requested = list(current.active_task_ids) if current.global_issues else []

        # A pure unsupported message must not execute a previous pending request.
        only_unknown = (
            new_ids
            and all(decision.intent == Intent.UNKNOWN for decision in understood.decisions)
            and not understood.slot_updates
            and not understood.issues
            and not current.global_issues
        )
        if only_unknown or (not new_ids and not touched and not current.global_issues):
            unchanged = deepcopy(session) if session is not None else ChatSession()
            reply = self.orchestrator.handle(
                IntentDecision(intent=Intent.UNKNOWN, language=understood.language)
            )
            reply.metadata["language"] = understood.language.value
            reply.metadata["conversation_mode"] = "enhanced"
            return TurnResult(reply, unchanged)

        current.active_task_ids = requested
        result = run_validated_tasks(
            current.state,
            requested,
            self.orchestrator,
            policy=self.policy,
            issues=current.global_issues,
            language=understood.language,
        )
        # Save the final held-state snapshot, never execution.state or old inputs.
        current.state = result.state
        for problem in result.clarification.global_problems:
            if problem.issue is not None:
                _remember_issue(current, problem.issue)
        reply = result.reply
        if (
            understood.needs_clarification
            and understood.clarification_question
            and reply.needs_clarification
        ):
            previous_question = reply.suggested_question
            reply.suggested_question = understood.clarification_question
            if previous_question and previous_question in reply.text:
                reply.text = reply.text.replace(
                    previous_question, understood.clarification_question
                )
            reply.metadata["model_clarification_question"] = understood.clarification_question
        reply.metadata["language"] = understood.language.value
        reply.metadata["conversation_mode"] = "enhanced"
        reply.metadata["global_blockers"] = bool(current.global_issues)
        records = list(reply.metadata.get("tasks", {}).values())
        if (
            records
            and not reply.needs_clarification
            and not any(record.get("execution_outcome") == "success" for record in records)
        ):
            failed = any(
                record.get("execution_outcome") == "technical_failure" for record in records
            )
            reply.metadata["status"] = "failed" if failed else "empty"
        # Preserve the existing single-task UI's metrics without flattening tasks.
        if len(requested) == 1:
            record = reply.metadata.get("tasks", {}).get(requested[0], {})
            source = record.get("reply")
            if source:
                for key in ("intent", "structured_result", "rule_version", "status"):
                    if key in source["metadata"]:
                        reply.metadata[key] = deepcopy(source["metadata"][key])
        return TurnResult(reply, current)
