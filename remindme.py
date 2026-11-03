#!/usr/bin/env python3
"""A small Linux terminal reminder app with optional desktop notifications."""

from __future__ import annotations

import argparse
import curses
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

POLL_SECONDS = 10
SNOOZE_MINUTES = 10
NOTIFICATION_TIMEOUT_MS = 30_000
DEFAULT_THEME = "default"
THEMES = {
    "default": None,
    "copilot": ("#8b949e", "#2f81f7", "#ffffff", "#1f6feb", "#d29922", "#3fb950"),
    "github-dark": ("#c9d1d9", "#58a6ff", "#ffffff", "#1f6feb", "#d29922", "#8b949e"),
    "github-light": ("#24292f", "#0969da", "#ffffff", "#0969da", "#9a6700", "#57606a"),
    "catppuccin-mocha": ("#cba6f7", "#89b4fa", "#1e1e2e", "#cba6f7", "#f9e2af", "#a6adc8"),
    "catppuccin-latte": ("#8839ef", "#1e66f5", "#eff1f5", "#8839ef", "#df8e1d", "#6c6f85"),
    "dracula": ("#bd93f9", "#8be9fd", "#282a36", "#bd93f9", "#f1fa8c", "#6272a4"),
    "nord": ("#88c0d0", "#81a1c1", "#2e3440", "#88c0d0", "#ebcb8b", "#d8dee9"),
    "gruvbox-dark": ("#fabd2f", "#83a598", "#282828", "#d79921", "#fe8019", "#a89984"),
    "gruvbox-light": ("#b57614", "#076678", "#fbf1c7", "#b57614", "#af3a03", "#928374"),
    "tokyo-night": ("#7aa2f7", "#7dcfff", "#1a1b26", "#3d59a1", "#e0af68", "#565f89"),
    "one-dark": ("#61afef", "#56b6c2", "#282c34", "#61afef", "#e5c07b", "#abb2bf"),
    "monokai": ("#f92672", "#66d9ef", "#272822", "#75715e", "#e6db74", "#a6a6a0"),
    "solarized-dark": ("#268bd2", "#2aa198", "#002b36", "#268bd2", "#b58900", "#839496"),
    "solarized-light": ("#268bd2", "#2aa198", "#fdf6e3", "#268bd2", "#b58900", "#657b83"),
    "rose-pine": ("#c4a7e7", "#9ccfd8", "#191724", "#6e6a86", "#f6c177", "#908caa"),
    "kanagawa": ("#7e9cd8", "#7fb4ca", "#1f1f28", "#2d4f67", "#e6c384", "#727169"),
    "everforest": ("#a7c080", "#83c092", "#2d353b", "#a7c080", "#dbbc7f", "#859289"),
    "matrix": ("#00ff41", "#008f11", "#000000", "#003b00", "#ccff00", "#008f11"),
    "synthwave": ("#ff7edb", "#36f9f6", "#262335", "#b893ce", "#fede5d", "#848bbd"),
    "ocean": ("#56b6c2", "#61afef", "#1b2b34", "#264b59", "#fac863", "#65737e"),
    "forest": ("#a3be8c", "#8fbcbb", "#202b22", "#3b5b4b", "#ebcb8b", "#879a82"),
    "ember": ("#ff6e6e", "#ffb86c", "#28201c", "#a63a50", "#ffcc66", "#b0a39a"),
    "high-contrast": ("#ffffff", "#00ffff", "#000000", "#ffffff", "#ffff00", "#ffffff"),
}

COLOR_PAIRS = {
    "header": 1,
    "accent": 2,
    "selected": 3,
    "due": 4,
    "done": 5,
    "muted": 6,
}
ACTIVE_THEME = DEFAULT_THEME


@dataclass(frozen=True)
class Reminder:
    id: int
    title: str
    due_at: datetime
    done: bool
    notified: bool


@dataclass(frozen=True)
class PaletteOption:
    command: str
    description: str


class QuitRequested(Exception):
    pass


def theme_palette_options() -> tuple[PaletteOption, ...]:
    return tuple(
        PaletteOption(name, f"Switch to the {name} theme")
        for name in THEMES
    )


