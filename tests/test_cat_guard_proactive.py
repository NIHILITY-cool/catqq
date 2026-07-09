import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from data.plugins.astrbot_plugin_cat_guard.proactive import (
    Contact,
    ContactTask,
    ProactiveConfig,
    ProactiveState,
    build_reminder_message,
    due_tasks,
    format_due_time,
    mark_task_done,
    parse_contacts,
    parse_reminder_command,
    resolve_target_user_id,
    load_state,
    save_state,
    should_send_proactive,
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

    def test_build_reminder_message_names_sender(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("提醒鲍鲍下午考试带笔", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "蛋蛋让小猫提醒你：下午考试带笔",
        )

    def test_build_ask_message_names_sender(self):
        sender = self.contacts["3262379680"]
        command = parse_reminder_command("去问问鲍鲍吃药没", self.contacts, self.now)

        self.assertEqual(
            build_reminder_message(command, sender),
            "蛋蛋让小猫问你：吃药没",
        )

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
