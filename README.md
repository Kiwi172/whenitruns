# whenitruns

[![ci](https://github.com/Kiwi172/whenitruns/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiwi172/whenitruns/actions/workflows/ci.yml)

**Know exactly when every scheduled job on your machine fires — in plain English, with next-run times and collision warnings. No account, no upload, no network.**

Cron schedules hide in five different places on a typical Linux box: your user crontab, `/etc/crontab`, `/etc/cron.d/*`, `cron.hourly/daily/weekly/monthly` run-parts directories, and systemd `.timer` units with their own `OnCalendar` mini-language. Each uses a slightly different syntax, `5-55/10 * * * *` is unreadable at 2am, and nobody can tell you whether your backup job and your log rotation fire at the same minute. `systemctl list-timers` only shows timers; `crontab -l` only shows one file. whenitruns reads them all, explains every schedule in English, computes the next runs, and flags jobs that collide.

```
  /etc/crontab ─┐
  /etc/cron.d/* ─┤
  cron.daily/* ──┼──► whenitruns ──► "at 03:30 on weekday sun"
  *.timer units ─┤                  next: 2026-10-25 03:30 Sun
  jobs.json ─────┘                  ⚠ collides with e2scrub.timer
```

## Install

```
python3 whenitruns.py            # or: pipx install whenitruns
```

Single file, Python 3.9+, zero dependencies. Nothing is installed, nothing runs as a daemon, no file is ever uploaded.

## Use

```
whenitruns                          # scan system locations (crontab, cron.d, timers)
whenitruns mycrontab cron.d jobs.json   # scan your own files
whenitruns --expr "5-55/10 * * * *"     # explain one expression
whenitruns --next 5 --days 14           # more next-runs, wider collision window
whenitruns --json                       # machine-readable output
```

Sample output:

```
== /etc/cron.d/e2scrub_all
  test -e /run/systemd/system || SERVICE_MODE=1 ... (user root)
    schedule: 30 3 * * 0
    meaning : at 03:30 on weekday sun
    next    : 2026-10-25 03:30 Sun

== collisions (jobs firing at the same minute within 7 days)
  2026-10-25 03:10 Sun: e2scrub_all:..., e2scrub_all.timer:OnCalendar=Sun *-*-* 03:10:00
```

## What it understands

- 5-field cron: `*/n`, ranges, lists, `jan`/`mon` names, `@daily`-style macros, the classic day-of-month OR day-of-week rule
- `/etc/crontab` and `/etc/cron.d` files with their user field
- `cron.hourly/daily/weekly/monthly` run-parts directories
- systemd `.timer` units: `OnCalendar` keywords and common `*-*-* HH:MM` patterns, plus relative `OnBootSec`/`OnUnitActiveSec` durations
- JSON files with `jobs[].schedule.expr` entries (e.g. Hermes cron job files)

## Tests

```
python3 tests/test_whenitruns.py    # standalone, no pytest needed
pytest tests/                       # or under pytest
```

## License

MIT
