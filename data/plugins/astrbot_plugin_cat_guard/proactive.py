"""Pure helpers for CatQQ contacts and proactive messages."""
from __future__ import annotations

import json
import hashlib
import os
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Contact:
    user_id: str
    name: str
    relationship: str = ""

    @property
    def identity_label(self) -> str:
        if self.relationship:
            return f"{self.name}（{self.relationship}）"
        return self.name


@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool
    target: str
    active_start_hour: int
    active_end_hour: int
    max_per_day: int
    min_gap: timedelta
    inactive_after: timedelta
    after_reply_cooldown: timedelta
    random_chance: float = 0.08


@dataclass
class ProactiveState:
    last_seen_at: dict[str, datetime] = field(default_factory=dict)
    last_sent_at: datetime | None = None
    daily_date: date | None = None
    daily_count: int = 0
    fixed_sent_dates: dict[str, str] = field(default_factory=dict)
    pending_tasks: list["ContactTask"] = field(default_factory=list)


@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ReminderCommand:
    target_user_id: str
    target_name: str
    body: str
    intent: str = "remind"
    due_at: datetime | None = None


@dataclass
class ContactTask:
    task_id: str
    target_user_id: str
    target_name: str
    sender_user_id: str
    sender_name: str
    body: str
    intent: str
    due_at: datetime
    created_at: datetime
    status: str = "pending"
    sent_at: datetime | None = None


TASK_PREFIXES = ("小猫任务", "任务")


def parse_contacts(raw: str) -> dict[str, Contact]:
    contacts: dict[str, Contact] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        user_id = parts[0]
        relationship = parts[2] if len(parts) >= 3 else ""
        contacts[user_id] = Contact(user_id=user_id, name=parts[1], relationship=relationship)
    return contacts


def parse_legacy_identity(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        user_id, identity = part.split(":", 1)
        user_id = user_id.strip()
        identity = identity.strip()
        if user_id and identity:
            result[user_id] = identity
    return result


def contacts_from_env(env: dict[str, str] | None = None) -> dict[str, Contact]:
    env = env or os.environ
    contacts = parse_contacts(env.get("CATQQ_CONTACTS", ""))
    if contacts:
        return contacts

    allowed_users = [
        user_id.strip()
        for user_id in env.get("CATQQ_ALLOWED_USERS", "你的QQ号").split(",")
        if user_id.strip()
    ]
    legacy_identity = parse_legacy_identity(env.get("CATQQ_USER_IDENTITY", ""))

    result: dict[str, Contact] = {}
    for user_id in allowed_users:
        identity = legacy_identity.get(user_id, "主人")
        name = identity
        relationship = ""
        if "（" in identity and identity.endswith("）"):
            name, relationship = identity[:-1].split("（", 1)
        result[user_id] = Contact(user_id=user_id, name=name, relationship=relationship)
    return result


def resolve_target_user_id(target: str, contacts: dict[str, Contact]) -> str | None:
    target = target.strip()
    if not target:
        return None
    if target in contacts:
        return target
    for user_id, contact in contacts.items():
        if contact.name == target:
            return user_id
    return None


def parse_reminder_command(
    message: str,
    contacts: dict[str, Contact],
    now: datetime | None = None,
) -> ReminderCommand | None:
    now = now or datetime.now()
    text = message.strip()
    due_at, text = _extract_due_prefix(text, now)
    verbs = (
        ("去问问", "ask"),
        ("去问", "ask"),
        ("问问", "ask"),
        ("问", "ask"),
        ("去提醒", "remind"),
        ("去给", "tell"),
        ("去找", "call"),
        ("给", "tell"),
        ("提醒", "remind"),
        ("叫", "call"),
        ("找", "call"),
    )
    for verb, intent in verbs:
        if not text.startswith(verb):
            continue

        rest = text[len(verb):].strip()
        for user_id, contact in sorted(
            contacts.items(),
            key=lambda item: len(item[1].name),
            reverse=True,
        ):
            for alias in (contact.name, user_id):
                if not alias or not rest.startswith(alias):
                    continue
                body = rest[len(alias):].strip()
                body = body.lstrip(" :：,，。.")
                body_due_at, cleaned_body = _extract_due_prefix(body, now)
                if body_due_at is not None and due_at is None:
                    due_at = body_due_at
                    body = cleaned_body
                elif _strips_immediate_marker(body, cleaned_body):
                    body = cleaned_body
                if intent == "tell":
                    body = body.removeprefix("说").strip()
                return ReminderCommand(
                    target_user_id=user_id,
                    target_name=contact.name,
                    body=body,
                    intent=intent,
                    due_at=due_at,
                )

    return None


def task_text_from_message(message: str) -> str | None:
    text = message.strip()
    for prefix in TASK_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix):
            raw_rest = text[len(prefix):]
            rest = raw_rest.strip()
            if not raw_rest:
                return ""
            if raw_rest[0] in {":", "：", "，", ",", " ", "\t"}:
                return rest.lstrip(":：,， ").strip()
    return None


