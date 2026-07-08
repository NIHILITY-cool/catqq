import unittest
from datetime import date, datetime, timedelta

from data.plugins.astrbot_plugin_cat_guard.proactive import (
    Contact,
    ProactiveConfig,
    ProactiveState,
    parse_contacts,
    resolve_target_user_id,
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
