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
        CatTaskToolCommand,
        Contact,
        ReminderCommand,
        build_reminder_message,
        build_immediate_confirmation,
        build_scheduled_confirmation,
        build_sender_memory_pair,
        build_target_memory_pair,
        build_task_message,
        choose_proactive_message,
        choose_proactive_trigger,
        contacts_from_env,
        create_contact_task,
        due_tasks,
        extract_tool_command,
        format_task_overview,
        format_due_time,
        load_state,
        mark_task_done,
        mark_proactive_sent,
        proactive_config_from_env,
        reminder_from_tool_command,
        resolve_target_user_id,
        save_state,
        should_send_proactive,
        ToolCommandError,
    )
except ImportError:
    from proactive import (
        CatTaskToolCommand,
        Contact,
        ReminderCommand,
        build_reminder_message,
        build_immediate_confirmation,
        build_scheduled_confirmation,
        build_sender_memory_pair,
        build_target_memory_pair,
        build_task_message,
        choose_proactive_message,
        choose_proactive_trigger,
        contacts_from_env,
        create_contact_task,
        due_tasks,
        extract_tool_command,
        format_task_overview,
        format_due_time,
        load_state,
        mark_task_done,
        mark_proactive_sent,
        proactive_config_from_env,
        reminder_from_tool_command,
        resolve_target_user_id,
        save_state,
        should_send_proactive,
        ToolCommandError,
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
CONFLICTING_LLM_TOOLS = ("send_message_to_user", "future_task")

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
        self._tool_guard_task: asyncio.Task = asyncio.ensure_future(
            self._disable_conflicting_llm_tools_after_plugins_loaded()
        )
        self._state = load_state(STATE_PATH)
        self._disable_conflicting_llm_tools(log_not_found=True)
        if PROACTIVE_CONFIG.enabled and PROACTIVE_TARGET_USER_ID is None:
            logger.warning(
                "[cat_guard] proactive contact enabled but target is not in contacts"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _disable_conflicting_llm_tools(self, *, log_not_found: bool = False) -> int:
        """Force cross-contact work through the cat task protocol."""
        disabled_count = 0
        for tool_name in CONFLICTING_LLM_TOOLS:
            try:
                disabled = self.context.deactivate_llm_tool(tool_name)
            except Exception as exc:
                logger.warning(
                    f"[cat_guard] failed to disable LLM tool {tool_name}: {exc}"
                )
                continue
            if disabled:
                disabled_count += 1
                logger.info(f"[cat_guard] disabled conflicting LLM tool: {tool_name}")
            elif log_not_found:
                logger.info(f"[cat_guard] conflicting LLM tool not found: {tool_name}")
        return disabled_count

    async def _disable_conflicting_llm_tools_after_plugins_loaded(self) -> None:
        """Disable built-in tools again after later plugins finish registering."""
        await asyncio.sleep(3)
        disabled_count = self._disable_conflicting_llm_tools(log_not_found=True)
        logger.info(
            f"[cat_guard] delayed conflicting LLM tool guard finished: disabled={disabled_count}"
        )

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

    def _private_umo(self, user_id: str, platform_id: str = "default") -> str:
        session = MessageSesion(
            platform_name=platform_id,
            message_type=MessageType.FRIEND_MESSAGE,
            session_id=user_id,
        )
        return str(session)

    async def _append_memory_pair(
        self,
        unified_msg_origin: str,
        user_message: dict[str, str],
        assistant_message: dict[str, str],
    ) -> None:
        conv_mgr = getattr(self.context, "conversation_manager", None)
        if conv_mgr is None:
            logger.warning("[cat_guard] no conversation manager for task memory")
            return
        try:
            conversation_id = await conv_mgr.get_curr_conversation_id(
                unified_msg_origin
            )
            if not conversation_id:
                platform_id = unified_msg_origin.split(":", 1)[0]
                conversation_id = await conv_mgr.new_conversation(
                    unified_msg_origin,
                    platform_id=platform_id,
                )
            await conv_mgr.add_message_pair(
                conversation_id,
                user_message,
                assistant_message,
            )
        except Exception as exc:
            logger.warning(
                f"[cat_guard] failed to append task memory for {unified_msg_origin}: {exc}"
            )

    async def _remember_task_for_sender(
        self,
        event: AstrMessageEvent,
        sender,
        reminder,
        confirmation: str,
    ) -> None:
        user_message, assistant_message = build_sender_memory_pair(
            reminder,
            sender,
            confirmation,
        )
        await self._append_memory_pair(
            event.unified_msg_origin,
            user_message,
            assistant_message,
        )

    async def _remember_task_for_target(
        self,
        *,
        platform_id: str,
        sender,
        reminder,
        sent_text: str,
    ) -> None:
        user_message, assistant_message = build_target_memory_pair(
            reminder,
            sender,
            sent_text,
        )
        await self._append_memory_pair(
            self._private_umo(reminder.target_user_id, platform_id),
            user_message,
            assistant_message,
        )

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
                task_message = build_task_message(task)
                await self._send_private_text(
                    platform,
                    task.target_user_id,
                    task_message,
                )
                reminder = ReminderCommand(
                    target_user_id=task.target_user_id,
                    target_name=task.target_name,
                    body=task.body,
                    intent=task.intent,
                    due_at=task.due_at,
                )
                await self._remember_task_for_target(
                    platform_id=os.environ.get("CATQQ_PLATFORM_ID", "default"),
                    sender=CONTACTS.get(
                        task.sender_user_id,
                        Contact(task.sender_user_id, task.sender_name),
                    ),
                    reminder=reminder,
                    sent_text=task_message,
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

    async def _execute_reminder(
        self,
        *,
        event: AstrMessageEvent | None,
        sender: Contact,
        reminder: ReminderCommand,
        platform_id: str,
        now: datetime,
    ) -> str | None:
        platform = self._get_platform()
        if platform is None and reminder.due_at is None:
            return "小猫现在没连上，提醒不了"

        if reminder.due_at is not None:
            task = create_contact_task(reminder, sender, now)
            self._state.pending_tasks.append(task)
            save_state(STATE_PATH, self._state)

            logger.info(
                f"[cat_guard] contact task scheduled: target={task.target_name} due={format_due_time(task.due_at, now)}"
            )
            confirmation = build_scheduled_confirmation(task, now)
            if event is not None:
                await self._remember_task_for_sender(
                    event,
                    sender,
                    reminder,
                    confirmation,
                )
            return confirmation

        reminder_text = build_reminder_message(reminder, sender)
        await self._send_private_text(platform, reminder.target_user_id, reminder_text)

        self._state.last_sent_at = now
        save_state(STATE_PATH, self._state)

        logger.info(
            f"[cat_guard] manual reminder: from={sender.name} to={reminder.target_name}"
        )
        confirmation = build_immediate_confirmation(reminder)
        if event is not None:
            await self._remember_task_for_sender(event, sender, reminder, confirmation)
        await self._remember_task_for_target(
            platform_id=platform_id,
            sender=sender,
            reminder=reminder,
            sent_text=reminder_text,
        )
        return confirmation

    async def _execute_tool_command(
        self,
        *,
        event: AstrMessageEvent,
        user_id: str,
        command,
        now: datetime,
    ) -> str:
        if command.name == "list":
            return self._format_contact_task_list(now)
        if command.name == "cancel":
            if not command.task_id.strip():
                raise ToolCommandError("取消任务需要 id")
            return self._cancel_contact_task(command.task_id)
        if command.name not in {"send", "schedule"}:
            raise ToolCommandError(f"不支持的工具：{command.name}")

        reminder = reminder_from_tool_command(command, CONTACTS, now)
        platform_id = event.unified_msg_origin.split(":", 1)[0]
        return await self._execute_reminder(
            event=event,
            sender=CONTACTS[user_id],
            reminder=reminder,
            platform_id=platform_id,
            now=now,
        )

    def _sender_from_tool_event(self, event: AstrMessageEvent) -> tuple[str, str]:
        user_id = str(event.get_sender_id())
        if user_id not in ALLOWED_USERS:
            raise ToolCommandError("这个人不在小猫的白名单里")
        platform_id = event.unified_msg_origin.split(":", 1)[0]
        return user_id, platform_id

    @filter.llm_tool(name="cat_task_send")
    async def cat_task_send_tool(
        self,
        event: AstrMessageEvent,
        target: str,
        action: str,
        content: str,
    ) -> str:
        """让小猫现在联系一个已配置联系人，用于带话、询问、提醒、去找一下或温和打小报告。跨联系人发消息必须调用这个工具，不要自己说找不到。

        Args:
            target(string): 联系人名字或 QQ 号，例如 蛋蛋、鲍鲍。
            action(string): 动作类型，只能是 tell、ask、remind、visit、report。
            content(string): 要带给目标联系人的内容。tell/ask/remind/report 必须有内容，visit 可以简短写去找一下的原因。
        """
        try:
            user_id, _ = self._sender_from_tool_event(event)
            command = CatTaskToolCommand(
                name="send",
                target=target,
                action=action,
                content=content,
            )
            result = await self._execute_tool_command(
                event=event,
                user_id=user_id,
                command=command,
                now=datetime.now(),
            )
            return result or "小猫已经执行了"
        except ToolCommandError as exc:
            return f"小猫没有执行这个任务：{exc}"
        except Exception as exc:
            logger.error(f"[cat_guard] structured send tool failed: {exc}")
            return "小猫执行这个任务时出错了"

    @filter.llm_tool(name="cat_task_schedule")
    async def cat_task_schedule_tool(
        self,
        event: AstrMessageEvent,
        target: str,
        action: str,
        time: str,
        content: str,
    ) -> str:
        """安排小猫在未来时间联系一个已配置联系人，任务会进入小猫任务列表和记忆。

        Args:
            target(string): 联系人名字或 QQ 号，例如 蛋蛋、鲍鲍。
            action(string): 动作类型，只能是 tell、ask、remind、visit、report。
            time(string): 未来时间，例如 今天16:00、下午三点、半小时后、明早八点。
            content(string): 到点后要带给目标联系人的内容。
        """
        try:
            user_id, _ = self._sender_from_tool_event(event)
            command = CatTaskToolCommand(
                name="schedule",
                target=target,
                action=action,
                content=content,
                time_text=time,
            )
            result = await self._execute_tool_command(
                event=event,
                user_id=user_id,
                command=command,
                now=datetime.now(),
            )
            return result or "小猫已经记住了"
        except ToolCommandError as exc:
            return f"小猫没有安排这个任务：{exc}"
        except Exception as exc:
            logger.error(f"[cat_guard] structured schedule tool failed: {exc}")
            return "小猫安排这个任务时出错了"

    @filter.llm_tool(name="cat_task_list")
    async def cat_task_list_tool(self, event: AstrMessageEvent) -> str:
        """查看小猫当前记着的任务列表，包括未来一次性任务、早安晚安固定任务和主动联系设置。用户问小猫还记着什么任务时调用。"""
        try:
            self._sender_from_tool_event(event)
            return self._format_contact_task_list(datetime.now())
        except ToolCommandError as exc:
            return f"小猫看不了任务列表：{exc}"
        except Exception as exc:
            logger.error(f"[cat_guard] structured list tool failed: {exc}")
            return "小猫查看任务列表时出错了"

    @filter.llm_tool(name="cat_task_cancel")
    async def cat_task_cancel_tool(self, event: AstrMessageEvent, task_id: str) -> str:
        """取消一个小猫未来待办任务。用户说取消某个任务、不要提醒了、删掉任务时调用。

        Args:
            task_id(string): 任务 ID，可以是不带 # 的完整 ID 或前缀。
        """
        try:
            self._sender_from_tool_event(event)
            if not task_id.strip():
                raise ToolCommandError("取消任务需要 id")
            return self._cancel_contact_task(task_id)
        except ToolCommandError as exc:
            return f"小猫取消不了这个任务：{exc}"
        except Exception as exc:
            logger.error(f"[cat_guard] structured cancel tool failed: {exc}")
            return "小猫取消任务时出错了"

    def _format_contact_task_list(self, now: datetime) -> str:
        proactive_target_name = None
        if PROACTIVE_TARGET_USER_ID is not None:
            proactive_target_name = CONTACTS[PROACTIVE_TARGET_USER_ID].name
        return format_task_overview(
            state=self._state,
            now=now,
            morning_hour=MORNING_HOUR,
            night_hour=NIGHT_HOUR,
            proactive_config=PROACTIVE_CONFIG,
            proactive_target_name=proactive_target_name,
        )

    def _cancel_contact_task(self, task_id: str) -> str:
        for task in self._state.pending_tasks:
            if task.task_id.startswith(task_id) and task.status == "pending":
                task.status = "cancelled"
                save_state(STATE_PATH, self._state)
                return f"小猫取消了 #{task.task_id}"
        return f"小猫没找到待办任务 #{task_id}"

    def _plain_text_from_result(self, result) -> str | None:
        if result is None or not result.chain:
            return None
        text_parts: list[str] = []
        for component in result.chain:
            if not isinstance(component, Plain):
                return None
            text_parts.append(component.text)
        return "".join(text_parts)

    @filter.on_decorating_result()
    async def cat_task_tool_output(self, event: AstrMessageEvent):
        """Execute hidden !cat_task_* commands emitted by the LLM."""
        result = event.get_result()
        text = self._plain_text_from_result(result)
        if text is None:
            return

        extraction = extract_tool_command(text)
        if extraction.command is None:
            return

        user_id = str(event.get_sender_id())
        visible_parts = []
        if extraction.visible_text:
            visible_parts.append(extraction.visible_text)

        now = datetime.now()
        try:
            if user_id not in ALLOWED_USERS:
                raise ToolCommandError("这个人不在小猫的白名单里")
            confirmation = await self._execute_tool_command(
                event=event,
                user_id=user_id,
                command=extraction.command,
                now=now,
            )
        except ToolCommandError as exc:
            confirmation = f"小猫没有执行这个任务：{exc}"
        except Exception as exc:
            logger.error(f"[cat_guard] tool command failed: {exc}")
            confirmation = "小猫执行这个任务时出错了"

        visible_parts.append(confirmation)
        if extraction.extra_command_count:
            visible_parts.append("小猫一次只做一件事，后面的先不动。")
        event.set_result(event.plain_result("\n".join(visible_parts)))

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def cat_guard(self, event: AstrMessageEvent):
        """Whitelist guard + sleep/wake gate for every incoming message."""

        # Scheduler is started eagerly in __init__; no lazy-start needed here.
        self._disable_conflicting_llm_tools()

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
        if self._tool_guard_task is not None:
            self._tool_guard_task.cancel()
        logger.info("[cat_guard] terminated")
