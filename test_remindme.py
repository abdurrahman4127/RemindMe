import curses
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from remindme import (
    NOTIFICATION_TIMEOUT_MS,
    Reminder,
    ReminderStore,
    QuitRequested,
    TimeChoice,
    TimeClarification,
    THEMES,
    command_palette,
    color_attr,
    default_config_path,
    default_database_path,
    edit_buffer,
    draw,
    draw_key_hints,
    draw_palette_key_hints,
    filter_palette_options,
    load_banner,
    load_theme,
    main,
    notify_due,
    parse_due,
    parse_reminder_request,
    prompt_due,
    run_tui,
    save_theme,
    send_desktop_notification,
    theme_palette_options,
)


class ParseDueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 14, 30, tzinfo=timezone.utc)

    def test_parses_relative_minutes(self):
        self.assertEqual(parse_due("in 20m", self.now), self.now + timedelta(minutes=20))

    def test_parses_combined_duration_components(self):
        self.assertEqual(
            parse_due("1d 3h", self.now),
            self.now + timedelta(hours=27),
        )
        self.assertEqual(
            parse_due("3d 47m", self.now),
            self.now + timedelta(minutes=3 * 24 * 60 + 47),
        )
        self.assertEqual(
            parse_due("in 1d 3h", self.now),
            self.now + timedelta(hours=27),
        )
        self.assertEqual(
            parse_due("1d3h", self.now),
            self.now + timedelta(hours=27),
        )

    def test_rejects_repeated_duration_unit(self):
        with self.assertRaisesRegex(ValueError, "each duration unit only once"):
            parse_due("1d 2d", self.now)

    def test_parses_today_and_tomorrow_with_am_pm(self):
        self.assertEqual(
            parse_due("tomorrow 9:15 AM", self.now),
            datetime(2026, 11, 2, 9, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("today 3:23 PM", self.now),
            datetime(2026, 11, 1, 15, 23, tzinfo=timezone.utc),
        )

    def test_parses_hour_only_am_pm_today_with_optional_spacing(self):
        expected = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
        for value in ("8pm", "8 pm", "today 8pm", "today 8 pm"):
            with self.subTest(value=value):
                self.assertEqual(parse_due(value, self.now), expected)

    def test_parses_am_pm_times_with_minutes_and_defaults_to_zero(self):
        self.assertEqual(
            parse_due("8:07pm", self.now),
            datetime(2026, 11, 1, 20, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("tomorrow 12am", self.now),
            datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("tomorrow 12pm", self.now),
            datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
        )

    def test_parses_bare_hour_before_am_candidate(self):
        early = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)

        self.assertEqual(
            parse_due("8", early),
            datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        )

    def test_bare_hour_after_am_candidate_confirms_pm(self):
        after_morning = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)

        with self.assertRaises(TimeClarification) as caught:
            parse_due("8", after_morning)

        self.assertEqual(caught.exception.choices[0].label, "Use 08:00 PM today")

    def test_bare_hour_after_both_candidates_asks_which_time_tomorrow(self):
        evening = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)

        with self.assertRaises(TimeClarification) as caught:
            parse_due("8", evening)

        self.assertEqual(
            [choice.due_at for choice in caught.exception.choices],
            [
                datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc),
            ],
        )

    def test_past_explicit_today_time_asks_today_or_tomorrow(self):
        late = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)

        with self.assertRaises(TimeClarification) as caught:
            parse_due("8pm", late)

        self.assertEqual(
            [choice.due_at for choice in caught.exception.choices],
            [
                datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc),
            ],
        )

    def test_rejects_invalid_clock_time_values(self):
        for value in ("13pm", "8:60pm", "24:00", "0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_due(value, self.now)

    def test_parses_tomorrow_local_time(self):
        self.assertEqual(
            parse_due("tomorrow 09:15", self.now),
            datetime(2026, 11, 2, 9, 15, tzinfo=timezone.utc),
        )

    def test_rejects_unrecognized_input(self):
        with self.assertRaises(ValueError):
            parse_due("sometime later", self.now)

    def test_parses_day_month_and_12_hour_time(self):
        self.assertEqual(
            parse_due("2/6, 1:23 PM", self.now),
            datetime(2027, 6, 2, 13, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("30/07 1am", self.now),
            datetime(2027, 7, 30, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("30/7 1am", self.now),
            datetime(2027, 7, 30, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_due("25/11, 1am", self.now),
            datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc),
        )

    def test_parses_day_month_name_and_cli_title(self):
        month_names = (
            ("jan", 1),
            ("january", 1),
            ("feb", 2),
            ("february", 2),
            ("mar", 3),
            ("march", 3),
            ("apr", 4),
            ("april", 4),
            ("may", 5),
            ("jun", 6),
            ("june", 6),
            ("jul", 7),
            ("july", 7),
            ("aug", 8),
            ("august", 8),
            ("sep", 9),
            ("sept", 9),
            ("september", 9),
            ("oct", 10),
            ("october", 10),
            ("nov", 11),
            ("november", 11),
            ("dec", 12),
            ("december", 12),
        )
        for month_name, month in month_names:
            day = 15
            year = 2026 if month >= 11 else 2027
            date_text = f"{day} {month_name}"
            with self.subTest(month_name=month_name):
                title, due_at = parse_reminder_request(
                    [date_text + ",", "1am", "linkedin", "post"],
                    self.now,
                )
                self.assertEqual(title, "linkedin post")
                self.assertEqual(
                    due_at,
                    datetime(year, month, day, 1, 0, tzinfo=timezone.utc),
                )

        title, due_at = parse_reminder_request(
            ["25", "nov,", "1am", "linkedin", "post"],
            self.now,
        )
        self.assertEqual(title, "linkedin post")
        self.assertEqual(
            due_at,
            datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc),
        )

    def test_december_date_does_not_match_day_duration_prefix(self):
        title, due_at = parse_reminder_request(
            ["16", "dec,", "11am", "linkedin", "post"],
            self.now,
        )

        self.assertEqual(title, "linkedin post")
        self.assertEqual(due_at, datetime(2026, 12, 16, 11, 0, tzinfo=timezone.utc))

    def test_named_month_date_supports_on_at_and_case_variants(self):
        title, due_at = parse_reminder_request(
            ["on", "16", "DeCeMbEr", "at", "11:00", "AM", "linkedin", "post"],
            self.now,
        )

        self.assertEqual(title, "linkedin post")
        self.assertEqual(due_at, datetime(2026, 12, 16, 11, 0, tzinfo=timezone.utc))

    def test_named_month_date_skips_invalid_calendar_occurrences(self):
        self.assertEqual(
            parse_due("29 feb, 8am", self.now),
            datetime(2028, 2, 29, 8, 0, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ValueError, "valid upcoming date"):
            parse_due("31 apr, 8am", self.now)

    def test_parses_absolute_time_with_optional_title(self):
        title, due_at = parse_reminder_request(
            ["on", "2/6", "at", "1:23", "PM", "Call", "Sam"],
            self.now,
        )
        self.assertEqual(title, "Call Sam")
        self.assertEqual(due_at, datetime(2027, 6, 2, 13, 23, tzinfo=timezone.utc))

    def test_parses_day_month_with_hour_only_time_and_quoted_title(self):
        title, due_at = parse_reminder_request(
            ["25/11", "1am", "linkedin post"],
            self.now,
        )

        self.assertEqual(title, "linkedin post")
        self.assertEqual(
            due_at,
            datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc),
        )

    def test_rejects_year_based_date_input(self):
        with self.assertRaises(ValueError):
            parse_due("2026-11-25 01:00", self.now)

    def test_parses_compact_clock_time_followed_by_title(self):
        for time_phrase in ("8pm", "8 pm", "today 8pm"):
            with self.subTest(time_phrase=time_phrase):
                title, due_at = parse_reminder_request(
                    [*time_phrase.split(), "Call", "Mom"],
                    self.now,
                )
                self.assertEqual(title, "Call Mom")
                self.assertEqual(
                    due_at,
                    datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc),
                )

    def test_reminder_request_can_resolve_a_time_clarification(self):
        evening = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)

        def clarify(error):
            self.assertIn("has passed", error.question)
            return error.choices[0].due_at

        title, due_at = parse_reminder_request(
            ["8", "Call", "Mom"],
            datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
            clarify,
        )

        self.assertEqual(title, "Call Mom")
        self.assertEqual(due_at, evening)

    def test_interactive_due_prompt_applies_selected_clarification(self):
        evening = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
        clarification = TimeClarification(
            "The morning time has passed.",
            (TimeChoice("Use 08:00 PM today", evening),),
        )
        with (
            patch("remindme.parse_due", side_effect=clarification),
            patch("remindme.prompt", return_value="1"),
        ):
            due_at, error = prompt_due(object(), "8")

        self.assertEqual(due_at, evening)
        self.assertIsNone(error)

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

    def test_parses_combined_duration_with_title(self):
        title, due_at = parse_reminder_request(
            ["1d", "3h", "Call", "Sam"],
            self.now,
        )
        self.assertEqual(title, "Call Sam")
        self.assertEqual(due_at, self.now + timedelta(hours=27))

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
            with closing(ReminderStore(database)) as store:
                self.assertEqual(store.list_reminders()[0].title, "Call Sam")

    def test_cli_time_and_note_flags_create_reminder_in_either_order(self):
        for flag_order in (
            ["-t", "in 2h", "-n", "Call Sam"],
            ["-n", "Call Sam", "-t", "in 2h"],
        ):
            with self.subTest(flag_order=flag_order), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "reminders.db"
                with patch("remindme.curses.wrapper") as tui:
                    result = main(["--database", str(database), *flag_order])
                self.assertEqual(result, 0)
                tui.assert_not_called()
                with closing(ReminderStore(database)) as store:
                    self.assertEqual(store.list_reminders()[0].title, "Call Sam")

    def test_cli_time_flag_defaults_note_to_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            with patch("remindme.curses.wrapper") as tui:
                result = main(["--database", str(database), "--time", "in 2h"])
            self.assertEqual(result, 0)
            tui.assert_not_called()
            with closing(ReminderStore(database)) as store:
                self.assertEqual(store.list_reminders()[0].title, "Reminder")

    def test_cli_note_flag_requires_time_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                main(["--database", str(database), "-n", "Call Sam"])
            self.assertEqual(error.exception.code, 2)
            self.assertIn("-n/--note requires -t/--time", stderr.getvalue())

    def test_cli_rejects_mixing_flags_with_positional_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            stderr = io.StringIO()
            with (
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as error,
            ):
                main(
                    [
                        "--database",
                        str(database),
                        "-t",
                        "in 2h",
                        "Call",
                        "Sam",
                    ]
                )
            self.assertEqual(error.exception.code, 2)
            self.assertIn("not both", stderr.getvalue())

    def test_bang_command_entrypoints_create_reminders(self):
        if os.name == "nt":
            self.skipTest("Unix shell launchers are not used on Windows")
        for command in ("!remindme", "!remind"):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
            ):
                database = Path(directory) / "reminders.db"
                result = subprocess.run(
                    [
                        str(Path(__file__).parent / command),
                        "--database",
                        str(database),
                        "in",
                        "2h",
                        "Call",
                        "Sam",
                    ],
                    capture_output=True,
                    check=True,
                    text=True,
                )

                self.assertIn("Added: Call Sam", result.stdout)
                with closing(ReminderStore(database)) as store:
                    self.assertEqual(store.list_reminders()[0].title, "Call Sam")

    @unittest.skipUnless(os.name == "nt", "Windows user-data paths")
    def test_windows_data_and_config_use_appdata(self):
        self.assertEqual(default_database_path().parent.name, "RemindMe")
        self.assertEqual(
            default_database_path().parent.parent,
            Path(os.environ["LOCALAPPDATA"]),
        )
        self.assertEqual(default_config_path().parent.name, "RemindMe")
        self.assertEqual(
            default_config_path().parent.parent,
            Path(os.environ["APPDATA"]),
        )

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


