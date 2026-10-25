#!/usr/bin/env python3
"""Tests for whenitruns. Run standalone: python3 tests/test_whenitruns.py
Or under pytest: pytest tests/"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import whenitruns as w  # noqa: E402

FIXED = dt.datetime(2026, 3, 2, 12, 0)  # a Monday


def test_parse_basic():
    s = w.parse_expr("*/10 9-17 * * 1-5")
    assert s["minute"] == {0, 10, 20, 30, 40, 50}
    assert s["hour"] == set(range(9, 18))
    assert s["dow"] == {1, 2, 3, 4, 5}
    assert s["dom_r"] is False and s["dow_r"] is True


def test_parse_names_and_sunday():
    s = w.parse_expr("0 0 * jan-mar mon,fri")
    assert s["month"] == {1, 2, 3}
    assert s["dow"] == {1, 5}
    s2 = w.parse_expr("0 0 * * 7")
    assert s2["dow"] == {0}


def test_parse_errors():
    for bad in ["* * * *", "60 * * * *", "* 24 * * *", "*/0 * * * *",
                "5-1 * * * *", "a * * * *"]:
        try:
            w.parse_expr(bad)
            assert False, "should have raised: %s" % bad
        except w.CronError:
            pass


def test_macros():
    assert w.parse_expr("@daily")["hour"] == {0}
    assert w.parse_expr("@reboot")["reboot"] is True


def test_next_runs():
    s = w.parse_expr("30 3 * * 0")  # Sundays 03:30
    runs = w.next_runs(s, 2, now=FIXED)
    assert len(runs) == 2
    assert runs[0] == dt.datetime(2026, 3, 8, 3, 30)  # next Sunday
    assert runs[0].weekday() == 6


def test_dom_dow_or_rule():
    # both restricted -> OR semantics (classic cron)
    s = w.parse_expr("0 0 13 * fri")
    runs = w.next_runs(s, 4, now=FIXED)
    days = [(r.month, r.day, r.weekday()) for r in runs]
    assert (3, 6, 4) in days           # Friday, dom not 13 -> matches via dow
    assert (3, 13, 4) in days          # Friday the 13th -> matches both
    assert (3, 3, 1) not in days       # a Tuesday, neither -> excluded


def test_human():
    assert "at 03:30" in w.human(w.parse_expr("30 3 * * *"))
    assert "every minute" in w.human(w.parse_expr("* * * * *"))
    assert "weekday" in w.human(w.parse_expr("0 9 * * 1-5"))
    assert "boot" in w.human(w.parse_expr("@reboot"))


def test_crontab_text():
    text = """
SHELL=/bin/sh
PATH=/x
# comment
*/5 * * * * root echo hi
bad line
0 0 * * * nobody /bin/true
"""
    jobs = w.parse_crontab_text(text, has_user=True)
    assert len(jobs) == 3
    assert jobs[0]["user"] == "root"
    assert jobs[1]["error"]
    assert jobs[2]["user"] == "nobody"


def test_cron_dirs():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        daily = os.path.join(d, "cron.daily")
        os.makedirs(daily)
        open(os.path.join(daily, "myscript"), "w").close()
        found = w.scan_paths([daily])
        assert len(found) == 1
        jobs = found[0][1]
        assert len(jobs) == 1
        assert jobs[0]["schedule"]["hour"] == {0}


def test_timer_file():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "foo.timer")
        open(p, "w").write(
            "[Unit]\nDescription=x\n[Timer]\nOnCalendar=daily\n"
            "OnUnitActiveSec=1h\n")
        jobs = w.parse_timer_file(p)
        assert any(j["schedule"] and j["schedule"]["hour"] == {0} for j in jobs)
        assert any(j.get("relative") for j in jobs)


def test_jobs_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "jobs.json")
        open(p, "w").write('{"jobs":[{"name":"x","schedule":{"expr":"0 6-23 * * *"}}]}')
        found = w.scan_paths([p])
        assert found and found[0][1][0]["schedule"]["hour"] == set(range(6, 24))


def test_collisions_via_main(tmpout=None):
    import tempfile
    import io
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        f1 = os.path.join(d, "a.cron")
        open(f1, "w").write("0 * * * * one\n")
        f2 = os.path.join(d, "b.cron")
        open(f2, "w").write("0 * * * * two\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = w.main([f1, f2, "--json"])
        import json
        data = json.loads(buf.getvalue())
        assert rc == 0
        assert data["collisions"], "expected same-minute jobs to collide"


def test_oncalendar():
    s = w.parse_oncalendar("Mon *-*-* 09:15")
    assert s and s["hour"] == {9} and s["minute"] == {15} and s["dow"] == {1}
    s2 = w.parse_oncalendar("*-*-* *:00/15:00")
    assert s2 and s2["minute"] == {0, 15, 30, 45}
    assert w.parse_oncalendar("weird spec") is None


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok %s" % fn.__name__)
    print("%d tests passed" % len(fns))


if __name__ == "__main__":
    _run_all()
