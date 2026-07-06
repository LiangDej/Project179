# Training Plan Methodology

How `training_planner.py` builds a periodized plan from a race date and an athlete's own
training history — no hardcoded numbers, no placeholder athlete required.

This describes the **approach**. For an actual athlete's live plan (VDOT, race targets,
current CTL, etc.), see `athlete.json` / `races.json` (gitignored, personal) — never commit
real numbers to this file.

---

## 1. Inputs (single sources of truth)

| Input | Source | Notes |
|---|---|---|
| VDOT, LTHR, HR zones, paces | `athlete.json` | race/TT-confirmed only |
| Race date, distance, goal | `races.json` | multiple races supported, one "active" |
| Recent training load | `running_activities_all.json` | synced from Garmin, not hand-edited |
| Training days/week, rest days, strength day | `athlete.json` | how many days you actually run |

Nothing about volume, phase length, or session count is hardcoded per-athlete. Every number
below is derived from these four inputs at runtime.

---

## 2. Dynamic volume targets

### 2.1 Current load
`_current_weekly_km()` — average of the last 4 **complete** weeks from
`running_activities_all.json`. This is "where the athlete actually is," not an assumption.

### 2.2 Historical peak
`_historical_peak_km()` — the single highest completed week in the last 52 weeks. This is
the athlete's own proof of capacity: a runner who has hit 60km/week before gets a different
peak target than one whose best week ever was 35km.

### 2.3 Target peak
```
baseline  = max(historical_peak, current_weekly, floor)
peak_km   = round_to_5( max(baseline × 1.13, current_weekly + 5) )
```
The `1.13` stretch factor is a "realistic overreach" — enough to force adaptation without
asking for a peak the athlete has never demonstrated they can approach. It never returns a
target *lower* than current load + one progression step, so an athlete mid-buildup is never
handed a plan that looks like a step backward.

### 2.4 Phase peaks
Base / Quality / Race-Specific peaks are proportional slices of `peak_km`, not independent
numbers:

```
base_max          = peak_km × 0.886
quality_max       = peak_km × 0.929
race_specific_max = peak_km × 1.0
```

These ratios preserve a progressive-overload shape (each phase incrementally harder than the
last) regardless of what `peak_km` actually is for a given athlete.

### 2.5 Taper
Taper volume is a **percentage of peak_km**, not a fixed absolute number:

| Weeks to race | % of peak |
|---|---|
| 4 (taper wk1) | 75% |
| 3 (taper wk2) | 55% |
| 2 (taper wk3) | 35% |
| 1 (race week) | 15% |
| 0 (race day)  | 0% |

This is a generic taper curve (Mujika & Padilla-style progressive volume reduction), applied
to whatever peak the athlete actually reached — a 45km/week runner and a 70km/week runner
taper by the same *shape*, not the same absolute km.

---

## 3. Phase boundaries (time, not volume)

Phase is determined purely by weeks-to-race:

```
weeks_to_race < 5   → taper
weeks_to_race < 8   → race_specific
weeks_to_race < 14  → quality
else                → base
```

Deload happens every 4th week at 80% of that week's volume, independent of phase.

---

## 4. Session distribution — driven by training days/week

Instead of a fixed 7-day template, the weekly schedule is built from three athlete.json
fields:

- `training_days_per_week` — how many days/week the athlete **runs** (strength and full-rest
  days don't count)
- `rest_days` — days with zero training (no run, no lift)
- `strength_day` — a non-running training day, if the athlete lifts

Given those, `_build_session_schedule()` assigns:

1. **Long run** → Sunday if available, else the last active day of the week
2. **Quality session(s)** → 1 or 2 days depending on the phase's `quality_max`, spaced apart
   from the long run
3. **Strides day** → the easy day immediately before the long run
4. **Remaining active days** → easy runs

A 4-day/week runner gets 1 quality day and fewer easy days; a 6-day/week runner keeps 2
quality days with easy runs filling the gaps. Nobody gets a plan assuming a training
frequency they don't have.

---

## 5. What stays fixed (deliberately, not laziness)

- **Deload factor (80%)** and **stretch factor (1.13)** are sports-science constants, not
  athlete data — they don't belong in `athlete.json` any more than "10% rule" would.
- **Phase HR zones and pace prescriptions** (`PHASE_PRESCRIPTIONS` in `config.py`) describe
  *what a T-pace interval looks like*, which is a training-methodology decision, not a
  per-athlete volume number — that's why it stays in code, not data.

---

## 6. Extending this

To adapt the planner for a different sport or goal structure, the surface area is:

- `PHASE_PEAK_RATIO` — change the relative shape of base/quality/race-specific
- `TAPER_PCT` — change the taper curve
- `STRETCH_FACTOR` — more or less aggressive peak-week targeting
- `_build_session_schedule()` — different day-assignment logic (e.g. 2 long runs/week for
  ultra training)

None of these require touching an athlete's actual data files.
