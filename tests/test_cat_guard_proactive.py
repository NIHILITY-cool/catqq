import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from data.plugins.astrbot_plugin_cat_guard.proactive import (
    CatTaskToolCommand,
    Contact,
    ContactTask,
    ProactiveConfig,
    ProactiveState,
    ReminderCommand,
    build_immediate_confirmation,
    build_sender_memory_pair,
    build_reminder_message,
    build_scheduled_confirmation,
    build_target_memory_pair,
    due_tasks,
    extract_tool_command,
    format_task_overview,
    format_due_time,
    is_task_help_request,
    is_task_list_request,
    mark_task_done,
    normalize_contact_message_body,
    normalize_reminder_body,
    parse_contacts,
    parse_reminder_command,
    parse_self_contact_request,
    parse_task_cancel_request,
    parse_tool_command_line,
    reminder_from_tool_command,
    resolve_target_user_id,
    load_state,
    save_state,
    should_send_proactive,
    ToolCommandError,
    task_text_from_message,
)


class ContactConfigTests(unittest.TestCase):
    def test_parse_contacts_maps_ids_names_and_relationships(self):
        contacts = parse_contacts(
            "3262379680|蛋蛋|创造玖玖的人,1906310787|鲍鲍|玖玖的小主人"
        )

        self.assertEqual(
            contacts,
            {
                "3262379680": Contact("3262379680", "蛋蛋", "创造玖玖的人"),
                "1906310787": Contact("1906310787", "鲍鲍", "玖玖的小主人"),
            },
        )

    def test_resolve_target_accepts_name_or_user_id(self):
        contacts = parse_contacts(
            "3262379680|蛋蛋|创造玖玖的人,1906310787|鲍鲍|玖玖的小主人"
        )

        self.assertEqual(resolve_target_user_id("鲍鲍", contacts), "1906310787")
        self.assertEqual(resolve_target_user_id("3262379680", contacts), "3262379680")


