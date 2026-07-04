"""CatQQ Agent — 小猫白名单守卫插件.

Whitelist guard + sleep/wake + scheduled greetings.
"""
import os
import asyncio
import random
from datetime import datetime, date

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# ---------------------------------------------------------------------------
# Configuration (from environment variables, with defaults)
# ---------------------------------------------------------------------------

ALLOWED_USERS: set[str] = set(
    uid.strip()
    for uid in os.environ.get("CATQQ_ALLOWED_USERS", "你的QQ号").split(",")
    if uid.strip()
)

if not ALLOWED_USERS:
    logger.warning("[cat_guard] ALLOWED_USERS is empty — every user will be blocked.")

MORNING_HOUR: int = int(os.environ.get("CATQQ_MORNING_HOUR", "8"))
NIGHT_HOUR: int = int(os.environ.get("CATQQ_NIGHT_HOUR", "23"))

# Who is who — read from env var, format: "QQ:name,QQ:name"
def _parse_identity(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            qq, name = part.split(":", 1)
            result[qq.strip()] = name.strip()
    return result

USER_IDENTITY: dict[str, str] = _parse_identity(
    os.environ.get("CATQQ_USER_IDENTITY", "你的QQ号:主人")
)

SLEEP_WORD: str = "小猫睡觉"
WAKE_WORD: str = "小猫醒醒"
SLEEP_REPLY: str = "咪睡了，人晚安"

# Morning / night message pools (cat-persona style)
MORNING_MESSAGES: list[str] = [
    "人，早。小猫看着你醒的。",
    "早呀。小猫已经醒了，一直在等你。",
    "人，新的一天。小猫的尾巴先和你说早安。",
    "醒了呀人。小猫等你很久了。",
]

NIGHT_MESSAGES: list[str] = [
    "人，该睡了。小猫已经困成猫饼了。",
    "很晚了人。小猫把爪子揣好，等你一起睡。",
    "该睡觉了。小猫不熬夜，你也不许熬夜。",
    "人，晚安。小猫就趴在你旁边。",
]


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.sleeping: bool = False
        self._last_morning: date | None = None
        self._last_night: date | None = None
        self._scheduler_task: asyncio.Task = asyncio.ensure_future(self._start_scheduler())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _start_scheduler(self) -> None:
        """Background loop: check time every 60 s, fire greetings on the hour."""
        await asyncio.sleep(10)  # Let platform adapters initialise
        logger.info("[cat_guard] scheduler started")
        while True:
            try:
                now = datetime.now()
                today = now.date()

                if now.hour == MORNING_HOUR and self._last_morning != today:
                    self._last_morning = today
                    await self._send_greetings("morning")

                if now.hour == NIGHT_HOUR and self._last_night != today:
                    self._last_night = today
                    await self._send_greetings("night")

            except Exception as exc:
                logger.error(f"[cat_guard] scheduler error: {exc}")

            # Sleep outside try so CancelledError from terminate() propagates cleanly.
            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Scheduled greetings
    # ------------------------------------------------------------------

    async def _send_greetings(self, greeting_type: str) -> None:
        """Send a greeting message to every whitelisted user."""
        if greeting_type == "morning":
            msg = random.choice(MORNING_MESSAGES)
        else:
            msg = random.choice(NIGHT_MESSAGES)

        # Try to get a platform adapter that can send proactive messages.
        platform = self._get_platform()
        if platform is None:
            logger.warning("[cat_guard] no platform available for greetings")
            return

        for user_id in ALLOWED_USERS:
            try:
                # UMO session-id format: platform:message_type:session_id
                # Verify with `/sid` in AstrBot if this does not work.
                session_id = f"aiocqhttp:PrivateMessage:{user_id}"
                await platform.send_by_session(session_id, msg)
                logger.info(
                    f"[cat_guard] sent {greeting_type} greeting to {user_id}"
                )
                await asyncio.sleep(0.5)  # Small gap to avoid rate-limiting
            except Exception as exc:
                logger.error(
                    f"[cat_guard] failed to send {greeting_type} to {user_id}: {exc}"
                )

    def _get_platform(self):
        """Return the first available OneBot v11 platform adapter.

        Walks ``context.platform_manager.platform_insts`` if available,
        otherwise falls back to ``context.platform``.
        """
        # Attempt 1: platform_manager (astrbot.api convention)
        pm = getattr(self.context, "platform_manager", None)
        if pm is not None:
            insts = getattr(pm, "platform_insts", [])
            if insts:
                return insts[0]

        # Attempt 2: direct platform client (astrbot_sdk convention)
        platform = getattr(self.context, "platform", None)
        if platform is not None and hasattr(platform, "send_by_session"):
            return platform

        return None

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def cat_guard(self, event: AstrMessageEvent):
        """Whitelist guard + sleep/wake gate for every incoming message."""

        # Scheduler is started eagerly in __init__; no lazy-start needed here.

        user_id = str(event.get_sender_id())
        message = (event.message_str or "").strip()

        # --- Block empty messages and system reminders ---
        if not message or "<system_reminder>" in message:
            logger.info(f"[cat_guard] block empty/system message from {user_id}")
            event.stop_event()
            return

        # --- Block all group messages ---
        group_id = getattr(event.message_obj, "group_id", "")
        if group_id:
            logger.info(
                f"[cat_guard] block group message: group={group_id} user={user_id}"
            )
            event.stop_event()
            return

        # --- Block non-whitelist users ---
        if user_id not in ALLOWED_USERS:
            logger.info(f"[cat_guard] block non-allowed user: {user_id}")
            event.stop_event()
            return

        # --- Sleep word ---
        if SLEEP_WORD in message:
            self.sleeping = True
            event.stop_event()
            yield event.plain_result(SLEEP_REPLY)
            return

        # --- Wake word ---
        if WAKE_WORD in message:
            self.sleeping = False
            event.stop_event()
            # Cat-style wake reply — use AI via plain_result or a static pool.
            wake_replies = [
                "咪醒了。人，早上好呀。",
                "小猫醒了。人，你叫我？",
                "嗯……小猫醒了。尾巴先醒的。",
            ]
            yield event.plain_result(random.choice(wake_replies))
            return

        # --- Sleeping → block ---
        if self.sleeping:
            logger.info(f"[cat_guard] sleeping, ignore message from {user_id}")
            event.stop_event()
            return

        # --- Pass through to LLM ---
        # Tag the message with sender identity so 玖玖 knows who's talking.
        if user_id in USER_IDENTITY:
            identity = USER_IDENTITY[user_id]
            event.message_str = f"(这是{identity}) {event.message_str}"
            logger.info(f"[cat_guard] tagged message for {identity}")
        return

    async def terminate(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
        logger.info("[cat_guard] terminated")