PALETTE_OPTIONS = (
    PaletteOption("add", "Add a reminder"),
    PaletteOption("edit", "Edit the selected reminder"),
    PaletteOption("done", "Toggle done on the selected reminder"),
    PaletteOption("snooze", "Snooze the selected reminder for 10 minutes"),
    PaletteOption("delete", "Delete the selected reminder"),
    PaletteOption("theme", "Choose a color theme"),
    PaletteOption("help", "Show key and command help"),
    PaletteOption("quit", "Exit RemindMe"),
)


def default_database_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "remindme" / "reminders.sqlite3"


def default_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "remindme" / "config.json"


def load_theme(path: Path | None = None) -> str:
    config_path = path or default_config_path()
    if not config_path.exists():
        return DEFAULT_THEME
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Invalid RemindMe config in {config_path}: expected a JSON object")
    theme = config.get("theme", DEFAULT_THEME)
    if not isinstance(theme, str) or theme not in THEMES:
        raise ValueError(f"Unknown RemindMe theme in {config_path}: {theme}")
    return theme


def save_theme(theme: str, path: Path | None = None) -> None:
    if theme not in THEMES:
        raise ValueError(f"Unknown RemindMe theme: {theme}")
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"theme": theme}, indent=2) + "\n",
        encoding="utf-8",
    )


def apply_theme(theme: str) -> None:
    global ACTIVE_THEME
    ACTIVE_THEME = theme
    if not curses.has_colors() or theme == DEFAULT_THEME:
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    palette = THEMES[theme]
    assert palette is not None
    header, accent, selected_bg, due, done, muted = palette
    selected_fg = contrasting_foreground(selected_bg)
    colors = {
        "header": header,
        "accent": accent,
        "selected": selected_fg,
        "due": due,
        "done": done,
        "muted": muted,
    }
    for role, pair in COLOR_PAIRS.items():
        foreground = hex_to_terminal_color(colors[role])
        pair_background = (
            hex_to_terminal_color(selected_bg) if role == "selected" else background
        )
        curses.init_pair(pair, foreground, pair_background)


def contrasting_foreground(background: str) -> str:
    rgb = tuple(int(background[index : index + 2], 16) for index in (1, 3, 5))
    brightness = sum(
        channel * weight for channel, weight in zip(rgb, (299, 587, 114))
    ) / 1000
    return "#000000" if brightness > 145 else "#ffffff"


