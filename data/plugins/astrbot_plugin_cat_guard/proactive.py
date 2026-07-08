"""Pure helpers for CatQQ contacts and proactive messages."""
from __future__ import annotations

import json
import os
import random
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


@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    reason: str


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
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
