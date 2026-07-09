"""CatQQ Agent — 小猫白名单守卫插件.

Whitelist guard + sleep/wake + scheduled greetings.
"""
import os
import asyncio
import random
from datetime import datetime, date
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.message_type import MessageType

try:
    from .proactive import (
        build_reminder_message,
        build_task_message,
        choose_proactive_message,
        choose_proactive_trigger,
        contacts_from_env,
        create_contact_task,
        due_tasks,
        format_due_time,
        is_task_help_request,
        is_task_list_request,
        load_state,
        mark_task_done,
        mark_proactive_sent,
        parse_reminder_command,
        parse_self_contact_request,
        parse_task_cancel_request,
        proactive_config_from_env,
        resolve_target_user_id,
        save_state,
        should_send_proactive,
        task_text_from_message,
    )
except ImportError:
    from proactive import (
        build_reminder_message,
        build_task_message,
        choose_proactive_message,
        choose_proactive_trigger,
        contacts_from_env,
        create_contact_task,
        due_tasks,
        format_due_time,
        is_task_help_request,
        is_task_list_request,
        load_state,
        mark_task_done,
        mark_proactive_sent,
        parse_reminder_command,
        parse_self_contact_request,
        parse_task_cancel_request,
        proactive_config_from_env,
        resolve_target_user_id,
        save_state,
        should_send_proactive,
        task_text_from_message,
    )

# ---------------------------------------------------------------------------
# Configuration (from environment variables, with defaults)
# ---------------------------------------------------------------------------

CONTACTS = contacts_from_env()
ALLOWED_USERS: set[str] = set(CONTACTS.keys())

if not ALLOWED_USERS:
    logger.warning("[cat_guard] ALLOWED_USERS is empty — every user will be blocked.")

MORNING_HOUR: int = int(os.environ.get("CATQQ_MORNING_HOUR", "8"))
NIGHT_HOUR: int = int(os.environ.get("CATQQ_NIGHT_HOUR", "23"))

