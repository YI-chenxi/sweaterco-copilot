"""Validate model-extracted proactive commands without executing them."""

from __future__ import annotations

import json
import logging
import re
from calendar import month_name
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.enhancement.understanding.normalizers import normalize_order_ids_in_text
from app.agent.intent_parser import detect_response_language
from app.agent.llm_client import LLMClientError, StructuredLLMClient
from app.agent.settings import AgentSettings

from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT

LOGGER = logging.getLogger(__name__)
_COMMANDS = frozenset({"create", "update", "cancel", "list", "get", "confirm", "reject"})
_BRIEFING_FIELDS = frozenset({"interval_days", "local_time", "timezone", "start_date", "enabled"})
_WATCH_FIELDS = frozenset(
    {
        "order_id",
        "condition_type",
        "deadline_at",
        "expected_status",
        "check_interval_minutes",
    }
)
_ORDER_ID = re.compile(r"(?<![A-Z0-9])ORD(?:ER)?[\s_-]?(\d{1,3})(?![A-Z0-9])", re.IGNORECASE)
_ORDER_STATUS = re.compile(r"\b(?:complete(?:d)?|in[\s_-]+progress)\b", re.IGNORECASE)
_NEGATED_STATUS = re.compile(
    r"\b(?:not|without|isn't|is not)\s+(?:yet\s+)?(?:complete(?:d)?|in[\s_-]+progress)\b",
    re.IGNORECASE,
)
_EXPLICIT_DATE = re.compile(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_MONTHS = {name.casefold(): number for number, name in enumerate(month_name) if name}
_FIRST_NINETEEN = (
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
    "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
    "nineteenth",
)
_DAY_WORDS = {word: number for number, word in enumerate(_FIRST_NINETEEN, start=1)}
_DAY_WORDS.update({f"twenty {word}": number for number, word in
                   enumerate(_FIRST_NINETEEN[:9], start=21)})
_DAY_WORDS.update({"twentieth": 20, "thirtieth": 30, "thirty first": 31})
_SPOKEN_DAY = "|".join(
    re.escape(word).replace(r"\ ", r"[\s-]+")
    for word in sorted(_DAY_WORDS, key=len, reverse=True)
)
_SPOKEN_DATE = re.compile(
    rf"\b(?P<month>{'|'.join(_MONTHS)})\s+(?:the\s+)?"
    rf"(?P<day>\d{{1,2}}(?:st|nd|rd|th)?|{_SPOKEN_DAY})"
    r"(?:,?\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
_SPOKEN_HOURS = {
    word: number for number, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve"))
}
_MINUTE_WORDS = {
    word: number for number, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"))
}
for tens, prefix in ((20, "twenty"), (30, "thirty"), (40, "forty"), (50, "fifty")):
    _MINUTE_WORDS[prefix] = tens
    _MINUTE_WORDS.update({f"{prefix} {word}": tens + value for word, value in
                          list(_MINUTE_WORDS.items())[:10] if 1 <= value <= 9})
_MINUTE_WORDS.update({f"oh {word}": value for word, value in _SPOKEN_HOURS.items()
                      if 1 <= value <= 9})
_MINUTE_PATTERN = "|".join(
    re.escape(word).replace(r"\ ", r"[\s-]+")
    for word in sorted(_MINUTE_WORDS, key=len, reverse=True)
)
_SPOKEN_TIME = re.compile(
    rf"\bat\s+(?P<hour>\d{{1,2}}|{'|'.join(_SPOKEN_HOURS)})"
    rf"(?::(?P<minute_digits>\d{{2}})|\s+(?P<minute_words>{_MINUTE_PATTERN}))?"
    r"\s*(?P<period>a\.?m\.?|p\.?m\.?)?(?=\W|$)",
    re.IGNORECASE,
)
_ZONE_ALIASES = {
    "singapore": "Asia/Singapore", "sgt": "Asia/Singapore",
    "shanghai": "Asia/Shanghai", "tokyo": "Asia/Tokyo", "jst": "Asia/Tokyo",
    "london": "Europe/London", "paris": "Europe/Paris",
    "new york": "America/New_York", "eastern": "America/New_York",
    "los angeles": "America/Los_Angeles", "pacific": "America/Los_Angeles",
    "sydney": "Australia/Sydney", "utc": "UTC", "gmt": "Etc/GMT",
}
_ZONE_CITIES = "|".join(re.escape(name) for name in sorted(_ZONE_ALIASES, key=len, reverse=True)
                        if " " in name or name not in {"sgt", "jst", "utc", "gmt"})
_SPOKEN_ZONE = re.compile(
    rf"\b(?P<city>{_ZONE_CITIES})\s+time\b"
    r"|\b(?P<iana>[A-Za-z_]+(?:/[A-Za-z_]+)+)\b"
    r"|\b(?P<abbr>SGT|JST|UTC|GMT)\b",
    re.IGNORECASE,
)
_TIME_CUE = re.compile(
    r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm|点|時|时)"
    r"|(?:早上|上午|下午|晚上|中午)",
    re.IGNORECASE,
)
_AFFIRM = re.compile(
    r"\b(?:yes|confirm|approve|go ahead|proceed)\b|确认|同意|执行|^(?:好的|好|是的)[！!。.]?$",
    re.IGNORECASE,
)
_REJECT = re.compile(
    r"\b(?:no|reject|decline|don't|do not)\b|拒绝|不要|取消提案|不执行", re.IGNORECASE
)
_SCHEDULE_CUE = re.compile(
    r"\b(?:schedule|scheduled|every|daily|weekly)\b|定时|每天|每隔|每周|每\d+天", re.IGNORECASE
)
_WATCH_CUE = re.compile(
    r"\b(?:watch|monitor|alert|notify|remind|tell me if)\b|监控|提醒|告警|如果|若", re.IGNORECASE
)


@dataclass(frozen=True)
class ProactiveCommand:
    """JSON-compatible P1 output; missing fields mean the command is not ready."""

    command_type: str = "unknown"
    task_type: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    target_id: str | None = None
    missing_fields: list[str] = field(default_factory=list)
    language: str = "en"
    reference_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _language(text: str) -> str:
    lowered = text.casefold()
    if re.search(r"用英文|英文回答|翻译成英文|\bin english\b", lowered):
        return "en"
    if re.search(r"用中文|中文回答|翻译成中文|\bin chinese\b", lowered):
        return "zh"
    return detect_response_language(text).value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _normalize_time(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}", value):
        return None
    hour, minute = map(int, value.split(":"))
    try:
        time(hour, minute)
    except ValueError:
        return None
    return f"{hour:02d}:{minute:02d}"


def _normalize_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _normalize_deadline(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _explicit_order_status(text: str) -> str | None:
    """Only use a single, positively stated order status from the source text."""

    if _NEGATED_STATUS.search(text):
        return None
    statuses = {
        "IN_PROGRESS" if match.group().casefold().startswith("in") else "COMPLETE"
        for match in _ORDER_STATUS.finditer(text)
    }
    return next(iter(statuses)) if len(statuses) == 1 else None


def _spoken_deadline(text: str, reference_date: date) -> str | None:
    """Resolve one explicit spoken month, day, time, and zone against the snapshot year."""

    dates = list(_SPOKEN_DATE.finditer(text))
    times = list(_SPOKEN_TIME.finditer(text))
    zones = list(_SPOKEN_ZONE.finditer(text))
    if len(dates) != 1 or len(times) != 1 or len(zones) != 1:
        return None
    day_text = dates[0]["day"].casefold().replace("-", " ")
    day = (
        int(re.sub(r"(?:st|nd|rd|th)$", "", day_text))
        if day_text[0].isdigit()
        else _DAY_WORDS.get(day_text)
    )
    if day is None:
        return None
    year = int(dates[0]["year"]) if dates[0]["year"] else reference_date.year
    try:
        deadline_date = date(year, _MONTHS[dates[0]["month"].casefold()], day)
    except ValueError:
        return None
    if deadline_date <= reference_date:
        return None
    hour_text = times[0]["hour"].casefold()
    hour = int(hour_text) if hour_text.isdigit() else _SPOKEN_HOURS[hour_text]
    minute_text = times[0]["minute_words"]
    minute = (int(times[0]["minute_digits"]) if times[0]["minute_digits"]
              else _MINUTE_WORDS[minute_text.casefold().replace("-", " ")] if minute_text
              else 0)
    if hour > 23 or minute > 59:
        return None
    period = times[0]["period"]
    if period:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period.casefold().startswith("p") else 0)
    elif hour <= 12 and not (hour == 0 or (hour_text.startswith("0") and len(hour_text) == 2)):
        return None
    zone_text = zones[0]["iana"] or _ZONE_ALIASES[
        (zones[0]["city"] or zones[0]["abbr"]).casefold()
    ]
    try:
        zone = ZoneInfo(zone_text)
    except ZoneInfoNotFoundError:
        return None
    local = datetime.combine(deadline_date, time(hour, minute), zone)
    utc = local.astimezone(timezone.utc)
    # An IANA zone may have a skipped or repeated local hour at a DST change.
    if utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        return None
    return utc.isoformat()


def _context_ids(context: dict[str, Any], task_type: str | None, command: str) -> set[str]:
    key = (
        "proposals"
        if command in {"confirm", "reject"}
        else ("briefings" if task_type == "briefing" else "watches")
    )
    entries = context.get(key, [])
    if not isinstance(entries, list):
        return set()
    ids: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        try:
            ids.add(str(UUID(item["id"])))
        except ValueError:
            continue
    return ids


def _normalize_parameters(
    raw: dict[str, Any], task_type: str, text: str, reference_date: date
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if task_type == "briefing":
        for key in ("interval_days",):
            value = _positive_int(raw.get(key))
            if value is not None:
                values[key] = value
        local_time = _normalize_time(raw.get("local_time"))
        if local_time and _TIME_CUE.search(text):
            hour = int(local_time[:2])
            ambiguous_hour = (
                hour <= 12
                and re.search(r"\d{1,2}\s*(?:点|時|时)", text)
                and not re.search(r"上午|下午|早上|晚上|中午|am\b|pm\b", text, re.IGNORECASE)
            )
            if not ambiguous_hour:
                values["local_time"] = local_time
        start_date = _normalize_date(raw.get("start_date"))
        if start_date and _EXPLICIT_DATE.search(text):
            values["start_date"] = start_date
        zone = raw.get("timezone")
        if isinstance(zone, str) and zone in text:
            try:
                ZoneInfo(zone)
                values["timezone"] = zone
            except ZoneInfoNotFoundError:
                pass
        if isinstance(raw.get("enabled"), bool):
            values["enabled"] = raw["enabled"]
    else:
        mentioned_orders = {f"ORD-{int(match.group(1)):03d}" for match in _ORDER_ID.finditer(text)}
        if len(mentioned_orders) == 1:
            values["order_id"] = next(iter(mentioned_orders))
        condition = raw.get("condition_type")
        if condition in {"no_progress_by_deadline", "status_equals"}:
            values["condition_type"] = condition
        deadline = _normalize_deadline(raw.get("deadline_at"))
        has_day = _EXPLICIT_DATE.search(text)
        spoken_deadline = _spoken_deadline(text, reference_date)
        if spoken_deadline is not None:
            values["deadline_at"] = spoken_deadline
        elif deadline and has_day and _TIME_CUE.search(text):
            values["deadline_at"] = deadline
        if condition == "status_equals":
            expected = _explicit_order_status(text)
            if expected is not None:
                values["expected_status"] = expected
        interval = _positive_int(raw.get("check_interval_minutes"))
        if interval is not None:
            values["check_interval_minutes"] = interval
    return values


class ProactiveCommandParser:
    """Extract a single proactive command using a replaceable structured LLM."""

    def __init__(self, client: StructuredLLMClient, *, reference_date: date | None = None) -> None:
        self.client = client
        self.reference_date = reference_date or AgentSettings().reference_date

    def parse(self, user_text: str, context: dict[str, Any] | None = None) -> ProactiveCommand:
        if not isinstance(user_text, str) or not user_text.strip():
            return ProactiveCommand()
        text = user_text.strip()
        language = _language(text)
        safe_context = context if isinstance(context, dict) else {}
        # Only IDs and task type enter the model prompt; no stored business data or secrets.
        prompt_context = {
            key: sorted(_context_ids(safe_context, kind, command))
            for key, kind, command in (
                ("briefings", "briefing", "get"),
                ("watches", "watch", "get"),
                ("proposals", None, "confirm"),
            )
        }
        try:
            raw = self.client.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=(
                    f"User message: {normalize_order_ids_in_text(text)}\n"
                    f"Business date: {self.reference_date.isoformat()}\n"
                    f"Available IDs: {json.dumps(prompt_context)}"
                ),
                schema=RESPONSE_SCHEMA,
            )
            if not isinstance(raw, dict):
                raise LLMClientError("Structured command must be an object")
            return self._validate(raw, text, language, safe_context)
        except (LLMClientError, TypeError, ValueError) as exc:
            LOGGER.warning("Proactive parsing failed: %s", exc)
            return ProactiveCommand(language=language)

    def _validate(
        self, raw: dict[str, Any], text: str, language: str, context: dict[str, Any]
    ) -> ProactiveCommand:
        command = raw.get("command_type")
        kind = raw.get("task_type")
        if command not in _COMMANDS:
            return ProactiveCommand(language=language)
        if command == "confirm" and (not _AFFIRM.search(text) or _REJECT.search(text)):
            return ProactiveCommand(language=language)
        if command == "reject" and not _REJECT.search(text):
            return ProactiveCommand(language=language)
        if command == "create" and kind == "briefing" and not _SCHEDULE_CUE.search(text):
            return ProactiveCommand(language=language)
        if command == "create" and kind == "watch" and not _WATCH_CUE.search(text):
            return ProactiveCommand(language=language)
        if command in {"confirm", "reject"}:
            kind = None
        elif kind not in {"briefing", "watch"}:
            return ProactiveCommand(language=language)
        if kind == "watch" and command == "update":
            # The V1 WatchService has no update method.
            return ProactiveCommand(language=language)
        target = raw.get("target_id")
        if not isinstance(target, str) or not target.strip():
            target = None
        else:
            try:
                target = str(UUID(target.strip()))
            except ValueError:
                target = None
        if command in {"update", "cancel", "get", "confirm", "reject"}:
            allowed = _context_ids(context, kind, command)
            if target not in allowed or (
                target.casefold() not in text.casefold() and len(allowed) != 1
            ):
                target = None
        else:
            target = None
        raw_params = raw.get("parameters")
        if not isinstance(raw_params, dict):
            raw_params = {}
        parameters = {}
        if kind and command in {"create", "update"}:
            parameters = _normalize_parameters(raw_params, kind, text, self.reference_date)
        if kind == "briefing":
            parameters = {
                key: value for key, value in parameters.items() if key in _BRIEFING_FIELDS
            }
            if command == "create" and "timezone" not in parameters:
                parameters["timezone"] = "Asia/Singapore"
        elif kind == "watch":
            parameters = {key: value for key, value in parameters.items() if key in _WATCH_FIELDS}
        missing: list[str] = []
        if command in {"update", "cancel", "get", "confirm", "reject"} and target is None:
            missing.append("target_id")
        if command == "create" and kind == "briefing":
            required = ("interval_days", "local_time", "start_date")
            missing.extend(key for key in required if key not in parameters)
        if command == "create" and kind == "watch":
            required = ("order_id", "condition_type", "check_interval_minutes")
            missing.extend(key for key in required if key not in parameters)
            if (
                parameters.get("condition_type") == "no_progress_by_deadline"
                and "deadline_at" not in parameters
            ):
                missing.append("deadline_at")
            if (
                parameters.get("condition_type") == "status_equals"
                and "expected_status" not in parameters
            ):
                missing.append("expected_status")
        if command == "update" and not parameters:
            missing.append("parameters")
        return ProactiveCommand(command, kind, parameters, target, missing, language)
