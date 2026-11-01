import curses
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from remindme import (
    NOTIFICATION_TIMEOUT_MS,
    Reminder,
    ReminderStore,
    edit_buffer,
    load_banner,
    main,
    notify_due,
    parse_due,
    parse_reminder_request,
    send_desktop_notification,
)


class ParseDueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 14, 30, tzinfo=timezone.utc)

    def test_parses_relative_minutes(self):
        self.assertEqual(parse_due("in 20m", self.now), self.now + timedelta(minutes=20))

    def test_parses_tomorrow_local_time(self):
        self.assertEqual(
            parse_due("tomorrow 09:15", self.now),
            datetime(2026, 11, 2, 9, 15, tzinfo=timezone.utc),
        )

    def test_rejects_unrecognized_input(self):
        with self.assertRaises(ValueError):
            parse_due("sometime later", self.now)

    def test_parses_month_day_and_12_hour_time(self):
        self.assertEqual(
            parse_due("2 Jun, 1:23 PM", self.now),
            datetime(2027, 6, 2, 13, 23, tzinfo=timezone.utc),
        )

    def test_parses_absolute_time_with_optional_title(self):
        title, due_at = parse_reminder_request(
            ["on", "2", "Jun", "at", "1:23", "PM", "Call", "Sam"],
            self.now,
        )
        self.assertEqual(title, "Call Sam")
        self.assertEqual(due_at, datetime(2027, 6, 2, 13, 23, tzinfo=timezone.utc))

    def test_parses_relative_time_and_defaults_title(self):
        title, due_at = parse_reminder_request(["in", "2h"], self.now)
        self.assertEqual(title, "Reminder")
        self.assertEqual(due_at, self.now + timedelta(hours=2))

    def test_parses_relative_time_and_title(self):
        title, due_at = parse_reminder_request(
            ["in", "20m", "Take", "medicine"], self.now
        )
        self.assertEqual(title, "Take medicine")
        self.assertEqual(due_at, self.now + timedelta(minutes=20))

    def test_cli_arguments_create_reminder_without_opening_tui(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            with patch("remindme.curses.wrapper") as tui:
                result = main(
                    [
                        "--database",
                        str(database),
                        "in",
                        "2h",
                        "Call",
                        "Sam",
                    ]
                )
            self.assertEqual(result, 0)
            tui.assert_not_called()
            store = ReminderStore(database)
            self.addCleanup(store.close)
            self.assertEqual(store.list_reminders()[0].title, "Call Sam")

    def test_no_arguments_open_the_tui(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            with (
                patch("remindme.default_database_path", return_value=database),
                patch("remindme.curses.wrapper") as tui,
            ):
                result = main([])
            self.assertEqual(result, 0)
            tui.assert_called_once()


class BannerTests(unittest.TestCase):
    def test_banner_is_loaded_from_dedicated_asset(self):
        banner = load_banner()

        self.assertEqual(len(banner), 10)
        self.assertTrue(banner[0].startswith("██▀███"))
        self.assertTrue(banner[-1].endswith("░"))

    def test_empty_banner_file_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "banner.txt"
            path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "banner file is empty"):
                load_banner(path)


class InputEditingTests(unittest.TestCase):
    def test_left_arrow_moves_cursor_without_deleting_text(self):
        text, cursor, submitted = edit_buffer("reminder", 8, curses.KEY_LEFT)

        self.assertEqual(text, "reminder")
        self.assertEqual(cursor, 7)
        self.assertFalse(submitted)

    def test_typing_inserts_at_cursor(self):
        text, cursor, submitted = edit_buffer("rminder", 1, "e")

        self.assertEqual(text, "reminder")
        self.assertEqual(cursor, 2)
        self.assertFalse(submitted)

    def test_delete_removes_character_after_cursor(self):
        text, cursor, submitted = edit_buffer(
            "remxinder", 3, curses.KEY_DC
        )

        self.assertEqual(text, "reminder")
        self.assertEqual(cursor, 3)
        self.assertFalse(submitted)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = ReminderStore(Path(self.temporary_directory.name) / "reminders.db")
        self.addCleanup(self.store.close)
        self.now = datetime.now().astimezone()
        self.store.add("Test reminder", self.now - timedelta(seconds=1))

    def test_successful_notification_is_not_repeated(self):
        notified = []
        send = lambda reminder: notified.append(reminder.title)

        self.assertEqual(notify_due(self.store, send), [])
        self.assertEqual(notify_due(self.store, send), [])
        self.assertEqual(notified, ["Test reminder"])

    def test_failed_notification_remains_retryable(self):
        def fail(_reminder):
            raise OSError("notification daemon unavailable")

        failures = notify_due(self.store, fail)
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(self.store.due_unnotified(self.now)), 1)

    @patch("remindme.subprocess.run")
    @patch("remindme.shutil.which", return_value="/usr/bin/notify-send")
    def test_desktop_notification_requests_30_second_timeout(
        self, _which, run
    ):
        reminder = Reminder(1, "Test reminder", self.now, False, False)

        send_desktop_notification(reminder)

        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/notify-send",
                f"--expire-time={NOTIFICATION_TIMEOUT_MS}",
                "--app-name=RemindMe",
                "Reminder",
                "Test reminder",
            ],
        )
        self.assertEqual(NOTIFICATION_TIMEOUT_MS, 30_000)


if __name__ == "__main__":
    unittest.main()