class ReminderCommandTests(unittest.TestCase):
    def setUp(self):
        self.contacts = parse_contacts(
            "3262379680|蛋蛋|创造玖玖的人,1906310787|鲍鲍|玖玖的小主人"
        )
        self.now = datetime(2026, 7, 9, 12, 0, 0)

    def test_parse_reminder_command_with_message_body(self):
        command = parse_reminder_command("提醒鲍鲍下午考试带笔", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.target_name, "鲍鲍")
        self.assertEqual(command.body, "下午考试带笔")

    def test_parse_reminder_command_with_colon_separator(self):
        command = parse_reminder_command("叫 鲍鲍：吃饭前看一眼准考证", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.body, "吃饭前看一眼准考证")

    def test_parse_ask_command_from_natural_wording(self):
        command = parse_reminder_command("去问问鲍鲍吃药没", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.target_name, "鲍鲍")
        self.assertEqual(command.body, "吃药没")
        self.assertEqual(command.intent, "ask")
        self.assertIsNone(command.due_at)

    def test_parse_tell_command_from_go_give_wording(self):
        command = parse_reminder_command("现在去给鲍鲍考试加油", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.target_name, "鲍鲍")
        self.assertEqual(command.body, "考试加油")
        self.assertEqual(command.intent, "tell")
        self.assertIsNone(command.due_at)

    def test_old_task_prefix_is_no_longer_user_facing_entry(self):
        self.assertIsNone(task_text_from_message("小猫任务：现在去给鲍鲍考试加油"))
        self.assertIsNone(task_text_from_message("任务 半小时后提醒鲍鲍喝水"))
        self.assertIsNone(task_text_from_message("普通聊天：现在去给鲍鲍考试加油"))

    def test_task_management_words(self):
        self.assertTrue(is_task_help_request(""))
        self.assertTrue(is_task_help_request("帮助"))
        self.assertTrue(is_task_list_request("列表"))
        self.assertEqual(parse_task_cancel_request("取消 a8f3c2"), "a8f3c2")
        self.assertEqual(parse_task_cancel_request("取消 #a8f3c2"), "a8f3c2")

    def test_parse_send_tool_command(self):
        command = parse_tool_command_line(
            '!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
        )

        self.assertEqual(
            command,
            CatTaskToolCommand(
                name="send",
                target="鲍鲍",
                action="tell",
                content="考试加油",
            ),
        )

    def test_parse_schedule_tool_command(self):
        command = parse_tool_command_line(
            '!cat_task_schedule target="鲍鲍" action="ask" time="今天16:00" content="考完了吗"'
        )

        self.assertEqual(command.name, "schedule")
        self.assertEqual(command.target, "鲍鲍")
        self.assertEqual(command.action, "ask")
        self.assertEqual(command.time_text, "今天16:00")
        self.assertEqual(command.content, "考完了吗")

    def test_parse_list_and_cancel_tool_commands(self):
        self.assertEqual(parse_tool_command_line("!cat_task_list").name, "list")
        cancel = parse_tool_command_line('!cat_task_cancel id="a8f3c2"')
        self.assertEqual(cancel.name, "cancel")
        self.assertEqual(cancel.task_id, "a8f3c2")

    def test_extract_tool_command_removes_hidden_line(self):
        extraction = extract_tool_command(
            '好嘛，小猫去轻轻说。\n!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
        )

        self.assertEqual(extraction.visible_text, "好嘛，小猫去轻轻说。")
        self.assertEqual(extraction.command.name, "send")
        self.assertEqual(extraction.extra_command_count, 0)

    def test_extract_tool_command_counts_extra_commands(self):
        extraction = extract_tool_command(
            '!cat_task_list\n!cat_task_cancel id="a8f3c2"'
        )

        self.assertEqual(extraction.command.name, "list")
        self.assertEqual(extraction.extra_command_count, 1)

    def test_tool_command_validates_target_and_action(self):
        command = parse_tool_command_line(
            '!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
        )
        reminder = reminder_from_tool_command(command, self.contacts, self.now)

        self.assertEqual(reminder.target_user_id, "1906310787")
        self.assertEqual(reminder.intent, "tell")
        self.assertEqual(reminder.body, "考试加油")

    def test_tool_command_can_target_dandan_by_name(self):
        command = parse_tool_command_line(
            '!cat_task_send target="蛋蛋" action="ask" content="你在干嘛"'
        )
        reminder = reminder_from_tool_command(command, self.contacts, self.now)

        self.assertEqual(reminder.target_user_id, "3262379680")
        self.assertEqual(reminder.target_name, "蛋蛋")
        self.assertEqual(reminder.intent, "ask")
        self.assertEqual(reminder.body, "你在干嘛")

    def test_tool_command_rejects_unknown_target(self):
        command = parse_tool_command_line(
            '!cat_task_send target="陌生人" action="tell" content="考试加油"'
        )

        with self.assertRaisesRegex(ToolCommandError, "联系人"):
            reminder_from_tool_command(command, self.contacts, self.now)

    def test_tool_command_rejects_empty_content_for_tell(self):
        command = parse_tool_command_line(
            '!cat_task_send target="鲍鲍" action="tell" content=""'
        )

        with self.assertRaisesRegex(ToolCommandError, "内容"):
            reminder_from_tool_command(command, self.contacts, self.now)

    def test_schedule_tool_command_parses_time_text(self):
        command = parse_tool_command_line(
            '!cat_task_schedule target="鲍鲍" action="ask" time="今天16:00" content="考完了吗"'
        )
        reminder = reminder_from_tool_command(command, self.contacts, self.now)

        self.assertEqual(reminder.due_at, datetime(2026, 7, 9, 16, 0, 0))

    def test_parse_time_after_contact(self):
        command = parse_reminder_command("提醒鲍鲍半小时后喝水", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.body, "喝水")
        self.assertEqual(command.due_at, datetime(2026, 7, 9, 12, 30, 0))

    def test_parse_go_find_after_clock_time(self):
        command = parse_reminder_command("16:00 去找鲍鲍", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.intent, "call")
        self.assertEqual(command.body, "")
        self.assertEqual(command.due_at, datetime(2026, 7, 9, 16, 0, 0))

    def test_parse_relative_scheduled_command(self):
        command = parse_reminder_command("半小时后叫鲍鲍喝水", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.body, "喝水")
        self.assertEqual(command.due_at, datetime(2026, 7, 9, 12, 30, 0))

    def test_parse_afternoon_scheduled_command(self):
        command = parse_reminder_command("下午三点问问鲍鲍吃药没", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.body, "吃药没")
        self.assertEqual(command.intent, "ask")
        self.assertEqual(command.due_at, datetime(2026, 7, 9, 15, 0, 0))

    def test_parse_tomorrow_morning_scheduled_command(self):
        command = parse_reminder_command("明早八点提醒鲍鲍带药", self.contacts, self.now)

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.body, "带药")
        self.assertEqual(command.due_at, datetime(2026, 7, 10, 8, 0, 0))

    def test_parse_reminder_command_ignores_normal_chat(self):
        self.assertIsNone(parse_reminder_command("你说了吗", self.contacts, self.now))
        self.assertIsNone(parse_reminder_command("鲍鲍今天来了吗", self.contacts, self.now))

    def test_normalize_tool_content_removes_mechanical_sender_prefix(self):
        self.assertEqual(
            normalize_contact_message_body("鲍鲍让我问你，你说的她是谁呀", "鲍鲍"),
            "你说的她是谁呀",
        )
        self.assertEqual(
            normalize_contact_message_body("鲍鲍让我跟你说她讨厌你", "鲍鲍"),
            "鲍鲍讨厌你",
        )
        self.assertEqual(
            normalize_contact_message_body("考试加油", "蛋蛋"),
            "考试加油",
        )

    def test_normalize_reminder_body_updates_command_payload(self):
        sender = self.contacts["1906310787"]
        command = ReminderCommand(
            target_user_id="3262379680",
            target_name="蛋蛋",
            body="鲍鲍让我问你，你说的她是谁呀",
            intent="ask",
        )

        normalized = normalize_reminder_body(command, sender)

        self.assertEqual(normalized.body, "你说的她是谁呀")
        self.assertEqual(normalized.intent, "ask")
        self.assertEqual(normalized.target_user_id, "3262379680")

    def test_build_reminder_message_uses_direct_body(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("提醒鲍鲍下午考试带笔", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "下午考试带笔",
        )

    def test_build_ask_message_uses_direct_body(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("去问问鲍鲍吃药没", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "吃药没",
        )

    def test_build_tell_message_uses_direct_body(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("现在去给鲍鲍考试加油", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "考试加油",
        )

    def test_build_call_message_with_body_uses_direct_body(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("找鲍鲍考试结束了吗", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "考试结束了吗",
        )

    def test_build_immediate_confirmation_includes_action_target_and_body(self):
        command = parse_reminder_command("现在去给鲍鲍考试加油", self.contacts, self.now)

        self.assertEqual(
            build_immediate_confirmation(command),
            "小猫已经把话带给鲍鲍了：考试加油",
        )

    def test_build_scheduled_confirmation_includes_task_context(self):
        command = parse_reminder_command("下午三点问问鲍鲍考完了吗", self.contacts, self.now)
        task = ContactTask(
            task_id="a8f3c2",
            target_user_id="1906310787",
            target_name="鲍鲍",
            sender_user_id="3262379680",
            sender_name="蛋蛋",
            body=command.body,
            intent=command.intent,
            due_at=command.due_at,
            created_at=self.now,
        )

        self.assertEqual(
            build_scheduled_confirmation(task, self.now),
            "小猫记住了，#a8f3c2 今天15:00 去问鲍鲍：考完了吗",
        )

    def test_build_memory_pairs_record_sender_and_target_context(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("现在去给鲍鲍考试加油", self.contacts, self.now)
        sent_text = build_reminder_message(command, sender)
        confirmation = build_immediate_confirmation(command)

        self.assertEqual(
            build_sender_memory_pair(command, sender, confirmation),
            (
                {"role": "user", "content": "小猫任务：蛋蛋让小猫给鲍鲍带话：考试加油"},
                {"role": "assistant", "content": "小猫已经把话带给鲍鲍了：考试加油"},
            ),
        )
        self.assertEqual(
            build_target_memory_pair(command, sender, sent_text),
            (
                {"role": "user", "content": "小猫任务记录：蛋蛋让小猫给鲍鲍带话：考试加油"},
                {"role": "assistant", "content": "考试加油"},
            ),
        )

    def test_parse_self_contact_request_with_ambiguous_afternoon_time(self):
        now = datetime(2026, 7, 9, 13, 5, 0)
        sender = self.contacts["1906310787"]

        command = parse_self_contact_request(
            "玖玖我是四点钟考完试哦，再给你说一声到时候记得来找我",
            sender,
            now,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.target_user_id, "1906310787")
        self.assertEqual(command.target_name, "鲍鲍")
        self.assertEqual(command.intent, "call")
        self.assertEqual(command.body, "到点来找我")
        self.assertEqual(command.due_at, datetime(2026, 7, 9, 16, 0, 0))

    def test_due_time_formatting(self):
        self.assertEqual(
            format_due_time(datetime(2026, 7, 9, 15, 0, 0), self.now),
            "今天15:00",
        )
        self.assertEqual(
            format_due_time(datetime(2026, 7, 10, 8, 0, 0), self.now),
            "明天08:00",
        )


class ContactTaskStateTests(unittest.TestCase):
    def test_due_tasks_returns_pending_due_once(self):
        state = ProactiveState(
            pending_tasks=[
                ContactTask(
                    task_id="a",
                    target_user_id="1906310787",
                    target_name="鲍鲍",
                    sender_user_id="3262379680",
                    sender_name="蛋蛋",
                    body="吃药没",
                    intent="ask",
                    due_at=datetime(2026, 7, 9, 12, 30, 0),
                    created_at=datetime(2026, 7, 9, 12, 0, 0),
                ),
                ContactTask(
                    task_id="b",
                    target_user_id="1906310787",
                    target_name="鲍鲍",
                    sender_user_id="3262379680",
                    sender_name="蛋蛋",
                    body="带药",
                    intent="remind",
                    due_at=datetime(2026, 7, 9, 13, 0, 0),
                    created_at=datetime(2026, 7, 9, 12, 0, 0),
                ),
            ]
        )

        tasks = due_tasks(state, datetime(2026, 7, 9, 12, 31, 0))

        self.assertEqual([task.task_id for task in tasks], ["a"])
        mark_task_done(state, tasks[0], datetime(2026, 7, 9, 12, 31, 0))
        self.assertEqual(due_tasks(state, datetime(2026, 7, 9, 12, 32, 0)), [])
        self.assertEqual(state.pending_tasks[0].status, "done")

    def test_pending_tasks_round_trip_through_state_file(self):
        state = ProactiveState(
            pending_tasks=[
                ContactTask(
                    task_id="a",
                    target_user_id="1906310787",
                    target_name="鲍鲍",
                    sender_user_id="3262379680",
                    sender_name="蛋蛋",
                    body="吃药没",
                    intent="ask",
                    due_at=datetime(2026, 7, 9, 12, 30, 0),
                    created_at=datetime(2026, 7, 9, 12, 0, 0),
                )
            ]
        )

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            save_state(path, state)
            loaded = load_state(path)

        self.assertEqual(len(loaded.pending_tasks), 1)
        self.assertEqual(loaded.pending_tasks[0].target_name, "鲍鲍")
        self.assertEqual(loaded.pending_tasks[0].due_at, datetime(2026, 7, 9, 12, 30, 0))

    def test_format_task_overview_lists_future_fixed_and_proactive_tasks(self):
        now = datetime(2026, 7, 9, 12, 0, 0)
        state = ProactiveState(
            pending_tasks=[
                ContactTask(
                    task_id="8f3a21",
                    target_user_id="1906310787",
                    target_name="鲍鲍",
                    sender_user_id="3262379680",
                    sender_name="蛋蛋",
                    body="考完了吗",
                    intent="ask",
                    due_at=datetime(2026, 7, 9, 16, 0, 0),
                    created_at=now,
                ),
                ContactTask(
                    task_id="done",
                    target_user_id="1906310787",
                    target_name="鲍鲍",
                    sender_user_id="3262379680",
                    sender_name="蛋蛋",
                    body="已经完成",
                    intent="tell",
                    due_at=datetime(2026, 7, 9, 13, 0, 0),
                    created_at=now,
                    status="done",
                ),
            ]
        )
        config = ProactiveConfig(
            enabled=True,
            target="鲍鲍",
            active_start_hour=10,
            active_end_hour=23,
            max_per_day=3,
            min_gap=timedelta(hours=3),
            inactive_after=timedelta(hours=6),
            after_reply_cooldown=timedelta(minutes=60),
        )

        text = format_task_overview(
            state=state,
            now=now,
            morning_hour=8,
            night_hour=23,
            proactive_config=config,
            proactive_target_name="鲍鲍",
        )

        self.assertIn("#8f3a21 今天16:00 去问鲍鲍：考完了吗", text)
        self.assertIn("每天08:00 早安消息：白名单联系人", text)
        self.assertIn("每天23:00 晚安消息：白名单联系人", text)
        self.assertIn("鲍鲍：开启，10:00-23:00，每天最多3次，冷却3小时", text)
        self.assertNotIn("已经完成", text)


class ProactiveGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 8, 15, 0, 0)
        self.config = ProactiveConfig(
            enabled=True,
            target="鲍鲍",
            active_start_hour=10,
            active_end_hour=23,
            max_per_day=3,
            min_gap=timedelta(hours=3),
            inactive_after=timedelta(hours=6),
            after_reply_cooldown=timedelta(minutes=60),
        )

    def test_allows_inactive_target_inside_limits(self):
        state = ProactiveState(
            last_seen_at={"1906310787": self.now - timedelta(hours=7)},
            last_sent_at=self.now - timedelta(hours=4),
            daily_date=date(2026, 7, 8),
            daily_count=1,
        )

        decision = should_send_proactive(
            now=self.now,
            target_user_id="1906310787",
            state=state,
            config=self.config,
            sleeping=False,
            trigger="inactive",
        )

        self.assertTrue(decision.allowed)

    def test_blocks_when_target_recently_replied(self):
        state = ProactiveState(
            last_seen_at={"1906310787": self.now - timedelta(minutes=20)},
            last_sent_at=self.now - timedelta(hours=4),
            daily_date=date(2026, 7, 8),
            daily_count=1,
        )

        decision = should_send_proactive(
            now=self.now,
            target_user_id="1906310787",
            state=state,
            config=self.config,
            sleeping=False,
            trigger="inactive",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "target_recently_seen")

    def test_blocks_when_daily_limit_reached(self):
        state = ProactiveState(
            last_seen_at={"1906310787": self.now - timedelta(hours=7)},
            last_sent_at=self.now - timedelta(hours=4),
            daily_date=date(2026, 7, 8),
            daily_count=3,
        )

        decision = should_send_proactive(
            now=self.now,
            target_user_id="1906310787",
            state=state,
            config=self.config,
            sleeping=False,
            trigger="inactive",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "daily_limit_reached")

    def test_resets_daily_count_on_new_date(self):
        state = ProactiveState(
            last_seen_at={"1906310787": self.now - timedelta(hours=7)},
            last_sent_at=self.now - timedelta(hours=4),
            daily_date=date(2026, 7, 7),
            daily_count=3,
        )

        decision = should_send_proactive(
            now=self.now,
            target_user_id="1906310787",
            state=state,
            config=self.config,
            sleeping=False,
            trigger="inactive",
        )

        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