def is_task_help_request(text: str) -> bool:
    return text.strip() in {"", "帮助", "help", "？", "?", "怎么用"}


def is_task_list_request(text: str) -> bool:
    return text.strip() in {"列表", "任务列表", "list", "查看"}


def parse_task_cancel_request(text: str) -> str | None:
    stripped = text.strip()
    for prefix in ("取消", "删除", "完成"):
        if stripped.startswith(prefix):
            task_id = stripped[len(prefix):].strip().lstrip("#").strip()
            return task_id or None
    return None


def parse_self_contact_request(
    message: str,
    sender: Contact,
    now: datetime | None = None,
) -> ReminderCommand | None:
    now = now or datetime.now()
    text = message.strip()
    if not any(marker in text for marker in ("找我", "来找我", "联系我", "叫我")):
        return None
    if "记得" not in text and "到时候" not in text:
        return None

    due_at = _find_due_expression(text, now)
    if due_at is None:
        return None

    return ReminderCommand(
        target_user_id=sender.user_id,
        target_name=sender.name,
        body="到点来找我",
        intent="call",
        due_at=due_at,
    )


def build_reminder_message(command: ReminderCommand, sender: Contact) -> str:
    if command.body:
        if command.intent == "ask":
            return f"{sender.name}让小猫问你：{command.body}"
        if command.intent == "tell":
            return f"{sender.name}让小猫跟你说：{command.body}"
        if command.intent == "call":
            return f"{sender.name}让小猫来找你：{command.body}"
        return f"{sender.name}让小猫提醒你：{command.body}"
    return f"{sender.name}让小猫来叫你一下"


def build_task_message(task: ContactTask) -> str:
    sender = Contact(task.sender_user_id, task.sender_name)
    command = ReminderCommand(
        target_user_id=task.target_user_id,
        target_name=task.target_name,
        body=task.body,
        intent=task.intent,
        due_at=task.due_at,
    )
    return build_reminder_message(command, sender)


def create_contact_task(
    command: ReminderCommand,
    sender: Contact,
    now: datetime,
) -> ContactTask:
    if command.due_at is None:
        raise ValueError("scheduled contact task requires due_at")
    task_seed = (
        f"{sender.user_id}:{command.target_user_id}:"
        f"{command.due_at.isoformat()}:{command.intent}:{command.body}"
    )
    task_id = hashlib.sha1(task_seed.encode("utf-8")).hexdigest()[:16]
    return ContactTask(
        task_id=task_id,
        target_user_id=command.target_user_id,
        target_name=command.target_name,
        sender_user_id=sender.user_id,
        sender_name=sender.name,
        body=command.body,
        intent=command.intent,
        due_at=command.due_at,
        created_at=now,
    )


def due_tasks(state: ProactiveState, now: datetime) -> list[ContactTask]:
    return [
        task
        for task in state.pending_tasks
        if task.status == "pending" and task.due_at <= now
    ]


def mark_task_done(state: ProactiveState, task: ContactTask, sent_at: datetime) -> None:
    for existing in state.pending_tasks:
        if existing.task_id == task.task_id:
            existing.status = "done"
            existing.sent_at = sent_at
            return


def format_due_time(due_at: datetime, now: datetime) -> str:
    if due_at.date() == now.date():
        prefix = "今天"
    elif due_at.date() == now.date() + timedelta(days=1):
        prefix = "明天"
    else:
        prefix = due_at.strftime("%m-%d ")
    return f"{prefix}{due_at.strftime('%H:%M')}"