class CommandPaletteTests(unittest.TestCase):
    def test_theme_choices_only_appear_in_the_theme_picker(self):
        suggestions = filter_palette_options("theme")

        commands = [option.command for option in suggestions]
        self.assertEqual(commands, ["theme"])
        self.assertIn("forest", [option.command for option in theme_palette_options()])

    def test_palette_searches_command_descriptions(self):
        suggestions = filter_palette_options("10 minutes")

        self.assertTrue(any(option.command == "snooze" for option in suggestions))

    def test_palette_accepts_slash_prefixed_query(self):
        suggestions = filter_palette_options("/theme")

        self.assertEqual([option.command for option in suggestions], ["theme"])

    def test_theme_selection_is_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"

            save_theme("ocean", config_path)

            self.assertEqual(load_theme(config_path), "ocean")

    def test_unknown_theme_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"

            with self.assertRaisesRegex(ValueError, "Unknown !RemindMe theme"):
                save_theme("ultraviolet", config_path)

    def test_default_theme_preserves_original_plain_style(self):
        with patch("remindme.ACTIVE_THEME", "default"):
            self.assertEqual(color_attr("selected"), curses.A_REVERSE)
            self.assertEqual(color_attr("header", curses.A_BOLD), curses.A_BOLD)

    def test_large_theme_collection_is_available(self):
        self.assertGreaterEqual(len(THEMES), 20)
        self.assertIsNone(THEMES["default"])
        self.assertIn("slate-blue", THEMES)
        self.assertIn("github-dark", THEMES)

    def test_palette_is_bottom_docked_and_runs_typed_command(self):
        class FakeWindow:
            def __init__(self, keys):
                self.keys = iter(keys)
                self.writes = []
                self.moves = []
                self.events = []

            def keypad(self, _enabled):
                pass

            def erase(self):
                self.events.append("panel_erase")

            def addnstr(self, *args):
                self.writes.append(args)

            def attrset(self, _attribute):
                pass

            def move(self, *args):
                self.moves.append(args)

            def refresh(self):
                self.events.append("panel_refresh")

            def get_wch(self):
                return next(self.keys)

        class FakeScreen:
            def __init__(self):
                self.events = []

            def getmaxyx(self):
                return 24, 80

            def touchwin(self):
                self.events.append("screen_touch")

            def refresh(self):
                self.events.append("screen_refresh")

        keys = [*("oce"), "\t", "\n"]
        panel = FakeWindow(keys)
        screen = FakeScreen()
        with (
            patch("remindme.curses.newwin", return_value=panel) as newwin,
            patch("remindme.curses.curs_set"),
            patch("remindme.ACTIVE_THEME", "default"),
        ):
            command = command_palette(
                screen,
                "default",
                theme_palette_options(),
                "Choose Theme",
            )

        self.assertEqual(command, "ocean")
        self.assertEqual(newwin.call_args.args, (13, 80, 11, 0))
        self.assertIn((10, 1 + len("› /ocean")), panel.moves)
        self.assertTrue(any("› /ocean" in write[2] for write in panel.writes))
        self.assertEqual(
            panel.events[-2:],
            ["panel_erase", "panel_refresh"],
        )
        self.assertEqual(screen.events, ["screen_touch", "screen_refresh"])

    def test_theme_palette_previews_highlighted_theme_and_restores_on_cancel(self):
        class FakeWindow:
            def __init__(self, keys):
                self.keys = iter(keys)

            def keypad(self, _enabled):
                pass

            def erase(self):
                pass

            def addnstr(self, *args):
                pass

            def move(self, *args):
                pass

            def refresh(self):
                pass

            def get_wch(self):
                return next(self.keys)

        class FakeScreen:
            def getmaxyx(self):
                return 24, 80

            def touchwin(self):
                pass

            def refresh(self):
                pass

        previews = []
        panel = FakeWindow((curses.KEY_DOWN, "\x1b"))
        with (
            patch("remindme.curses.newwin", return_value=panel),
            patch("remindme.curses.curs_set"),
            patch("remindme.curses.has_colors", return_value=False),
            patch("remindme.ACTIVE_THEME", "ocean"),
        ):
            result = command_palette(
                FakeScreen(),
                "ocean",
                theme_palette_options(),
                "Choose Theme",
                previews.append,
            )

        self.assertIsNone(result)
        self.assertEqual(previews, ["ocean", "forest", None])

    def test_palette_hints_style_keys_without_the_colon(self):
        class FakeWindow:
            def __init__(self):
                self.writes = []

            def addnstr(self, *args):
                self.writes.append(args)

        window = FakeWindow()
        draw_palette_key_hints(window, 0, 80)

        tab_key = next(write for write in window.writes if write[2] == "tab")
        colon_action = next(write for write in window.writes if write[2] == ": complete")
        self.assertEqual(tab_key[4], curses.A_REVERSE | curses.A_BOLD)
        self.assertEqual(colon_action[4], curses.A_DIM)

    def test_key_hints_shade_padded_key_only_and_omit_colon(self):
        class FakeWindow:
            def __init__(self):
                self.writes = []

            def addstr(self, *args):
                self.writes.append(args)

        window = FakeWindow()
        draw_key_hints(window, 0, 100, "default")
        chip = next(write for write in window.writes if len(write) == 4 and write[2] == " a ")
        self.assertEqual(chip[:3], (0, 0, " a "))
        self.assertEqual(chip[3], curses.A_REVERSE | curses.A_BOLD)
        self.assertIn((0, 3, " add", curses.A_NORMAL), window.writes)
        self.assertNotIn((0, 3, ": add", curses.A_NORMAL), window.writes)

    def test_reminder_list_shows_due_time_with_am_pm(self):
        class FakeWindow:
            def __init__(self):
                self.writes = []

            def erase(self):
                pass

            def getmaxyx(self):
                return 24, 100

            def addnstr(self, *args):
                self.writes.append(args)

            def addstr(self, *args):
                self.writes.append(args)

            def refresh(self):
                pass

        due_at = (datetime.now().astimezone() + timedelta(days=1)).replace(
            hour=19,
            minute=43,
            second=0,
            microsecond=0,
        )
        window = FakeWindow()
        draw(
            window,
            [Reminder(1, "Review paper", due_at, False, False)],
            0,
            "",
            Path("/tmp/reminders.sqlite3"),
            (),
            "default",
        )

        expected = due_at.strftime("%a %d %b %I:%M %p")
        self.assertTrue(any(expected in write[2] for write in window.writes))

    def test_invalid_theme_config_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "expected a JSON object"):
                load_theme(config_path)


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


