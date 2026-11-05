# RemindMe

This is a simple pet project of mine to help me remember things and manage
reminders from the terminal. I'm putting it on GitHub so I don't forget about
it or lose it when I mess up my system.

RemindMe is a local-first Linux reminder app with a terminal UI, SQLite
storage, and optional desktop notifications.

## Preview

Screenshot of the running terminal UI:

![RemindMe reminder list](assets/figs/reminders.png)

### ASCII banner

The app loads this banner from [`assets/banner.txt`](assets/banner.txt), so
you can change the art without editing the Python code:

```text
██▀███     ▓█████     ███▄ ▄███▓    ██▓    ███▄    █    ▓█████▄           ███▄ ▄███▓▓█████
▓██ ▒ ██▒   ▓█   ▀    ▓██▒▀█▀ ██▒   ▓██▒    ██ ▀█   █    ▒██▀ ██▌         ▓██▒▀█▀ ██▒▓█   ▀
▓██ ░▄█ ▒   ▒███      ▓██    ▓██░   ▒██▒   ▓██  ▀█ ██▒   ░██   █▌         ▓██    ▓██░▒███
▒██▀▀█▄     ▒▓█  ▄    ▒██    ▒██    ░██░   ▓██▒  ▐▌██▒   ░▓█▄   ▌         ▒██    ▒██ ▒▓█  ▄
░██▓ ▒██▒   ░▒████▒   ▒██▒   ░██▒   ░██░   ▒██░   ▓██░   ░▒████▓          ▒██▒   ░██▒░▒████▒
░ ▒▓ ░▒▓░   ░░ ▒░ ░   ░ ▒░   ░  ░   ░▓     ░ ▒░   ▒ ▒     ▒▒▓  ▒          ░ ▒░   ░  ░░░ ▒░ ░
  ░▒ ░ ▒░    ░ ░  ░   ░  ░      ░    ▒ ░   ░ ░░   ░ ▒░    ░ ▒  ▒          ░  ░      ░ ░ ░  ░
  ░░   ░       ░      ░      ░       ▒ ░      ░   ░ ░     ░ ░  ░          ░      ░      ░
   ░           ░  ░          ░       ░              ░       ░                    ░      ░  ░
                                                          ░
```

## Directory contents

| Path | Purpose |
| --- | --- |
| `remindme.py` | Application: TUI, command-line parser, SQLite storage, and notification loop. |
| `remindme` | Small executable entry point used by the shell command. |
| `remindme.service` | systemd user unit for background desktop notifications. |
| `test_remindme.py` | Automated tests for date parsing and notification behavior. |
| `assets/banner.txt` | Editable UTF-8 ASCII/Unicode art displayed in the TUI header. |
| `assets/figs/reminders.png` | Screenshot example of the RemindMe terminal UI. |
| `.gitignore` | Keeps Python bytecode and local database artifacts out of Git. |

## Requirements

- Linux and Python 3.10 or later (uses only the Python standard library).
- A terminal with curses support for the TUI.
- systemd user services and `notify-send` for background desktop alerts.
- A graphical-session notification daemon. The notification daemon decides
  whether it honors the requested 30-second display timeout.

No Python dependencies are required. The app does not install packages.

## Install the shell command

From a terminal, create `~/.local/bin` if needed and link the tracked entry
point there:

```sh
mkdir -p "$HOME/.local/bin"
ln -sfn "$HOME/scripts/RemindMe/remindme" "$HOME/.local/bin/remindme"
```

Ensure `~/.local/bin` is on `PATH`. For Bash, add this to `~/.bashrc` if it is
not already present, then open a new terminal:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

The `remind` command is retained as a compatibility alias. To recreate it:

```sh
ln -sfn "$HOME/scripts/RemindMe/remindme" "$HOME/.local/bin/remind"
```

## Use

Run `remindme` with no arguments to open the TUI in the current terminal.

Run it with a due-time phrase to create a reminder directly without opening
the TUI:

```sh
remindme in 20m "Email the journal editor"
remindme in 2h "Send an update to the research group"
remindme 1d 3h "Review the literature review"
remindme 3d 47m "Submit the revised manuscript"
remindme 8pm "Email the coauthor"
remindme 8 pm "Review the paper"
remindme today 8pm "Send the project update"
remindme today 8 pm "Review the paper"
remindme 8:07pm "Check the submission portal"
remindme tomorrow 12am "Submit the conference abstract"
remindme tomorrow 12pm "Meet with the research group"
remindme tomorrow 20:30 "Prepare conference slides"
remindme 30/07 1am "Submit the grant application"
remindme 30/7 1am "Proofread the revised manuscript"
remindme 25 nov, 1am "Prepare the LinkedIn post"
remindme 16 dec, 11am "Prepare the LinkedIn post"
remindme 2/6, 1:23 PM "Email the journal editor"
remindme on 2/6 at 1:23 PM "Meet with the research group"
remindme tomorrow 9:00 AM "Finish the peer review"
remindme today 1:23 PM "Send the revision update"
```

Relative time accepts combined days (`d`), hours (`h`), and minutes (`m`), for
example `1d 3h` means 27 hours from now and `3d 47m` means 3 days and 47
minutes from now. You can optionally prefix these with `in`. For clock times,
use either 24-hour time or 12-hour time with `AM`/`PM`. A date is optional for
12-hour times: `8pm`, `8 pm`, and `today 8pm` all mean today at 8:00 PM;
`8:07pm` means 8:07 PM. If minutes are omitted, they default to `00`.
`12am` is midnight and `12pm` is noon. Calendar dates use day/month without a
year; both `30/07` and `30/7` are valid. English month names and abbreviations
are also accepted, as in `25 nov, 1am` or `25 November, 1am`. A date can be
followed by a time such as `30/7 1am` or `2/6, 1:23 PM`; it resolves to the
next future occurrence in the local timezone. Reminder times in the TUI use
12-hour format with an `AM` or `PM` suffix. Existing 24-hour clock inputs such
as `20:00` remain supported, including with a day/month date. Calendar dates
do not accept a year. If no title follows the time, the title is `Reminder`.

A bare hour such as `8` is interpreted using the current local time. For
example, before 8:00 AM it means 8:00 AM today. After 8:00 AM, RemindMe asks
you to confirm 8:00 PM today or enter a different time. If both occurrences
have passed, it asks whether you meant 8:00 AM or 8:00 PM tomorrow. If an
explicitly specified today time has already passed (for example, `today 8pm`
after 8:00 PM), RemindMe asks whether to keep it today or move it to tomorrow.

Put reminder titles in quotes to preserve spaces, for example,
`remindme 8pm "Review the paper"` or
`remindme 30/07 1am "Submit the grant application"`.

TUI keys:

| Key | Action |
| --- | --- |
| `↑`/`↓` or `j`/`k` | Select a reminder |
| `a` | Add a reminder interactively |
| `e` | Edit the selected reminder |
| `space` | Mark done or reopen |
| `s` | Snooze for 10 minutes |
| `d` | Delete after confirmation |
| `ctrl+q` | Quit |
| `/` | Open the bottom-docked searchable command palette |

In the bottom command area, type to filter the suggestions shown above it,
use Up/Down to select, Tab to complete the highlighted suggestion, Enter to
run a command, and Escape to close it. Escape cancels the active prompt or
closes the palette; press Ctrl+Q to quit the app. Select `theme` to open the
theme picker; theme names are kept out of the main command suggestions and
shown only in that picker. The palette commands are `add`, `edit`, `done`,
`snooze`, `delete`, `theme`, `help`, and `quit`.

The theme picker starts on the currently active theme. Moving the selection
previews each theme immediately. Press Enter to save the preview as the new
theme, or Escape to restore the previous theme. Theme selection is saved
between runs.

When entering or editing text, Left/Right move the insertion cursor, Home/End
move to the beginning/end, Backspace deletes before the cursor, and Delete
deletes after it. Enter submits the field; Escape cancels the current input.

The TUI header includes command examples and the key guide at the bottom of
the screen.

The bottom key guide puts a shaded background behind each key and the single
space on either side of it. Action labels are plain text, with no colon
separator; for example, the guide appears as ` a ` add  ` e ` edit. The
palette’s own footer similarly highlights only its key labels.