def hex_to_terminal_color(value: str) -> int:
    rgb = tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
    if curses.COLORS >= 256:
        red, green, blue = rgb
        levels = (0, 95, 135, 175, 215, 255)
        cube = tuple(min(range(6), key=lambda i: abs(levels[i] - channel)) for channel in rgb)
        cube_color = 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
        gray_level = min(range(24), key=lambda i: abs(8 + i * 10 - sum(rgb) // 3))
        gray_color = 232 + gray_level

        def distance(index: int) -> int:
            if index == gray_color:
                color = (8 + gray_level * 10,) * 3
            else:
                color = tuple(levels[i] for i in (
                    (index - 16) // 36,
                    ((index - 16) // 6) % 6,
                    (index - 16) % 6,
                ))
            return sum((a - b) ** 2 for a, b in zip(rgb, color))

        return min((cube_color, gray_color), key=distance)

    basic = (
        (0, (0, 0, 0)),
        (1, (205, 0, 0)),
        (2, (0, 205, 0)),
        (3, (205, 205, 0)),
        (4, (0, 0, 238)),
        (5, (205, 0, 205)),
        (6, (0, 205, 205)),
        (7, (229, 229, 229)),
        (8, (127, 127, 127)),
        (9, (255, 0, 0)),
        (10, (0, 255, 0)),
        (11, (255, 255, 0)),
        (12, (92, 92, 255)),
        (13, (255, 0, 255)),
        (14, (0, 255, 255)),
        (15, (255, 255, 255)),
    )
    usable = basic[: max(8, min(curses.COLORS, len(basic)))]
    return min(
        usable,
        key=lambda item: sum((a - b) ** 2 for a, b in zip(rgb, item[1])),
    )[0]


def filter_palette_options(query: str) -> list[PaletteOption]:
    terms = query.strip().lower().lstrip("/").split()
    if not terms:
        return list(PALETTE_OPTIONS)
    matches = [
        option
        for option in PALETTE_OPTIONS
        if all(
            term in f"{option.command} {option.description}".lower()
            for term in terms
        )
    ]
    return sorted(
        matches,
        key=lambda option: (
            not option.command.startswith(" ".join(terms)),
            len(option.command),
        ),
    )


def load_banner(path: Path | None = None) -> tuple[str, ...]:
    banner_path = path or Path(__file__).resolve().parent / "assets" / "banner.txt"
    lines = tuple(banner_path.read_text(encoding="utf-8").splitlines())
    if not lines:
        raise ValueError(f"ASCII banner file is empty: {banner_path}")
    return lines


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            due_at TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    db.commit()
    return db


def encode_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat()


def decode_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def reminder_from_row(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        title=row["title"],
        due_at=decode_datetime(row["due_at"]),
        done=bool(row["done"]),
        notified=bool(row["notified"]),
    )


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self.db = connect_database(path)

    def close(self) -> None:
        self.db.close()

    def list_reminders(self) -> list[Reminder]:
        rows = self.db.execute(
            """
            SELECT * FROM reminders
            ORDER BY done ASC, due_at ASC, id ASC
            """
        ).fetchall()
        return [reminder_from_row(row) for row in rows]

    def add(self, title: str, due_at: datetime) -> None:
        title = title.strip()
        if not title:
            raise ValueError("Reminder title cannot be empty.")
        now = encode_datetime(datetime.now().astimezone())
        self.db.execute(
            "INSERT INTO reminders (title, due_at, created_at) VALUES (?, ?, ?)",
            (title, encode_datetime(due_at), now),
        )
        self.db.commit()

    def update(self, reminder_id: int, title: str, due_at: datetime) -> None:
        title = title.strip()
        if not title:
            raise ValueError("Reminder title cannot be empty.")
        self.db.execute(
            "UPDATE reminders SET title = ?, due_at = ?, notified = 0 WHERE id = ?",
            (title, encode_datetime(due_at), reminder_id),
        )
        self.db.commit()

    def set_done(self, reminder_id: int, done: bool) -> None:
        self.db.execute(
            "UPDATE reminders SET done = ?, notified = 0 WHERE id = ?",
            (int(done), reminder_id),
        )
        self.db.commit()

    def delete(self, reminder_id: int) -> None:
        self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        self.db.commit()

    def snooze(self, reminder_id: int, minutes: int = SNOOZE_MINUTES) -> None:
        due_at = datetime.now().astimezone() + timedelta(minutes=minutes)
        self.db.execute(
            "UPDATE reminders SET due_at = ?, notified = 0, done = 0 WHERE id = ?",
            (encode_datetime(due_at), reminder_id),
        )
        self.db.commit()

    def due_unnotified(self, now: datetime) -> list[Reminder]:
        rows = self.db.execute(
            """
            SELECT * FROM reminders
            WHERE done = 0 AND notified = 0 AND due_at <= ?
            ORDER BY due_at ASC, id ASC
            """,
            (encode_datetime(now),),
        ).fetchall()
        return [reminder_from_row(row) for row in rows]

    def set_notified(self, reminder_id: int) -> None:
        self.db.execute(
            "UPDATE reminders SET notified = 1 WHERE id = ? AND done = 0",
            (reminder_id,),
        )
        self.db.commit()

    def set_unnotified(self, reminder_id: int) -> None:
        self.db.execute(
            "UPDATE reminders SET notified = 0 WHERE id = ? AND done = 0",
            (reminder_id,),
        )
        self.db.commit()


def parse_due(value: str, now: datetime | None = None) -> datetime:
    """Parse a local date/time or a relative duration such as 'in 1d 3h'."""
    now = now or datetime.now().astimezone()
    value = value.strip().lower()
    relative = re.fullmatch(r"(?:in\s+)?((?:\d+\s*[dhm]\s*)+)", value)
    if relative:
        parts = re.findall(r"(\d+)\s*([dhm])", relative.group(1))
        units = [unit for _, unit in parts]
        if len(set(units)) != len(units):
            raise ValueError("Use each duration unit only once: d, h, m.")
        duration = sum(
            (
                timedelta(days=int(amount))
                if unit == "d"
                else timedelta(hours=int(amount))
                if unit == "h"
                else timedelta(minutes=int(amount))
                for amount, unit in parts
            ),
            timedelta(),
        )
        return now + duration

    absolute = re.fullmatch(
        r"(?:on\s+)?(\d{1,2}\s+[a-z]{3,9}),?\s+(?:at\s+)?"
        r"(\d{1,2}:\d{2}\s*(?:am|pm))",
        value,
    )
    if absolute:
        day_month, clock = absolute.groups()
        parsed_date = None
        for date_format in ("%d %b %Y %I:%M %p", "%d %B %Y %I:%M %p"):
            try:
                parsed_date = datetime.strptime(
                    f"{day_month} 2000 {clock}".upper(), date_format
                )
                break
            except ValueError:
                continue
        if parsed_date is None:
            raise ValueError("Use a date like '2 Jun, 1:23 PM'.")
        for year in range(now.year, now.year + 9):
            try:
                candidate = parsed_date.replace(year=year, tzinfo=now.tzinfo)
            except ValueError:
                continue
            if candidate > now:
                return candidate
        raise ValueError("Could not find a valid upcoming date.")

    if value.startswith("tomorrow "):
        time_value = value.removeprefix("tomorrow ").strip()
        target_date = (now + timedelta(days=1)).date()
    elif value.startswith("today "):
        time_value = value.removeprefix("today ").strip()
        target_date = now.date()
    else:
        for date_format in (
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %I:%M%p",
        ):
            try:
                return datetime.strptime(value.upper(), date_format).replace(
                    tzinfo=now.tzinfo
                )
            except ValueError:
                pass
        raise ValueError(
            "Use YYYY-MM-DD HH:MM, today HH:MM, tomorrow HH:MM, or in 20m/2h/1d."
        )

    for time_format in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            parsed_time = datetime.strptime(time_value.upper(), time_format).time()
            return datetime.combine(target_date, parsed_time, tzinfo=now.tzinfo)
        except ValueError:
            pass
    raise ValueError("Time must look like HH:MM or 9:30PM.")


def parse_reminder_request(
    arguments: list[str], now: datetime | None = None
) -> tuple[str, datetime]:
    """Parse a due-time phrase followed by an optional reminder title."""
    request = " ".join(arguments).strip()
    relative = re.match(
        r"^(?:in\s+)?((?:\d+\s*[dhm]\s*)+)",
        request,
        re.IGNORECASE,
    )
    if relative:
        due_text = relative.group(0).strip()
        if due_text.lower().startswith("in "):
            due_text = due_text[3:].strip()
        title = request[relative.end() :].strip() or "Reminder"
        return title, parse_due(due_text, now)

    absolute = re.match(
        r"^(?:(?:on\s+)?\d{1,2}\s+[a-z]{3,9},?\s+(?:at\s+)?"
        r"\d{1,2}:\d{2}\s*(?:am|pm))(?:\s+|$)",
        request,
        re.IGNORECASE,
    )
    if absolute:
        due_text = absolute.group(0)
        title = request[absolute.end() :].strip() or "Reminder"
        return title, parse_due(due_text, now)

    for due_text_length in range(len(request.split()), 0, -1):
        words = request.split()
        due_text = " ".join(words[:due_text_length])
        try:
            due_at = parse_due(due_text, now)
        except ValueError:
            continue
        title = " ".join(words[due_text_length:]).strip() or "Reminder"
        return title, due_at
    raise ValueError(
        "Start with a time such as 'in 20m', 'in 2h', or '2 Jun, 1:23 PM'."
    )


def notify_due(
    store: ReminderStore,
    send_notification: Callable[[Reminder], None],
    now: datetime | None = None,
) -> list[tuple[Reminder, str]]:
    failures: list[tuple[Reminder, str]] = []
    for reminder in store.due_unnotified(now or datetime.now().astimezone()):
        try:
            send_notification(reminder)
        except (OSError, subprocess.SubprocessError) as error:
            store.set_unnotified(reminder.id)
            failures.append((reminder, str(error)))
        else:
            store.set_notified(reminder.id)
    return failures


def send_desktop_notification(reminder: Reminder) -> None:
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        raise FileNotFoundError("notify-send is not installed")
    subprocess.run(
        [
            notify_send,
            f"--expire-time={NOTIFICATION_TIMEOUT_MS}",
            "--app-name=RemindMe",
            "Reminder",
            reminder.title,
        ],
        check=True,
        timeout=15,
    )


def run_daemon(store: ReminderStore) -> None:
    lock_path = store.path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another RemindMe notification service is running.") from error

        print(f"RemindMe notification service running; database: {store.path}", flush=True)
        while True:
            failures = notify_due(store, send_desktop_notification)
            for reminder, error in failures:
                print(
                    f"Could not notify for reminder {reminder.id}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(POLL_SECONDS)


def edit_buffer(
    text: str, cursor: int, key: int | str
) -> tuple[str, int, bool]:
    """Apply one editing key; return the buffer, cursor, and submit state."""
    characters = list(text)
    if key in ("\n", "\r", curses.KEY_ENTER, 10, 13):
        return text, cursor, True
    if key in ("\x1b",):
        return "", 0, True
    if key in (curses.KEY_LEFT, "\x02"):
        cursor = max(0, cursor - 1)
    elif key in (curses.KEY_RIGHT, "\x06"):
        cursor = min(len(characters), cursor + 1)
    elif key in (curses.KEY_HOME, "\x01"):
        cursor = 0
    elif key in (curses.KEY_END, "\x05"):
        cursor = len(characters)
    elif key in (curses.KEY_BACKSPACE, 8, 127, "\x7f"):
        if cursor:
            del characters[cursor - 1]
            cursor -= 1
    elif key == curses.KEY_DC:
        if cursor < len(characters):
            del characters[cursor]
    elif key == "\x15":
        del characters[:cursor]
        cursor = 0
    elif isinstance(key, str) and key.isprintable():
        characters.insert(cursor, key)
        cursor += 1
    return "".join(characters), cursor, False


def prompt(stdscr: curses.window, label: str, initial: str = "") -> str:
    height, width = stdscr.getmaxyx()
    row = max(0, height - 2)
    prompt_text = f"{label}: "
    start_x = min(len(prompt_text), max(0, width - 1))
    available = max(1, width - start_x - 1)
    text = initial
    cursor = len(text)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        while True:
            stdscr.move(row, 0)
            stdscr.clrtoeol()
            stdscr.addnstr(row, 0, prompt_text, max(0, width - 1))
            offset = max(0, min(cursor - available + 1, len(text) - available))
            stdscr.addnstr(
                row,
                start_x,
                text[offset : offset + available],
                available,
            )
            stdscr.move(row, start_x + cursor - offset)
            stdscr.refresh()
            key = stdscr.get_wch()
            if key in ("\x11", 17):
                raise QuitRequested
            text, cursor, submitted = edit_buffer(text, cursor, key)
            if submitted:
                return text.strip() or initial
    finally:
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def command_palette(
    stdscr: curses.window,
    theme: str,
    options: tuple[PaletteOption, ...] = PALETTE_OPTIONS,
    title: str = "RemindMe Commands",
    on_selection: Callable[[str | None], None] | None = None,
) -> str | None:
    height, width = stdscr.getmaxyx()
    if height < 5 or width < 12:
        return None

    panel_width = width
    panel_height = min(13, height - 1)
    panel_top = height - panel_height
    panel = curses.newwin(
        panel_height,
        panel_width,
        panel_top,
        0,
    )
    panel.keypad(True)
    query = ""
    cursor = 0
    selected = next(
        (
            index
            for index, option in enumerate(options)
            if options != PALETTE_OPTIONS and option.command == theme
        ),
        0,
    )
    last_preview: object = object()

    def dismiss_palette() -> None:
        panel.erase()
        panel.refresh()
        stdscr.touchwin()
        stdscr.refresh()

    def preview_selection(selection: str | None) -> None:
        nonlocal last_preview
        if on_selection is not None and selection != last_preview:
            on_selection(selection)
            last_preview = selection

    while True:
        matches = filter_palette_options(query)
        if options != PALETTE_OPTIONS:
            terms = query.strip().lower().lstrip("/").split()
            matches = [
                option
                for option in options
                if all(
                    term in f"{option.command} {option.description}".lower()
                    for term in terms
                )
            ]
        selected = min(selected, max(0, len(matches) - 1))
        preview_selection(matches[selected].command if matches else None)

        panel.erase()
        panel.addnstr(
            0,
            0,
            "─" * max(0, panel_width - 1),
            max(0, panel_width - 1),
            color_attr("accent"),
        )
        panel.addnstr(
            1,
            1,
            f"{title}  ·  Theme: {ACTIVE_THEME}",
            max(0, panel_width - 2),
            color_attr("accent", curses.A_BOLD),
        )

        list_top = 2
        prompt_row = panel_height - 3
        visible_count = max(0, prompt_row - list_top - 1)
        first = max(0, selected - visible_count + 1)
        for index, option in enumerate(matches[first : first + visible_count], first):
            row = list_top + index - first
            attr = (
                color_attr("selected", curses.A_BOLD)
                if index == selected
                else color_attr("muted")
            )
            panel.addnstr(
                row,
                2,
                f"/{option.command:<18} {option.description}",
                max(0, panel_width - 4),
                attr,
            )
        if not matches and panel_height > list_top:
            panel.addnstr(
                list_top,
                2,
                "No matching commands.",
                max(0, panel_width - 4),
                color_attr("muted", curses.A_DIM),
            )
        panel.addnstr(
            prompt_row - 1,
            0,
            "─" * max(0, panel_width - 1),
            max(0, panel_width - 1),
            color_attr("muted"),
        )
        prompt_prefix = "› /"
        input_width = max(1, panel_width - len(prompt_prefix) - 2)
        input_offset = max(0, min(cursor - input_width + 1, len(query) - input_width))
        panel.addnstr(
            prompt_row,
            1,
            prompt_prefix + query[input_offset : input_offset + input_width],
            max(0, panel_width - 2),
            color_attr("accent", curses.A_BOLD),
        )
        draw_palette_key_hints(panel, panel_height - 2, panel_width)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        panel.move(prompt_row, min(panel_width - 1, 1 + len(prompt_prefix) + cursor - input_offset))
        panel.refresh()

        key = panel.get_wch()
        if key in ("\x11", 17):
            preview_selection(None)
            dismiss_palette()
            raise QuitRequested
        if key in ("\x1b", 27):
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            preview_selection(None)
            dismiss_palette()
            return None
        if key in (curses.KEY_UP, "\x10"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "\x0e"):
            selected = min(max(0, len(matches) - 1), selected + 1)
            continue
        if key in ("\t", 9):
            if matches:
                query = matches[selected].command
                cursor = len(query)
                selected = 0
            continue
        if key in ("\n", "\r", curses.KEY_ENTER):
            if not matches:
                continue
            normalized_query = query.strip().lower().lstrip("/")
            exact = next(
                (option for option in matches if option.command.lower() == normalized_query),
                None,
            )
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            command = (exact or matches[selected]).command
            preview_selection(command)
            dismiss_palette()
            return command
        query, cursor, _submitted = edit_buffer(query, cursor, key)


def color_attr(role: str, extra: int = 0) -> int:
    if ACTIVE_THEME == DEFAULT_THEME:
        attr = curses.A_REVERSE if role == "selected" else curses.A_NORMAL
    else:
        attr = (
            curses.color_pair(COLOR_PAIRS[role])
            if curses.has_colors()
            else (curses.A_REVERSE if role == "selected" else curses.A_NORMAL)
        )
    return attr | extra


def draw_palette_key_hints(
    window: curses.window,
    row: int,
    width: int,
) -> None:
    hints = (
        ("↑/↓", "select"),
        ("enter", "accept"),
        ("tab", "complete"),
        ("esc", "cancel"),
    )
    column = 1
    for index, (key, action) in enumerate(hints):
        separator = " · " if index else ""
        hint = f"{key}: {action}"
        if column + len(separator) + len(hint) > width - 1:
            break
        if separator:
            window.addnstr(
                row,
                column,
                separator,
                len(separator),
                color_attr("muted", curses.A_DIM),
            )
            column += len(separator)
        window.addnstr(
            row,
            column,
            key,
            len(key),
            color_attr("selected", curses.A_BOLD),
        )
        column += len(key)
        suffix = f": {action}"
        window.addnstr(
            row,
            column,
            suffix,
            len(suffix),
            color_attr("muted", curses.A_DIM),
        )
        column += len(suffix)


def draw_key_hints(
    stdscr: curses.window,
    row: int,
    width: int,
    theme: str,
) -> None:
    hints = (
        ("a", "add"),
        ("e", "edit"),
        ("space", "done"),
        ("s", "snooze"),
        ("d", "delete"),
        ("/", "commands"),
        ("ctrl+q", "quit"),
    )
    column = 0
    for index, (key, action) in enumerate(hints):
        chip = f" {key} "
        suffix = f" {action}"
        plain = chip + suffix + ("  " if index < len(hints) - 1 else "")
        if column + len(plain) > width:
            break
        stdscr.addstr(
            row,
            column,
            chip,
            color_attr("selected", curses.A_BOLD),
        )
        column += len(chip)
        stdscr.addstr(row, column, suffix, curses.A_NORMAL)
        column += len(suffix)
        if index < len(hints) - 1:
            stdscr.addstr(row, column, "  ", curses.A_NORMAL)
            column += 2


def draw(
    stdscr: curses.window,
    reminders: list[Reminder],
    selected: int,
    message: str,
    database_path: Path,
    banner: tuple[str, ...],
    theme: str,
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height > 0:
        stdscr.addnstr(
            0,
            1,
            f"Data: {database_path}  Theme: {theme}",
            max(0, width - 2),
            color_attr("muted"),
        )
    if height > 1:
        stdscr.addnstr(1, 0, "─" * max(0, width - 1), max(0, width - 1), color_attr("muted"))
    for row, line in enumerate(banner, start=2):
        if row < height:
            stdscr.addnstr(row, 0, line, max(0, width - 1), color_attr("header", curses.A_BOLD))
    tagline_row = 2 + len(banner)
    if height > tagline_row:
        stdscr.addnstr(
            tagline_row,
            1,
            "@github.com/abdurrahman4127",
            max(0, width - 2),
            color_attr("muted", curses.A_DIM),
        )
    separator_row = tagline_row + 1
    if height > separator_row:
        stdscr.addnstr(
            separator_row,
            0,
            "─" * max(0, width - 1),
            max(0, width - 1),
            color_attr("muted"),
        )
    pending = [reminder for reminder in reminders if not reminder.done]
    completed = [reminder for reminder in reminders if reminder.done]
    lines: list[tuple[Reminder, str]] = [
        (reminder, "✓" if reminder.notified else " ") for reminder in pending
    ]
    lines.extend((reminder, "x") for reminder in completed)
    list_top = separator_row + 1
    if not lines:
        if height > list_top:
            stdscr.addnstr(
                list_top,
                2,
                "No reminders yet. Press a or / to add one.",
                max(0, width - 3),
                color_attr("muted"),
            )

    list_bottom = max(list_top, height - 4)
    visible_rows = max(0, list_bottom - list_top)
    start = max(0, selected - visible_rows + 1)
    for row_index, (reminder, marker) in enumerate(lines[start : start + visible_rows], start):
        local_due = reminder.due_at.astimezone()
        due_text = local_due.strftime("%a %d %b %I:%M %p")
        status = "DONE" if reminder.done else (
            "DUE" if local_due <= datetime.now().astimezone() else due_text
        )
        text = f" {marker} {status:>16}  {reminder.title}"
        if row_index == selected:
            attr = color_attr("selected", curses.A_BOLD)
        elif reminder.done:
            attr = color_attr("done", curses.A_DIM)
        elif status == "DUE":
            attr = color_attr("due", curses.A_BOLD)
        else:
            attr = curses.A_NORMAL
        if reminder.done:
            attr |= curses.A_DIM
        stdscr.addnstr(list_top + row_index - start, 0, text, max(0, width - 1), attr)

    if height > 4:
        stdscr.addnstr(
            height - 4,
            0,
            "─" * max(0, width - 1),
            max(0, width - 1),
            color_attr("muted"),
        )
    if height > 3:
        stdscr.addnstr(
            max(0, height - 3),
            0,
            message,
            max(0, width - 1),
            color_attr("accent", curses.A_BOLD),
        )
    if height > 2:
        stdscr.addnstr(
            max(0, height - 2),
            0,
            "CLI: remindme 1d 3h [title]  |  remindme 2 Jun, 1:23 PM [title]",
            max(0, width - 1),
            color_attr("muted"),
        )
    if height > 1:
        draw_key_hints(stdscr, height - 1, width - 1, theme)
    stdscr.refresh()


def run_tui(stdscr: curses.window, store: ReminderStore) -> None:
    banner = load_banner()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    theme = load_theme()
    apply_theme(theme)
    stdscr.keypad(True)
    selected = 0
    message = ""

    while True:
        reminders = store.list_reminders()
        selected = min(selected, max(0, len(reminders) - 1))
        draw(stdscr, reminders, selected, message, store.path, banner, theme)
        message = ""
        key = stdscr.getch()

        if key == ord("/"):
            command = command_palette(stdscr, theme)
            if command is None:
                continue
            if command == "theme":
                themes = theme_palette_options()
                original_theme = theme
                command = command_palette(
                    stdscr,
                    theme,
                    themes,
                    "Choose Theme",
                    lambda preview: apply_theme(preview or original_theme),
                )
                if command:
                    save_theme(command)
                    theme = command
                    apply_theme(theme)
                    message = f"Theme changed to {theme}."
                continue
            if command == "help":
                message = "Commands: /add /edit /done /snooze /delete /theme /quit"
                continue
            key = {
                "add": ord("a"),
                "edit": ord("e"),
                "done": ord(" "),
                "snooze": ord("s"),
                "delete": ord("d"),
                "quit": 17,
            }.get(command, key)

        if key == 17:
            return
        if key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = min(max(0, len(reminders) - 1), selected + 1)
        elif key == ord("a"):
            title = prompt(stdscr, "Reminder")
            if not title:
                message = "Cancelled: title is required."
                continue
            due_text = prompt(
                stdscr,
                "When (tomorrow 9:00 AM, 2 Jun 1:23 PM, or 1d 3h)",
            )
            try:
                store.add(title, parse_due(due_text))
                message = "Reminder added."
            except ValueError as error:
                message = str(error)
        elif key == ord("e") and reminders:
            reminder = reminders[selected]
            title = prompt(stdscr, "Title", reminder.title)
            due_text = prompt(
                stdscr,
                "When (tomorrow 9:00 AM, 2 Jun 1:23 PM, or 1d 3h)",
                reminder.due_at.astimezone().strftime("%Y-%m-%d %I:%M %p"),
            )
            try:
                store.update(reminder.id, title, parse_due(due_text))
                message = "Reminder updated."
            except ValueError as error:
                message = str(error)
        elif key == ord(" ") and reminders:
            reminder = reminders[selected]
            store.set_done(reminder.id, not reminder.done)
            message = "Reminder marked done." if not reminder.done else "Reminder reopened."
        elif key == ord("s") and reminders and not reminders[selected].done:
            store.snooze(reminders[selected].id)
            message = f"Snoozed for {SNOOZE_MINUTES} minutes."
        elif key == ord("d") and reminders:
            answer = prompt(stdscr, "Delete reminder? Type y to confirm")
            if answer.lower() == "y":
                store.delete(reminders[selected].id)
                message = "Reminder deleted."
            else:
                message = "Delete cancelled."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A local-first Linux terminal reminder app.")
    parser.add_argument(
        "--database",
        type=Path,
        default=default_database_path(),
        help="database path (default: XDG data directory)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="run the background desktop-notification service",
    )
    parser.add_argument(
        "reminder",
        nargs="*",
        metavar="TIME/TITLE",
        help="create a reminder instead of opening the TUI (for example: 1d 3h Call Sam)",
    )
    args = parser.parse_args(argv)
    store = ReminderStore(args.database)
    try:
        if args.daemon:
            run_daemon(store)
        elif args.reminder:
            title, due_at = parse_reminder_request(args.reminder)
            store.add(title, due_at)
            print(f"Added: {title} — {due_at.astimezone().strftime('%a %d %b, %I:%M %p')}")
        else:
            curses.wrapper(run_tui, store)
    except QuitRequested:
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"remindme: {error}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
