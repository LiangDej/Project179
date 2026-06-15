#!/usr/bin/env python3
"""
skill_sync.py — Auto-update garmin-coach-analyzer.md จาก session log

อ่าน QualitySessionLog/sessions.json แล้ว:
  1. Append row ใหม่เข้า Raw Session Data table ใน skill file
  2. อัพเดต timestamp "อัพเดตล่าสุด" ใน Quality Run Analysis section

Usage:
    python3 skill_sync.py               # sync จริง
    python3 skill_sync.py --dry-run     # preview ว่าจะเพิ่มอะไรบ้าง
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path(__file__).parent.parent
LOG_PATH   = BASE_DIR / "QualitySessionLog" / "sessions.json"
SKILL_PATH = BASE_DIR.parent / "skills" / "garmin-coach-analyzer.md"

# Table header ที่ต้องหาใน skill file
TABLE_HEADER = "| วันที่ | ประเภท | เพซ(active) | AvgHR | MaxHR | Cadence | Power | GCT | Decoupling | Notes |"
TABLE_SEP    = "|---|---|---|---|---|---|---|---|---|---|"


def load_log():
    if not LOG_PATH.exists():
        print("⚠️  ไม่พบ sessions.json — รัน post_session_analyzer.py --update-log ก่อน")
        sys.exit(1)
    with open(LOG_PATH) as f:
        return json.load(f)


def load_skill():
    with open(SKILL_PATH, encoding="utf-8") as f:
        return f.read()


def save_skill(content):
    with open(SKILL_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def existing_dates_in_table(skill_text):
    """Extract all dates already present in the Raw Session Data table."""
    dates = set()
    in_table = False
    for line in skill_text.splitlines():
        if TABLE_HEADER in line:
            in_table = True
            continue
        if in_table:
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})", line)
            if m:
                dates.add(m.group(1))
            elif line.strip().startswith("#") or (line.strip() == "" and not dates):
                pass
            elif line.strip() == "" or line.startswith(">"):
                in_table = False
    return dates


def session_to_table_row(s):
    """Convert a session log entry to a markdown table row."""
    session_type_abbrev = {
        "Easy Run":       "E",
        "Marathon Pace":  "M",
        "Threshold (T)":  "T",
        "Interval (I)":   "I",
        "Repetition (R)": "R",
    }
    stype = session_type_abbrev.get(s.get("session_type", ""), s.get("session_type", "?"))
    treadmill = " 🏃" if s.get("is_treadmill") else ""
    dc  = f"{s['decoupling']}%" if s.get("decoupling") is not None else "N/A"
    cad = f"{s['cadence']}spm"  if s.get("cadence") else "N/A"
    gct = f"{s['gct']}ms"       if s.get("gct") else "N/A"
    pwr = f"{s['power']}W"      if s.get("power") else "N/A"

    grade_tag = f" {s['grade']}" if s.get("grade") and s["grade"] != "—" else ""
    notes = f"{grade_tag}{treadmill}".strip() or "—"

    return (
        f"| {s['date']} | {stype} | {s.get('pace','N/A')} | "
        f"{s.get('avg_hr','N/A')} | {s.get('max_hr','N/A')} | "
        f"{cad} | {pwr} | {gct} | {dc} | {notes} |"
    )


def insert_rows_into_table(skill_text, new_rows):
    """Insert new rows just after the table separator row."""
    lines   = skill_text.splitlines(keepends=True)
    sep_idx = None
    header_found = False

    for i, line in enumerate(lines):
        if TABLE_HEADER in line:
            header_found = True
        if header_found and TABLE_SEP in line:
            sep_idx = i
            break

    if sep_idx is None:
        print("❌ ไม่พบตาราง Raw Session Data ใน skill file")
        sys.exit(1)

    # Sort new rows by date descending so newest appears at top
    new_rows_sorted = sorted(new_rows, key=lambda r: r.split("|")[1].strip(), reverse=True)
    insert_text = "".join(r + "\n" for r in new_rows_sorted)

    lines.insert(sep_idx + 1, insert_text)
    return "".join(lines)


def update_sync_timestamp(skill_text):
    """Update or insert a sync timestamp line in the Quality Run Analysis section."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ts_marker = "> **Last sync:**"
    ts_line   = f"{ts_marker} {ts} (skill_sync.py)\n"

    if ts_marker in skill_text:
        skill_text = re.sub(
            rf"{re.escape(ts_marker)}.*\n", ts_line, skill_text
        )
    else:
        # Insert after the Quality Run Analysis section header
        skill_text = skill_text.replace(
            "> ข้อมูลนี้วิเคราะห์จาก Time-series",
            f"{ts_line}> ข้อมูลนี้วิเคราะห์จาก Time-series",
            1,
        )
    return skill_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="แสดงผลเท่านั้น ไม่เขียนไฟล์จริง")
    args = parser.parse_args()

    log        = load_log()
    sessions   = log.get("sessions", [])
    skill_text = load_skill()

    existing = existing_dates_in_table(skill_text)
    new_entries = [s for s in sessions if s["date"] not in existing]

    if not new_entries:
        print("✅ Skill file เป็นปัจจุบันแล้ว — ไม่มี session ใหม่ที่ต้องเพิ่ม")
        return

    new_rows = [session_to_table_row(s) for s in new_entries]

    print(f"📋 พบ {len(new_entries)} session ใหม่ที่จะเพิ่ม:")
    for row in new_rows:
        print(f"   {row}")

    if args.dry_run:
        print("\n🔎 Dry-run mode — ไม่ได้เขียนไฟล์จริง")
        return

    updated = insert_rows_into_table(skill_text, new_rows)
    updated = update_sync_timestamp(updated)
    save_skill(updated)

    print(f"\n✅ อัพเดต {SKILL_PATH.name} เรียบร้อย — เพิ่ม {len(new_entries)} row")


if __name__ == "__main__":
    main()
