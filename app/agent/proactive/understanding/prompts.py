"""Structured extraction contract for proactive requests."""

SYSTEM_PROMPT = """
You route bilingual SweaterCo requests. Extract commands; never execute tools or
invent business facts, dates, times, IDs, or missing parameters. Return one object.
The user prompt supplies the factory business date. For a watch deadline spoken
as a month and day without a year, use that business date's year only when the
result is later than the business date; otherwise leave the deadline missing.
Understand ordinary spoken order numbers such as "order 002" as ORD-002.
Keep explicit time zones such as Singapore time when forming deadline_at.

Scheduled briefings are persistent: "every morning", "每三天". An immediate
"give me a morning briefing now" belongs to the existing business chat, so
return unknown here. Standing watches persist and check an order later. A current
order-status question, feasibility question, or "why?" follow-up belongs to
existing business chat: return unknown. Drafting text alone is not a side effect.
Sending a message or adding an order note is not in the V1 action-tool contract:
return unknown rather than implying that it can execute.

Supported command_type: create, update, cancel, list, get, confirm, reject,
unknown. task_type: briefing, watch, or null for confirm/reject/unknown.
Briefing parameters: interval_days (positive integer), local_time (HH:MM, 24h),
timezone (IANA name; default Asia/Singapore), start_date (YYYY-MM-DD), enabled.
Watch parameters: order_id (ORD-057 form), condition_type
(no_progress_by_deadline or status_equals), deadline_at (timezone-aware ISO 8601),
expected_status, check_interval_minutes (positive integer).
Do not turn an unsupported watch condition into a supported one. Do not interpret
an unclear hour as AM or PM. If a target such as "that one" is ambiguous, leave
target_id null. Confirmation/rejection must identify one exact pending proposal.
Use the supplied context IDs only. A model-suggested value is still checked by
the caller. A command may contain missing values; use null for those fields.
Return en or zh according to the user's requested reply language, otherwise the
language used by the user. Never include secrets in output.
""".strip()

PARAMETER_FIELDS = (
    "interval_days",
    "local_time",
    "timezone",
    "start_date",
    "enabled",
    "order_id",
    "condition_type",
    "deadline_at",
    "expected_status",
    "check_interval_minutes",
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "command_type": {
            "type": "string",
            "enum": ["create", "update", "cancel", "list", "get", "confirm", "reject", "unknown"],
        },
        "task_type": {"type": ["string", "null"], "enum": ["briefing", "watch", None]},
        "target_id": {"type": ["string", "null"]},
        "language": {"type": "string", "enum": ["en", "zh"]},
        "parameters": {
            "type": "object",
            "properties": {
                "interval_days": {"type": ["integer", "null"]},
                "local_time": {"type": ["string", "null"]},
                "timezone": {"type": ["string", "null"]},
                "start_date": {"type": ["string", "null"]},
                "enabled": {"type": ["boolean", "null"]},
                "order_id": {"type": ["string", "null"]},
                "condition_type": {"type": ["string", "null"]},
                "deadline_at": {"type": ["string", "null"]},
                "expected_status": {"type": ["string", "null"]},
                "check_interval_minutes": {"type": ["integer", "null"]},
            },
            "required": list(PARAMETER_FIELDS),
            "additionalProperties": False,
        },
    },
    "required": ["command_type", "task_type", "target_id", "language", "parameters"],
    "additionalProperties": False,
}
