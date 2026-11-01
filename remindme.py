#!/usr/bin/env python3
"""A small Linux terminal reminder app with optional desktop notifications."""

from __future__ import annotations

import argparse
import curses
import fcntl
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


@dataclass(frozen=True)
class Reminder:
    id: int
    title: str
    due_at: datetime
    done: bool
    notified: bool


def default_database_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "remindme" / "reminders.sqlite3"


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
    """Parse a local date/time or a short relative time like 'in 20m'."""
    now = now or datetime.now().astimezone()
    value = value.strip().lower()
    relative = re.fullmatch(r"in\s+(\d+)\s*([mhd])", value)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        duration = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }[unit]
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
        for date_format in ("%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p"):
            try:
                return datetime.strptime(value.upper(), date_format).replace(
                    tzinfo=now.tzinfo
                )
            except ValueError:
                pass
        raise ValueError(
            "Use YYYY-MM-DD HH:MM, today HH:MM, tomorrow HH:MM, or in 20m/2h/1d."
        )

    for time_format in ("%H:%M", "%I:%M%p"):
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
    relative = re.match(r"^(in\s+\d+\s*[mhd])(?:\s+|$)", request, re.IGNORECASE)
    if relative:
        due_text = relative.group(1)
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
    if key in ("\n", "\r", curses.KEY_ENTER):
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
            text, cursor, submitted = edit_buffer(text, cursor, stdscr.get_wch())
            if submitted:
                return text.strip() or initial
    finally:
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def draw(
    stdscr: curses.window,
    reminders: list[Reminder],
    selected: int,
    message: str,
    database_path: Path,
    banner: tuple[str, ...],
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height > 0:
        stdscr.addnstr(0, 1, f"Data: {database_path}", max(0, width - 2))
    if height > 1:
        stdscr.addnstr(1, 0, "─" * max(0, width - 1), max(0, width - 1))
    for row, line in enumerate(banner, start=2):
        if row < height:
            stdscr.addnstr(row, 0, line, max(0, width - 1), curses.A_BOLD)
    tagline_row = 2 + len(banner)
    if height > tagline_row:
        stdscr.addnstr(
            tagline_row,
            1,
            "@github.com/abdurrahman4127",
            max(0, width - 2),
            curses.A_DIM,
        )
    separator_row = tagline_row + 1
    if height > separator_row:
        stdscr.addnstr(
            separator_row,
            0,
            "─" * max(0, width - 1),
            max(0, width - 1),
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
            stdscr.addnstr(list_top, 2, "No reminders yet. Press a to add one.", max(0, width - 3))

    list_bottom = max(list_top, height - 4)
    visible_rows = max(0, list_bottom - list_top)
    start = max(0, selected - visible_rows + 1)
    for row_index, (reminder, marker) in enumerate(lines[start : start + visible_rows], start):
        local_due = reminder.due_at.astimezone()
        due_text = local_due.strftime("%a %d %b %H:%M")
        status = "DONE" if reminder.done else (
            "DUE" if local_due <= datetime.now().astimezone() else due_text
        )
        text = f" {marker} {status:>16}  {reminder.title}"
        attr = curses.A_REVERSE if row_index == selected else curses.A_NORMAL
        if reminder.done:
            attr |= curses.A_DIM
        stdscr.addnstr(list_top + row_index - start, 0, text, max(0, width - 1), attr)

    if height > 4:
        stdscr.addnstr(height - 4, 0, "─" * max(0, width - 1), max(0, width - 1))
    if height > 3:
        stdscr.addnstr(max(0, height - 3), 0, message, max(0, width - 1), curses.A_BOLD)
    if height > 2:
        stdscr.addnstr(
            max(0, height - 2),
            0,
            "CLI: remindme in 20m [title]  |  remindme 2 Jun, 1:23 PM [title]",
            max(0, width - 1),
        )
    if height > 1:
        stdscr.addnstr(
            height - 1,
            0,
            "↑/↓ move  a add  e edit  Space done  s snooze  d delete  q quit",
            max(0, width - 1),
        )
    stdscr.refresh()


def run_tui(stdscr: curses.window, store: ReminderStore) -> None:
    banner = load_banner()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    selected = 0
    message = ""

    while True:
        reminders = store.list_reminders()
        selected = min(selected, max(0, len(reminders) - 1))
        draw(stdscr, reminders, selected, message, store.path, banner)
        message = ""
        key = stdscr.getch()

        if key in (ord("q"), 27):
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
            due_text = prompt(stdscr, "When (YYYY-MM-DD HH:MM, tomorrow 09:00, in 20m)")
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
                "When (YYYY-MM-DD HH:MM, tomorrow 09:00, in 20m)",
                reminder.due_at.astimezone().strftime("%Y-%m-%d %H:%M"),
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
        help="create a reminder instead of opening the TUI (for example: in 20m Call Sam)",
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
