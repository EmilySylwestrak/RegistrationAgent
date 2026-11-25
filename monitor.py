"""
UO class seat monitor.

Loads the public UO class schedule page, finds the course(s) listed in
config.yaml, and sends an ntfy push when a lecture AND one of its linked
discussions both have open seats (i.e. a combination you can actually
register for). It never logs in and never registers -- you do that yourself.

Run locally:   python monitor.py --dry-run     (prints what it sees, no push)
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.yaml"
STATE_PATH = HERE / "state.json"

CRN_RE = re.compile(r"^\d{5}$")
COURSE_RE = re.compile(r"^([A-Z]{2,5})\s+(\d{3}[A-Z]?)\b")
INT_RE = re.compile(r"^-?\d+$")


# ---------------------------------------------------------------- parsing

def _cells(tr):
    """Direct <td>/<th> children of a row, as cleaned text."""
    return [" ".join(td.get_text(" ", strip=True).split())
            for td in tr.find_all(["td", "th"], recursive=False)]


def parse_schedule(html):
    """
    Returns {course_code: [section, ...]} in page order. Each section is
    {type, crn, avail, max, times:[(time, day)], instructor}.
    Continuation rows (a second meeting time with no CRN) are attached to
    the section above them.
    """
    soup = BeautifulSoup(html, "html.parser")
    courses = {}
    current_course = None
    last_section = None
    seen = set()

    for tr in soup.find_all("tr"):
        cells = _cells(tr)
        if not cells:
            continue

        # Course header, e.g. "STAT 243Z Elem Statistics I >3 >4"
        m = COURSE_RE.match(cells[0])
        if m and not any(CRN_RE.match(c) for c in cells):
            current_course = f"{m.group(1)} {m.group(2)}"
            courses.setdefault(current_course, [])
            last_section = None
            continue

        # Section row: [type, CRN, Avail, Max, Time, Day, Location, Instructor, ...]
        crn_idx = next((i for i, c in enumerate(cells) if CRN_RE.match(c)), None)
        if crn_idx is not None and current_course:
            rest = cells[crn_idx + 1:]
            if len(rest) >= 2 and INT_RE.match(rest[0]) and INT_RE.match(rest[1]):
                crn = cells[crn_idx]
                if crn in seen:          # nested tables can repeat a row
                    continue
                seen.add(crn)
                kind = cells[crn_idx - 1] if crn_idx > 0 else ""
                section = {
                    "type": "lecture" if kind.lower().startswith("lec") or not kind.startswith("+")
                            else kind.lstrip("+ ").strip().lower(),
                    "label": kind.lstrip("+ ").strip() or "Section",
                    "crn": crn,
                    "avail": int(rest[0]),
                    "max": int(rest[1]),
                    "times": [],
                    "instructor": rest[5] if len(rest) > 5 else "",
                }
                if len(rest) > 3:
                    section["times"].append((rest[2], rest[3]))
                courses[current_course].append(section)
                last_section = section
            continue

        # Continuation row: a second time/day with no CRN
        if last_section is not None:
            texts = [c for c in cells if c]
            tm = next((c for c in texts if re.match(r"^\d{4}-\d{4}$", c)), None)
            if tm:
                i = texts.index(tm)
                day = texts[i + 1] if i + 1 < len(texts) else ""
                last_section["times"].append((tm, day))

    return courses


def group_lectures(sections):
    """Pair each lecture with the '+ ...' sections that follow it."""
    groups = []
    for s in sections:
        if s["type"] == "lecture":
            groups.append({"lecture": s, "linked": []})
        elif groups:
            groups[-1]["linked"].append(s)
    return groups


def fmt_times(s):
    return ", ".join(f"{d} {t}" for t, d in s["times"]) or "time tba"


def find_openings(courses, course_codes):
    """Registrable combos: open lecture + open linked section (or a lecture with no links)."""
    openings = []
    for code in course_codes:
        for g in group_lectures(courses.get(code, [])):
            lec = g["lecture"]
            if lec["avail"] <= 0:
                continue
            if not g["linked"]:
                openings.append({"key": f"{lec['crn']}", "course": code,
                                 "lecture": lec, "linked": None})
                continue
            for d in g["linked"]:
                if d["avail"] > 0:
                    openings.append({"key": f"{lec['crn']}+{d['crn']}", "course": code,
                                     "lecture": lec, "linked": d})
    return openings


# ---------------------------------------------------------------- state & notify

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"open_keys": [], "consecutive_errors": 0, "error_alert_sent": False}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def notify(topic, title, message, click=None, priority="high", tags="mortar_board"):
    if not topic:
        print(f"[no ntfy topic] {title}\n{message}")
        return
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": tags}
    if click:
        headers["Click"] = click
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers=headers, timeout=20)


# ---------------------------------------------------------------- main

def main():
    dry = "--dry-run" in sys.argv
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    topic = "" if dry else os.environ.get("NTFY_TOPIC", "").strip()
    codes = [c.strip().upper() for c in cfg["courses"]]
    state = load_state()

    try:
        r = requests.get(cfg["schedule_url"], timeout=30,
                         headers={"User-Agent": "UO class seat monitor (personal, low-frequency)"})
        r.raise_for_status()
        courses = parse_schedule(r.text)
        missing = [c for c in codes if not courses.get(c)]
        if missing:
            raise RuntimeError(
                f"Couldn't find {', '.join(missing)} on the schedule page. "
                "The page layout or URL may have changed (the old class search retires Dec 16, 2026).")
    except Exception as e:
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        print(f"ERROR ({state['consecutive_errors']} in a row): {e}")
        if (cfg.get("notify_on_errors", True)
                and state["consecutive_errors"] >= cfg.get("error_threshold", 3)
                and not state.get("error_alert_sent")):
            notify(topic, "Class monitor is failing", str(e)[:500], priority="default", tags="warning")
            state["error_alert_sent"] = True
        save_state(state)
        return 0  # don't fail the workflow; state still gets committed

    if state.get("error_alert_sent"):
        notify(topic, "Class monitor is working again", "Checks are succeeding again.",
               priority="low", tags="white_check_mark")
    state["consecutive_errors"] = 0
    state["error_alert_sent"] = False

    for code in codes:
        for g in group_lectures(courses[code]):
            lec = g["lecture"]
            print(f"{code} Lecture {lec['crn']} {lec['avail']}/{lec['max']} {fmt_times(lec)} {lec['instructor']}")
            for d in g["linked"]:
                print(f"    + {d['label']:<6} {d['crn']} {d['avail']}/{d['max']} {fmt_times(d)}")

    openings = find_openings(courses, codes)
    previous = set(state.get("open_keys", []))
    new = [o for o in openings if o["key"] not in previous]

    if new:
        lines = []
        for o in new:
            lec = o["lecture"]
            line = (f"{o['course']}: Lecture {lec['crn']} ({lec['instructor']}, {fmt_times(lec)}) "
                    f"- {lec['avail']} seat(s)")
            if o["linked"]:
                d = o["linked"]
                line += f"\n  + {d['label']} {d['crn']} ({fmt_times(d)}) - {d['avail']} seat(s)"
            lines.append(line)
        msg = "\n".join(lines) + "\n\nRegister now with these CRNs."
        title = f"{new[0]['course']} seat open!" if len(new) == 1 else f"{len(new)} class openings!"
        print("\nALERT:\n" + msg)
        notify(topic, title, msg, click=cfg.get("click_url") or cfg["schedule_url"])
    else:
        print(f"\nNo new registrable openings ({len(openings)} already alerted / still open).")

    # Remember what's open now; when something closes it drops out,
    # so if it reopens later you get alerted again.
    state["open_keys"] = sorted(o["key"] for o in openings)
    state["last_success"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