def _extract_due_prefix(text: str, now: datetime) -> tuple[datetime | None, str]:
    stripped = text.strip()
    for marker in ("现在", "马上", "立刻", "立即"):
        if stripped.startswith(marker):
            return None, stripped[len(marker):].strip()

    relative = _parse_relative_due(stripped, now)
    if relative is not None:
        due_at, consumed = relative
        return due_at, stripped[consumed:].strip()

    absolute = _parse_absolute_due(stripped, now)
    if absolute is not None:
        due_at, consumed = absolute
        return due_at, stripped[consumed:].strip()

    return None, stripped


def _strips_immediate_marker(original: str, cleaned: str) -> bool:
    stripped = original.strip()
    return cleaned != stripped and any(
        stripped.startswith(marker) for marker in ("现在", "马上", "立刻", "立即")
    )


def _parse_relative_due(text: str, now: datetime) -> tuple[datetime, int] | None:
    if text.startswith("半小时后"):
        return now + timedelta(minutes=30), len("半小时后")

    match = re.match(r"^([0-9一二两三四五六七八九十]+)\s*(个?小时|小时|分钟|分)后", text)
    if not match:
        return None

    amount = _parse_cn_number(match.group(1))
    if amount is None:
        return None
    unit = match.group(2)
    if "小时" in unit:
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(minutes=amount)
    return now + delta, match.end()


def _find_due_expression(text: str, now: datetime) -> datetime | None:
    for index in range(len(text)):
        candidate = text[index:].strip()
        relative = _parse_relative_due(candidate, now)
        if relative is not None:
            return relative[0]
        absolute = _parse_absolute_due(candidate, now)
        if absolute is not None:
            return absolute[0]
    return None


def _parse_absolute_due(text: str, now: datetime) -> tuple[datetime, int] | None:
    day_offset = 0
    meridiem = ""
    consumed_prefix = 0

    prefixes = (
        ("明天下午", 1, "下午"),
        ("明天晚上", 1, "晚上"),
        ("明天上午", 1, "上午"),
        ("明天早上", 1, "早上"),
        ("明天中午", 1, "中午"),
        ("明早", 1, "早上"),
        ("明晚", 1, "晚上"),
        ("明天", 1, ""),
        ("今天下午", 0, "下午"),
        ("今天晚上", 0, "晚上"),
        ("今天上午", 0, "上午"),
        ("今天早上", 0, "早上"),
        ("今晚", 0, "晚上"),
        ("下午", 0, "下午"),
        ("晚上", 0, "晚上"),
        ("上午", 0, "上午"),
        ("早上", 0, "早上"),
        ("中午", 0, "中午"),
    )

    rest = text
    for prefix, offset, prefix_meridiem in prefixes:
        if text.startswith(prefix):
            day_offset = offset
            meridiem = prefix_meridiem
            consumed_prefix = len(prefix)
            rest = text[consumed_prefix:].strip()
            break

    match = re.match(
        r"^([0-9]{1,2}|[一二两三四五六七八九十]+)\s*(点|时|钟|[:：])?\s*([0-9]{1,2}|[一二三四五六七八九十]+|半)?",
        rest,
    )
    if not match or not match.group(2):
        return None

    hour = _parse_cn_number(match.group(1))
    if hour is None:
        return None
    minute = 0
    minute_raw = match.group(3)
    if minute_raw == "半":
        minute = 30
    elif minute_raw:
        parsed_minute = _parse_cn_number(minute_raw)
        if parsed_minute is None:
            return None
        minute = parsed_minute

    if meridiem in {"下午", "晚上"} and hour < 12:
        hour += 12
    if meridiem == "中午" and hour < 11:
        hour += 12

    try:
        due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    except ValueError:
        return None

    if day_offset == 0 and not meridiem and due_at <= now and 1 <= hour <= 11:
        try:
            afternoon_due_at = due_at.replace(hour=hour + 12)
        except ValueError:
            afternoon_due_at = due_at
        if afternoon_due_at > now:
            due_at = afternoon_due_at

    if day_offset == 0 and due_at <= now:
        due_at += timedelta(days=1)

    return due_at, consumed_prefix + match.end()