class TuiQuitTests(unittest.TestCase):
    def test_escape_and_q_do_not_quit_but_ctrl_q_does(self):
        class FakeScreen:
            def __init__(self):
                self.keys = iter((27, ord("q"), 27, 17))
                self.read_count = 0

            def keypad(self, _enabled):
                pass

            def getch(self):
                self.read_count += 1
                return next(self.keys)

        with tempfile.TemporaryDirectory() as directory:
            with closing(ReminderStore(Path(directory) / "reminders.db")) as store:
                screen = FakeScreen()
                with (
                    patch("remindme.load_banner", return_value=()),
                    patch("remindme.load_theme", return_value="default"),
                    patch("remindme.apply_theme"),
                    patch("remindme.curses.curs_set"),
                    patch("remindme.draw"),
                ):
                    run_tui(screen, store)

        self.assertEqual(screen.read_count, 4)

    def test_ctrl_q_quits(self):
        class FakeScreen:
            def __init__(self):
                self.read_count = 0

            def keypad(self, _enabled):
                pass

            def getch(self):
                self.read_count += 1
                return 17

        with tempfile.TemporaryDirectory() as directory:
            with closing(ReminderStore(Path(directory) / "reminders.db")) as store:
                screen = FakeScreen()
                with (
                    patch("remindme.load_banner", return_value=()),
                    patch("remindme.load_theme", return_value="default"),
                    patch("remindme.apply_theme"),
                    patch("remindme.curses.curs_set"),
                    patch("remindme.draw"),
                ):
                    run_tui(screen, store)

        self.assertEqual(screen.read_count, 1)

    def test_main_handles_ctrl_q_from_modal_input_as_clean_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reminders.db"
            with patch("remindme.curses.wrapper", side_effect=QuitRequested):
                result = main(["--database", str(database)])

        self.assertEqual(result, 0)

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
    @unittest.skipIf(os.name == "nt", "Linux notify-send behavior")
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
                "--app-name=!RemindMe",
                "Reminder",
                "Test reminder",
            ],
        )
        self.assertEqual(NOTIFICATION_TIMEOUT_MS, 30_000)

    @unittest.skipUnless(os.name == "nt", "Windows toast notifications")
    @patch("winotify.Notification")
    def test_windows_desktop_notification_uses_toast(self, notification):
        reminder = Reminder(1, "Test reminder", self.now, False, False)

        send_desktop_notification(reminder)

        notification.assert_called_once_with(
            app_id="!RemindMe",
            title="Reminder",
            msg="Test reminder",
        )
        notification.return_value.show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
