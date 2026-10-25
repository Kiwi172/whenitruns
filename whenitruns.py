#!/usr/bin/env python3
"""whenitruns — explain every scheduled job on your machine, in English.

Reads crontabs, /etc/cron.d, cron.hourly/daily/weekly/monthly dirs,
systemd .timer units, and JSON job files with cron expressions.
Prints human-readable schedules, the next runs, and collision warnings.
Stdlib only. No network, no daemon, nothing installed.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

__version__ = "0.1.0"

MONTH_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
               "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

MACROS = {
    "@reboot": None,
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

FIELD_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
FIELD_NAMES = ["minute", "hour", "day-of-month", "month", "day-of-week"]


class CronError(ValueError):
    pass


def _token_to_int(tok, lo, hi, names):
    t = tok.strip().lower()
    if names and t[:3] in names:
        v = names[t[:3]]
    else:
        if not re.fullmatch(r"\d+", t):
            raise CronError("bad value %r" % tok)
        v = int(t)
    if v == 7 and hi == 7:
        v = 0  # Sunday: 7 == 0
    if not (lo <= v <= hi):
        raise CronError("value %d out of range %d-%d" % (v, lo, hi))
    return v


def parse_field(spec, idx):
    """Parse one cron field into (set_of_ints, is_restricted)."""
    lo, hi = FIELD_BOUNDS[idx]
    names = MONTH_NAMES if idx == 3 else (DOW_NAMES if idx == 4 else None)
    spec = spec.strip()
    if not spec:
        raise CronError("empty field")
    out = set()
    restricted = spec != "*"
    for part in spec.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            if not re.fullmatch(r"\d+", step_s):
                raise CronError("bad step %r" % part)
            step = int(step_s)
            if step < 1:
                raise CronError("step must be >= 1")
        else:
            base = part
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a_s, _, b_s = base.partition("-")
            a, b = _token_to_int(a_s, lo, hi, names), _token_to_int(b_s, lo, hi, names)
            if a > b:
                raise CronError("range %d-%d is inverted" % (a, b))
        else:
            a = b = _token_to_int(base, lo, hi, names)
            if step > 1:
                b = hi  # "5/10" means 5-max step 10
        out.update(range(a, b + 1, step))
    if not out:
        raise CronError("field matches nothing")
    if idx == 4:
        out = {0 if v == 7 else v for v in out}
    return out, restricted


def parse_expr(expr):
    """Parse a 5-field cron expression or @macro. Returns dict or {'reboot': True}."""
    expr = expr.strip()
    if expr in MACROS:
        if MACROS[expr] is None:
            return {"reboot": True, "expr": expr}
        expr = MACROS[expr]
    fields = expr.split()
    if len(fields) != 5:
        raise CronError("expected 5 fields, got %d" % len(fields))
    parsed = []
    for i, f in enumerate(fields):
        parsed.append(parse_field(f, i))
    return {
        "expr": expr,
        "minute": parsed[0][0], "hour": parsed[1][0],
        "dom": parsed[2][0], "month": parsed[3][0], "dow": parsed[4][0],
        "dom_r": parsed[2][1], "dow_r": parsed[4][1],
        "reboot": False,
    }


def matches(sched, when):
    if sched.get("reboot"):
        return False
    if when.month not in sched["month"]:
        return False
    dom_ok = when.day in sched["dom"]
    dow = (when.weekday() + 1) % 7  # python Mon=0 -> cron Sun=0
    dow_ok = dow in sched["dow"]
    # classic cron: if BOTH dom and dow are restricted, it's an OR
    if sched["dom_r"] and sched["dow_r"]:
        if not (dom_ok or dow_ok):
            return False
    else:
        if not (dom_ok and dow_ok):
            return False
    return when.hour in sched["hour"] and when.minute in sched["minute"]


def next_runs(sched, count=5, now=None, horizon_days=366):
    if sched.get("reboot"):
        return []
    now = now or dt.datetime.now()
    start = (now + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    end = start + dt.timedelta(days=horizon_days)
    runs = []
    day = start.date()
    while len(runs) < count and day <= end.date():
        if day.month in sched["month"]:
            dom_ok = day.day in sched["dom"]
            dow = (day.weekday() + 1) % 7
            dow_ok = dow in sched["dow"]
            if sched["dom_r"] and sched["dow_r"]:
                day_ok = dom_ok or dow_ok
            else:
                day_ok = dom_ok and dow_ok
            if day_ok:
                for h in sorted(sched["hour"]):
                    for m in sorted(sched["minute"]):
                        t = dt.datetime(day.year, day.month, day.day, h, m)
                        if start <= t < end:
                            runs.append(t)
                            if len(runs) >= count:
                                break
                    if len(runs) >= count:
                        break
        day += dt.timedelta(days=1)
    return runs


def _fmt_set(vals, names=None):
    vals = sorted(vals)
    inv = {}
    for k, v in (names or {}).items():
        inv.setdefault(v, k)
    def show(v):
        return inv.get(v, str(v)) if names else str(v)
    # collapse to ranges/steps when contiguous
    if len(vals) > 8:
        step = vals[1] - vals[0] if len(vals) > 1 else 1
        if step > 1 and all(b - a == step for a, b in zip(vals, vals[1:])):
            return "%s every %d" % (show(vals[0]), step)
        if vals == list(range(vals[0], vals[-1] + 1)):
            return "%s-%s" % (show(vals[0]), show(vals[-1]))
    return ",".join(show(v) for v in vals)


def human(sched):
    if sched.get("reboot"):
        return "once at system boot"
    m, h = sched["minute"], sched["hour"]
    dom, mon, dow = sched["dom"], sched["month"], sched["dow"]
    full = lambda s, lo, hi: s == set(range(lo, hi + 1))
    parts = []
    if full(m, 0, 59) and full(h, 0, 23):
        when = "every minute"
    elif len(m) == 1 and full(h, 0, 23):
        when = "at minute :%02d of every hour" % next(iter(m))
    elif len(m) == 1 and len(h) == 1:
        when = "at %02d:%02d" % (next(iter(h)), next(iter(m)))
    elif len(m) == 1 and full(h, 0, 23):
        when = "every hour at :%02d" % next(iter(m))
    elif len(m) == 1:
        when = "at :%02d during hours[%s]" % (next(iter(m)), _fmt_set(h))
    elif full(m, 0, 59) and len(h) == 1:
        when = "every minute during hour %d" % next(iter(h))
    else:
        when = "minute[%s] hour[%s]" % (_fmt_set(m), _fmt_set(h))
    if not full(mon, 1, 12):
        parts.append("in months " + _fmt_set(mon, MONTH_NAMES))
    if full(dom, 1, 31) and full(dow, 0, 6):
        pass
    elif sched["dom_r"] and sched["dow_r"]:
        parts.append("on day-of-month %s OR weekday %s" %
                     (_fmt_set(dom), _fmt_set(dow, DOW_NAMES)))
    elif sched["dow_r"]:
        parts.append("on weekday " + _fmt_set(dow, DOW_NAMES))
    else:
        parts.append("on day-of-month " + _fmt_set(dom))
    return when + (" " + " ".join(parts) if parts else "")


# ---------------------------------------------------------------- parsing

def parse_crontab_text(text, has_user):
    jobs = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^\w+\s*=", line):  # env assignment
            continue
        if line.startswith("@"):
            head, _, cmd = line.partition(" ")
            if head not in MACROS:
                jobs.append({"error": "unknown macro %s" % head, "line": lineno})
                continue
            sched = parse_expr(head)
            jobs.append({"schedule": sched, "command": cmd.strip(), "line": lineno})
            continue
        fields = line.split(None, 6 if has_user else 5)
        if len(fields) < (6 if has_user else 5):
            jobs.append({"error": "too few fields", "line": lineno})
            continue
        expr = " ".join(fields[:5])
        rest = fields[5:]
        user = rest[0] if has_user else None
        cmd = " ".join(rest[1:]) if has_user else " ".join(rest)
        try:
            sched = parse_expr(expr)
        except CronError as e:
            jobs.append({"error": str(e), "line": lineno, "raw": expr})
            continue
        jobs.append({"schedule": sched, "user": user, "command": cmd, "line": lineno})
    return jobs


UNIT_TIMES = ("OnActiveSec", "OnBootSec", "OnStartupSec", "OnUnitActiveSec",
              "OnUnitInactiveSec")
DUR_RE = re.compile(r"^(?:(\d+)s|(\d+)min|(\d+)h|(\d+)d|\s*)$")


def parse_duration(tok):
    tok = tok.strip().lower()
    total = dt.timedelta()
    found = False
    for num, unit in re.findall(r"(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hours?|d|days?|w|weeks?)", tok):
        n = int(num)
        if unit.startswith(("s",)):
            total += dt.timedelta(seconds=n)
        elif unit.startswith(("m",)):
            total += dt.timedelta(minutes=n)
        elif unit.startswith("h"):
            total += dt.timedelta(hours=n)
        elif unit.startswith(("d", "w")):
            days = n * (7 if unit.startswith("w") else 1)
            total += dt.timedelta(days=days)
        found = True
    return total if found else None


CAL_KEYWORDS = {"minutely": "* * * * *", "hourly": "0 * * * *",
                "daily": "0 0 * * *", "midnight": "0 0 * * *",
                "weekly": "0 0 * * 1", "monthly": "0 0 1 * *",
                "yearly": "0 0 1 1 *", "annually": "0 0 1 1 *"}
# systemd OnCalendar: [DayOfWeek] Y-M-D HH:MM[:SS]
CAL_RE = re.compile(
    r"^(?:(?P<dow>sun|mon|tue|wed|thu|fri|sat)[a-z]*\s+)?"
    r"(?:\d{4}|\*)-(?:\d{1,2}|\*)-(?:\d{1,2}|\*)\s+"
    r"(?P<h>\d{1,2}|\*):(?P<m>\d{1,2}(?:/\d+)?|\*(?:/\d+)?)"
    r"(?::(?P<s>\d{1,2}|\*))?\s*$",
    re.IGNORECASE)


def parse_oncalendar(spec):
    """Best-effort: map common OnCalendar specs to a cron schedule."""
    s = spec.strip().lower()
    if s in CAL_KEYWORDS:
        return parse_expr(CAL_KEYWORDS[s])
    m = CAL_RE.match(s)
    if not m:
        return None

    def conv(tok):
        if tok == "*":
            return "*"
        if "/" in tok:
            base, step = tok.split("/")
            if base in ("*", ""):
                base = "0"
            return "%d/%s" % (int(base), int(step))
        return str(int(tok))

    try:
        return parse_expr("%s %s * * %s" % (
            conv(m.group("m")), conv(m.group("h")),
            m.group("dow")[:3] if m.group("dow") else "*"))
    except CronError:
        return None


def parse_timer_file(path):
    jobs = []
    section = None
    oncal, rels = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                section = line
                continue
            if "=" not in line or line.startswith((";", "#")):
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key == "OnCalendar":
                oncal.append(val)
            elif key in UNIT_TIMES:
                d = parse_duration(val)
                if d is not None:
                    rels.append((key, d))
    for spec in oncal:
        sched = parse_oncalendar(spec)
        jobs.append({"schedule": sched, "command": "OnCalendar=%s" % spec,
                     "unparsed": sched is None})
    for key, d in rels:
        jobs.append({"schedule": None, "command": "%s=%s" % (key, d),
                     "relative": str(d)})
    return jobs


def scan_paths(paths, _seen=None):
    """Yield (source, jobs) for files and dirs. Symlinked duplicates skipped."""
    if _seen is None:
        _seen = set()
    found = []
    for p in paths:
        rp = os.path.realpath(p)
        if rp in _seen:
            continue
        _seen.add(rp)
        if os.path.isdir(p):
            base = os.path.basename(p)
            if base == "cron.d":
                for c in sorted(glob.glob(os.path.join(p, "*"))):
                    if os.path.isfile(c):
                        with open(c, "r", errors="replace") as f:
                            found.append((c, parse_crontab_text(f.read(), True)))
                continue
            if base.startswith("cron."):
                period = os.path.basename(p)[5:]
                macro = {"hourly": "@hourly", "daily": "@daily",
                         "weekly": "@weekly", "monthly": "@monthly"}.get(period)
                scripts = sorted(glob.glob(os.path.join(p, "*")))
                jobs = [{"schedule": parse_expr(macro) if macro else None,
                         "command": "run-parts script: " + os.path.basename(s),
                         "line": 0} for s in scripts if os.path.isfile(s)]
                found.append((p, jobs))
                continue
            candidates = (glob.glob(os.path.join(p, "*.timer")) +
                          glob.glob(os.path.join(p, "*.cron")) +
                          glob.glob(os.path.join(p, "*")))
            for c in sorted(set(candidates)):
                found.extend(scan_paths([c], _seen))
            continue
        if not os.path.isfile(p):
            continue
        if p.endswith(".timer"):
            found.append((p, parse_timer_file(p)))
        elif p.endswith(".json"):
            try:
                data = json.load(open(p))
            except (OSError, ValueError):
                continue
            jobs = []
            for job in (data.get("jobs") or []):
                expr = ((job.get("schedule") or {}).get("expr")
                        or job.get("schedule_display") or job.get("cron"))
                if not expr:
                    continue
                try:
                    jobs.append({"schedule": parse_expr(expr),
                                 "command": job.get("name") or job.get("id") or "?",
                                 "line": 0})
                except CronError as e:
                    jobs.append({"error": str(e), "raw": expr, "line": 0,
                                 "command": job.get("name", "?")})
            if jobs:
                found.append((p, jobs))
        else:
            has_user = "/cron.d" in p or p.endswith("/crontab")
            with open(p, "r", errors="replace") as f:
                jobs = parse_crontab_text(f.read(), has_user)
            if jobs:
                found.append((p, jobs))
    return found


DEFAULT_PATHS = ["/etc/crontab", "/etc/cron.d", "/etc/cron.hourly",
                 "/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly",
                 "/usr/lib/systemd/system", "/etc/systemd/system"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="whenitruns", description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files/dirs to scan (default: system locations)")
    ap.add_argument("--expr", help="explain a single cron expression and exit")
    ap.add_argument("--next", type=int, default=3, metavar="N", help="show next N runs (default 3)")
    ap.add_argument("--days", type=int, default=7, metavar="N", help="collision window in days (default 7)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = ap.parse_args(argv)

    if args.expr:
        try:
            sched = parse_expr(args.expr)
        except CronError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        print(human(sched))
        for t in next_runs(sched, args.next):
            print("  next: %s" % t.strftime("%Y-%m-%d %H:%M %a"))
        return 0

    paths = args.paths or DEFAULT_PATHS
    sources = scan_paths(paths)
    now = dt.datetime.now()
    horizon = now + dt.timedelta(days=args.days)

    all_runs = {}  # minute -> [job labels]
    out = []
    for src, jobs in sources:
        entries = []
        for job in jobs:
            if "error" in job:
                entries.append({"error": job["error"], "line": job["line"],
                                "raw": job.get("raw", "")})
                continue
            sched = job["schedule"]
            if sched is None:
                entries.append({"source": src, "command": job["command"],
                                "human": "relative timer (see unit)", "next": []})
                continue
            if sched.get("unparsed") or job.get("unparsed"):
                entries.append({"source": src, "command": job["command"],
                                "human": "OnCalendar spec not parsed", "next": []})
                continue
            runs = next_runs(sched, args.next, now=now, horizon_days=366)
            label = "%s:%s" % (os.path.basename(src), job.get("command", "")[:40])
            for t in next_runs(sched, 500, now=now, horizon_days=args.days):
                if t <= horizon:
                    all_runs.setdefault(t, set()).add(label)
            entries.append({"source": src, "line": job.get("line", 0),
                            "command": job["command"], "user": job.get("user"),
                            "expr": sched["expr"], "human": human(sched),
                            "next": [t.strftime("%Y-%m-%d %H:%M %a") for t in runs]})
        out.append({"source": src, "jobs": entries})

    collisions = {t: labels for t, labels in all_runs.items() if len(labels) > 1}

    if args.json:
        print(json.dumps({"scanned": [s for s, _ in sources], "sources": out,
                          "collisions": {t.strftime("%Y-%m-%d %H:%M"): sorted(v)
                                         for t, v in sorted(collisions.items())}},
                         indent=2))
        return 0

    total = sum(len(j) for _, j in sources)
    print("whenitruns: %d jobs from %d sources" % (total, len(sources)))
    for block in out:
        src, entries = block["source"], block["jobs"]
        if not entries:
            continue
        print("\n== %s" % src)
        for e in entries:
            if "error" in e:
                print("  [line %s] PARSE ERROR: %s  (%s)" % (e["line"], e["error"], e["raw"]))
                continue
            who = " (user %s)" % e["user"] if e.get("user") else ""
            print("  %s%s" % (e["command"] or e["expr"], who))
            print("    schedule: %s" % e.get("expr", "(no cron expr)"))
            print("    meaning : %s" % e["human"])
            for t in e["next"]:
                print("    next    : %s" % t)
    if collisions:
        print("\n== collisions (jobs firing at the same minute within %d days)" % args.days)
        for t in sorted(collisions)[:10]:
            print("  %s: %s" % (t.strftime("%Y-%m-%d %H:%M %a"), ", ".join(sorted(collisions[t]))))
    else:
        print("\nNo collisions in the next %d days." % args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
