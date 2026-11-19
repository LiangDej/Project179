# PROJECT 179 — Agent Instructions (universal)

This file exists so AI coding agents that don't read `CLAUDE.md` by convention
(Cursor, Windsurf, GitHub Copilot, Gemini CLI/Agent, OpenAI Codex, etc.) still
see the same rules Claude Code/Cowork get. **`CLAUDE.md` is the canonical,
full version** — read it for complete context (project overview, test suite,
data files, bash commands). This file is a short pointer so the core rule
doesn't get missed.

> **Note for human contributors:** this file (like `CLAUDE.md`) is written for
> *AI coding assistants* reading it during a live coaching session — it tells
> the assistant to ask before touching tool code, so a chat request like "fix
> my pace" can't accidentally rewrite `daily_brief.py`. It is **not** a rule
> against human pull requests — open a PR normally if you're a person.

## ⚠️ FILE PROTECTION (applies to AI assistants)

**ALL Python files in `GarminRawData/tools/` are PROTECTED**, plus
`skills/garmin_coach_mcp/config.py` and `skills/garmin_coach_mcp/db_helper.py`.
Never modify, overwrite, or recreate these during a coaching session —
including any new tool added to that directory in the future.

- If asked to run, test, or analyze output from any of these tools → run
  them via bash/shell, do **not** edit the source.
- If you believe a protected file needs a fix → **STOP**, tell the user what
  change you'd like to make, and wait for explicit approval before touching
  it.

**This approval requirement does NOT apply to `athlete.json`, `races.json`,
`training_plan.json`, or any other data file** — those are meant to be edited freely, no
confirmation needed. That includes first-time onboarding (copying
`athlete.example.json`/`races.example.json`/`training_plan.example.json` to
`athlete.json`/`races.json`/`training_plan.json` and filling in the athlete's
real values): just write the files directly.

## Single sources of truth

| Value | Edit here (data, not code) |
|---|---|
| VDOT, LTHR, RHR, MHR, weight, HR-zone %, nutrition, form baselines, training venue (paces are derived from VDOT) | `GarminRawData/athlete.json` |
| Race targets (date/distance/goal tiers/stations/course/recovery) | `GarminRawData/races.json` |
| Season plan: phases (dates, weekly_km, long_rule) + workout specs | `GarminRawData/training_plan.json` |

No literal HR/pace/race/product/date values in tool code — `test_suite.py`
fails on them (AST scan).

Full details, bash commands, test suite instructions, and data-file
reference: see [`CLAUDE.md`](CLAUDE.md).