Use `remindme --help` for command-line options. Pass `--database PATH` before
or after the reminder phrase to use a different SQLite database.

## Customize

The app is intentionally small. Personalize it without changing Python code:

1. Open `assets/banner.txt` in a text editor.
2. Select all of its contents and replace them with your own banner, one
   visual row per text line. Keep leading spaces if your art uses indentation,
   and save the file as UTF-8.
3. Run `remindme` again to see the new banner. The app reads the file each
   time it starts the TUI.

The tagline below the art is separate. To change it, edit the
`@github.com/abdurrahman4127` string in `draw()` in `remindme.py`.

Other useful settings in `remindme.py`:

- `POLL_SECONDS`: how often the background service checks for due reminders.
- `SNOOZE_MINUTES`: the TUI snooze duration.
- `NOTIFICATION_TIMEOUT_MS`: requested desktop-alert duration. The desktop
  notification daemon may override it.
- Use `/theme` to browse 24 themes, including Slate Blue, GitHub, Catppuccin,
  Dracula, Nord, Gruvbox, Tokyo Night, Solarized, and more. The original plain
  terminal appearance is preserved by the `default` theme.
  Your choice is saved in
  `$XDG_CONFIG_HOME/remindme/config.json` (or `~/.config/remindme/config.json`).
  To add or recolor themes, edit the hex color tuples in `THEMES` in
  `remindme.py`.


- Available themes: `default`, `slate-blue`, `github-dark`, `github-light`,
  `catppuccin-mocha`, `catppuccin-latte`, `dracula`, `nord`, `gruvbox-dark`,
  `gruvbox-light`, `tokyo-night`, `one-dark`, `monokai`, `solarized-dark`,
  `solarized-light`, `rose-pine`, `kanagawa`, `everforest`, `matrix`,
  `synthwave`, `ocean`, `forest`, `ember`, and `high-contrast`.

Set `XDG_DATA_HOME` to move the default database location, or pass
`--database PATH` when running the command.

If you install the project somewhere other than `~/scripts/RemindMe`, update
`ExecStart` in `remindme.service`, then relink/restart the user service:

```sh
systemctl --user disable --now remindme.service
systemctl --user link "$HOME/scripts/RemindMe/remindme.service"
systemctl --user daemon-reload
systemctl --user enable --now remindme.service
```

## Enable desktop notifications

The systemd user service checks every 10 seconds. When the computer is asleep
or powered off, it cannot display an alert; once the user service runs again,
it catches up and sends reminders that are still pending. The service runs
after the user session starts. Desktop notifications generally require logging
into the graphical session.

Enable the service for this checkout:

```sh
systemctl --user link "$HOME/scripts/RemindMe/remindme.service"
systemctl --user daemon-reload
systemctl --user enable --now remindme.service
```

Check service health and recent logs:

```sh
systemctl --user status remindme.service --no-pager
journalctl --user -u remindme.service -n 50 --no-pager
```

The notification requests a 30-second timeout via `notify-send
--expire-time=30000`. Some desktop environments ignore applications' timeout
requests and use their own notification settings.

To disable the service without deleting its unit file:

```sh
systemctl --user disable --now remindme.service
```

## Data and backup

By default, reminders are stored outside this Git repository at
`$XDG_DATA_HOME/remindme/reminders.sqlite3`, or
`~/.local/share/remindme/reminders.sqlite3` if `XDG_DATA_HOME` is unset.
The CLI and notification service use the same database. `--database PATH`
overrides the location for a CLI invocation; the background service continues
using its default database.

Back up the SQLite database to preserve reminders. For example, stop the
service, copy the database, and start the service again:

```sh
systemctl --user stop remindme.service
cp "$HOME/.local/share/remindme/reminders.sqlite3" "$HOME/reminders.sqlite3.backup"
systemctl --user start remindme.service
```

If `XDG_DATA_HOME` is set, substitute that directory for `~/.local/share`.

## Development and tests

Run the test suite from this directory:

```sh
python3 -m unittest -v
```

The tests use temporary databases and do not send real desktop notifications.