USER_IDENTITY: dict[str, str] = {
    user_id: contact.identity_label for user_id, contact in CONTACTS.items()
}
PROACTIVE_CONFIG = proactive_config_from_env()
PROACTIVE_TARGET_USER_ID = resolve_target_user_id(PROACTIVE_CONFIG.target, CONTACTS)
STATE_PATH = Path(os.environ.get("CATQQ_STATE_PATH", "/AstrBot/data/cat_guard_state.json"))

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
        self._state = load_state(STATE_PATH)
        if PROACTIVE_CONFIG.enabled and PROACTIVE_TARGET_USER_ID is None:
            logger.warning(
                "[cat_guard] proactive contact enabled but target is not in contacts"
            )

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

                await self._check_contact_tasks(now)
                await self._check_proactive_contact(now)

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
                await self._send_private_text(platform, user_id, msg)
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

    async def _send_private_text(self, platform, user_id: str, text: str) -> None:
        """Send plain text to a private QQ session through AstrBot."""
        session = MessageSesion(
            platform_name="aiocqhttp",
            message_type=MessageType.FRIEND_MESSAGE,
            session_id=user_id,
        )
        await platform.send_by_session(session, MessageChain([Plain(text)]))

    # ------------------------------------------------------------------
    # Proactive contact
    # ------------------------------------------------------------------

    async def _check_proactive_contact(self, now: datetime) -> None:
        """Maybe send a proactive message to the configured target contact."""
        if PROACTIVE_TARGET_USER_ID is None:
            return

        trigger = choose_proactive_trigger(
            now=now,
            target_user_id=PROACTIVE_TARGET_USER_ID,
            state=self._state,
            config=PROACTIVE_CONFIG,
        )
        if trigger is None:
            return

        decision = should_send_proactive(
            now=now,
            target_user_id=PROACTIVE_TARGET_USER_ID,
            state=self._state,
            config=PROACTIVE_CONFIG,
            sleeping=self.sleeping,
            trigger=trigger,
        )
        if not decision.allowed:
            logger.info(
                f"[cat_guard] proactive blocked: trigger={trigger} reason={decision.reason}"
            )
            return

        platform = self._get_platform()
        if platform is None:
            logger.warning("[cat_guard] no platform available for proactive contact")
            return

        contact = CONTACTS[PROACTIVE_TARGET_USER_ID]
        message = choose_proactive_message(trigger, contact)

        await self._send_private_text(platform, PROACTIVE_TARGET_USER_ID, message)
        mark_proactive_sent(
            state=self._state,
            target_user_id=PROACTIVE_TARGET_USER_ID,
            sent_at=now,
            trigger=trigger,
        )
        save_state(STATE_PATH, self._state)
        logger.info(
            f"[cat_guard] proactive sent: target={contact.name} trigger={trigger}"
        )

    async def _check_contact_tasks(self, now: datetime) -> None:
        """Send scheduled contact tasks that are due."""
        tasks = due_tasks(self._state, now)
        if not tasks:
            return

        platform = self._get_platform()
        if platform is None:
            logger.warning("[cat_guard] no platform available for contact tasks")
            return

        for task in tasks:
            try:
                await self._send_private_text(
                    platform,
                    task.target_user_id,
                    build_task_message(task),
                )
                mark_task_done(self._state, task, now)
                save_state(STATE_PATH, self._state)
                logger.info(
                    f"[cat_guard] contact task sent: target={task.target_name} task={task.task_id}"
                )
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.error(
                    f"[cat_guard] failed to send contact task {task.task_id}: {exc}"
                )

    async def _execute_contact_command(
        self,
        *,
        event: AstrMessageEvent,
        user_id: str,
        message: str,
        now: datetime,
        from_task_prefix: bool,
    ) -> str | None:
        task_text = task_text_from_message(message)
        text = task_text if task_text is not None else message

        if task_text is not None:
            event.stop_event()
            if is_task_help_request(text):
                return self._contact_task_help()

            if is_task_list_request(text):
                return self._format_contact_task_list(now)

            cancel_task_id = parse_task_cancel_request(text)
            if cancel_task_id is not None:
                return self._cancel_contact_task(cancel_task_id)

        reminder = parse_reminder_command(text, CONTACTS, now)
        if reminder is None:
            if from_task_prefix:
                event.stop_event()
                return "小猫没看懂这个任务。可以这样写：小猫任务：现在去给鲍鲍考试加油"
            return None

        platform = self._get_platform()
        event.stop_event()
        if platform is None and reminder.due_at is None:
            return "小猫现在没连上，提醒不了"

        sender = CONTACTS[user_id]
        if reminder.due_at is not None:
            task = create_contact_task(reminder, sender, now)
            self._state.pending_tasks.append(task)
            save_state(STATE_PATH, self._state)

            due_text = format_due_time(task.due_at, now)
            logger.info(
                f"[cat_guard] contact task scheduled: target={task.target_name} due={due_text}"
            )
            return f"小猫记住了，#{task.task_id} {due_text}去找{task.target_name}"

        reminder_text = build_reminder_message(reminder, sender)
        await self._send_private_text(platform, reminder.target_user_id, reminder_text)

        self._state.last_sent_at = now
        save_state(STATE_PATH, self._state)

        logger.info(
            f"[cat_guard] manual reminder: from={sender.name} to={reminder.target_name}"
        )
        return f"小猫去找{reminder.target_name}了"

    def _contact_task_help(self) -> str:
        return (
            "小猫任务可以这样写：\n"
            "小猫任务：现在去给鲍鲍考试加油\n"
            "小猫任务：半小时后提醒鲍鲍喝水\n"
            "小猫任务：下午三点问问鲍鲍考完了吗\n"
            "小猫任务：列表\n"
            "小猫任务：取消 #任务ID"
        )

    def _format_contact_task_list(self, now: datetime) -> str:
        pending = [
            task for task in self._state.pending_tasks if task.status == "pending"
        ]
        if not pending:
            return "现在没有待办的小猫任务"

        lines = ["待办小猫任务："]
        for task in sorted(pending, key=lambda item: item.due_at):
            body = f"：{task.body}" if task.body else ""
            lines.append(
                f"#{task.task_id} {format_due_time(task.due_at, now)} "
                f"{task.target_name}{body}"
            )
        return "\n".join(lines)

    def _cancel_contact_task(self, task_id: str) -> str:
        for task in self._state.pending_tasks:
            if task.task_id.startswith(task_id) and task.status == "pending":
                task.status = "cancelled"
                save_state(STATE_PATH, self._state)
                return f"小猫取消了 #{task.task_id}"
        return f"小猫没找到待办任务 #{task_id}"

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

        # Record the latest real user message for proactive-contact cooldowns.
        self._state.last_seen_at[user_id] = datetime.now()
        save_state(STATE_PATH, self._state)

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

        # --- Contact task commands ---
        now = datetime.now()
        task_text = task_text_from_message(message)
        contact_reply = await self._execute_contact_command(
            event=event,
            user_id=user_id,
            message=message,
            now=now,
            from_task_prefix=task_text is not None,
        )
        if contact_reply is not None:
            yield event.plain_result(contact_reply)
            return

        self_request = parse_self_contact_request(message, CONTACTS[user_id], now)
        if self_request is not None:
            event.stop_event()
            task = create_contact_task(self_request, CONTACTS[user_id], now)
            self._state.pending_tasks.append(task)
            save_state(STATE_PATH, self._state)
            logger.info(
                f"[cat_guard] self contact task scheduled: target={task.target_name}"
            )
            yield event.plain_result(
                f"小猫记住了，#{task.task_id} {format_due_time(task.due_at, now)}来找你"
            )
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