def _parse_cn_number(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    values = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if raw in values:
        return values[raw]
    if raw.startswith("十") and len(raw) == 2:
        return 10 + values.get(raw[1], -10)
    if raw.endswith("十") and len(raw) == 2:
        return values.get(raw[0], 0) * 10
    if "十" in raw and len(raw) == 3:
        left, right = raw.split("十", 1)
        return values.get(left, 0) * 10 + values.get(right, 0)
    return None


def parse_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def parse_active_hours(raw: str) -> tuple[int, int]:
    if "-" not in raw:
        return 10, 23
    start, end = raw.split("-", 1)
    try:
        start_hour = max(0, min(23, int(start.strip())))
        end_hour = max(0, min(23, int(end.strip())))
    except ValueError:
        return 10, 23
    return start_hour, end_hour


def proactive_config_from_env(env: dict[str, str] | None = None) -> ProactiveConfig:
    env = env or os.environ
    active_start, active_end = parse_active_hours(
        env.get("CATQQ_PROACTIVE_ACTIVE_HOURS", "10-23")
    )
    return ProactiveConfig(
        enabled=parse_bool(env.get("CATQQ_PROACTIVE_ENABLED", "false")),
        target=env.get("CATQQ_PROACTIVE_TARGET", ""),
        active_start_hour=active_start,
        active_end_hour=active_end,
        max_per_day=int(env.get("CATQQ_PROACTIVE_MAX_PER_DAY", "3")),
        min_gap=timedelta(
            hours=float(env.get("CATQQ_PROACTIVE_MIN_GAP_HOURS", "3"))
        ),
        inactive_after=timedelta(
            hours=float(env.get("CATQQ_PROACTIVE_INACTIVE_HOURS", "6"))
        ),
        after_reply_cooldown=timedelta(
            minutes=float(
                env.get("CATQQ_PROACTIVE_AFTER_REPLY_COOLDOWN_MINUTES", "60")
            )
        ),
        random_chance=float(env.get("CATQQ_PROACTIVE_RANDOM_CHANCE", "0.08")),
    )


def should_send_proactive(
    *,
    now: datetime,
    target_user_id: str,
    state: ProactiveState,
    config: ProactiveConfig,
    sleeping: bool,
    trigger: str,
) -> ProactiveDecision:
    if not config.enabled:
        return ProactiveDecision(False, "disabled")
    if sleeping:
        return ProactiveDecision(False, "sleeping")
    if not _hour_in_window(now.hour, config.active_start_hour, config.active_end_hour):
        return ProactiveDecision(False, "outside_active_hours")

    today_count = state.daily_count if state.daily_date == now.date() else 0
    if today_count >= config.max_per_day:
        return ProactiveDecision(False, "daily_limit_reached")

    if state.last_sent_at is not None and now - state.last_sent_at < config.min_gap:
        return ProactiveDecision(False, "min_gap_not_reached")

    last_seen = state.last_seen_at.get(target_user_id)
    if last_seen is not None and now - last_seen < config.after_reply_cooldown:
        return ProactiveDecision(False, "target_recently_seen")

    if trigger == "inactive" and last_seen is not None:
        if now - last_seen < config.inactive_after:
            return ProactiveDecision(False, "target_not_inactive")

    return ProactiveDecision(True, "allowed")


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def choose_proactive_trigger(
    *,
    now: datetime,
    target_user_id: str,
    state: ProactiveState,
    config: ProactiveConfig,
) -> str | None:
    last_seen = state.last_seen_at.get(target_user_id)
    if last_seen is None or now - last_seen >= config.inactive_after:
        return "inactive"

    fixed_window = _fixed_window_name(now)
    if fixed_window:
        key = f"{now.date().isoformat()}:{fixed_window}"
        if state.fixed_sent_dates.get(target_user_id) != key:
            return fixed_window

    if random.random() < config.random_chance:
        return "random"

    return None


def _fixed_window_name(now: datetime) -> str | None:
    if 13 <= now.hour <= 14:
        return "fixed_afternoon"
    if 21 <= now.hour <= 22:
        return "fixed_night"
    return None


PROACTIVE_MESSAGES: dict[str, list[str]] = {
    "inactive": [
        "{name}今天还没有摸小猫",
        "小猫把这里看了三遍",
        "你今天是不是把小猫忘在这了",
    ],
    "fixed_afternoon": [
        "{name}，在干嘛呀",
        "小猫路过聊天框，看了一眼",
        "人，小猫来点名了",
    ],
    "fixed_night": [
        "{name}，晚上还不来看看小猫吗",
        "小猫准备睡前点一次名",
        "人，小猫在这里",
    ],
    "random": [
        "小猫只是突然想叫你一下",
        "{name}",
        "没事，小猫看看你在不在",
    ],
}


def choose_proactive_message(trigger: str, contact: Contact) -> str:
    messages = PROACTIVE_MESSAGES.get(trigger) or PROACTIVE_MESSAGES["random"]
    return random.choice(messages).format(name=contact.name)


def mark_proactive_sent(
    *,
    state: ProactiveState,
    target_user_id: str,
    sent_at: datetime,
    trigger: str,
) -> None:
    if state.daily_date != sent_at.date():
        state.daily_date = sent_at.date()
        state.daily_count = 0
    state.daily_count += 1
    state.last_sent_at = sent_at
    if trigger.startswith("fixed_"):
        state.fixed_sent_dates[target_user_id] = f"{sent_at.date().isoformat()}:{trigger}"


def load_state(path: Path) -> ProactiveState:
    if not path.exists():
        return ProactiveState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProactiveState()

    last_seen_raw = raw.get("last_seen_at", {})
    last_seen_at = {
        str(user_id): datetime.fromisoformat(value)
        for user_id, value in last_seen_raw.items()
        if isinstance(value, str)
    }
    proactive = raw.get("proactive", {})
    daily_date = None
    if isinstance(proactive.get("daily_date"), str):
        daily_date = date.fromisoformat(proactive["daily_date"])
    last_sent_at = None
    if isinstance(proactive.get("last_sent_at"), str):
        last_sent_at = datetime.fromisoformat(proactive["last_sent_at"])

    return ProactiveState(
        last_seen_at=last_seen_at,
        last_sent_at=last_sent_at,
        daily_date=daily_date,
        daily_count=int(proactive.get("daily_count", 0)),
        fixed_sent_dates={
            str(key): str(value)
            for key, value in proactive.get("fixed_sent_dates", {}).items()
        },
        pending_tasks=[
            _task_from_json(item)
            for item in raw.get("pending_tasks", [])
            if isinstance(item, dict)
        ],
    )


def save_state(path: Path, state: ProactiveState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "last_seen_at": {
            user_id: seen_at.isoformat()
            for user_id, seen_at in state.last_seen_at.items()
        },
        "proactive": {
            "last_sent_at": state.last_sent_at.isoformat()
            if state.last_sent_at
            else None,
            "daily_date": state.daily_date.isoformat() if state.daily_date else None,
            "daily_count": state.daily_count,
            "fixed_sent_dates": state.fixed_sent_dates,
        },
        "pending_tasks": [_task_to_json(task) for task in state.pending_tasks],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _task_from_json(raw: dict[str, Any]) -> ContactTask:
    sent_at = raw.get("sent_at")
    return ContactTask(
        task_id=str(raw["task_id"]),
        target_user_id=str(raw["target_user_id"]),
        target_name=str(raw["target_name"]),
        sender_user_id=str(raw["sender_user_id"]),
        sender_name=str(raw["sender_name"]),
        body=str(raw.get("body", "")),
        intent=str(raw.get("intent", "remind")),
        due_at=datetime.fromisoformat(str(raw["due_at"])),
        created_at=datetime.fromisoformat(str(raw["created_at"])),
        status=str(raw.get("status", "pending")),
        sent_at=datetime.fromisoformat(sent_at) if isinstance(sent_at, str) else None,
    )


def _task_to_json(task: ContactTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "target_user_id": task.target_user_id,
        "target_name": task.target_name,
        "sender_user_id": task.sender_user_id,
        "sender_name": task.sender_name,
        "body": task.body,
        "intent": task.intent,
        "due_at": task.due_at.isoformat(),
        "created_at": task.created_at.isoformat(),
        "status": task.status,
        "sent_at": task.sent_at.isoformat() if task.sent_at else None,
    }
