#!/usr/bin/env python3
"""
form_analysis.py — per-km running-form breakdown + wrist-HR cadence-lock check

Why this exists (2026-11-17): every "post run analyze" the coach agent wrote a
fresh throw-away script (per-km GCT/VR, HR-vs-cadence lock, belt-speed pace).
Ad-hoc code each time = a new chance to mis-compute. These are now fixed,
tested functions; post_session_analyzer.py prints them and exposes them in
--json so the agent narrates numbers instead of computing them.

Pure functions only (no Garmin calls) — input is the activity-details dict
already fetched by post_session_analyzer.fetch_activity().
"""

from __future__ import annotations

import math

# Thresholds (first-third vs last-third of the run)
GCT_FATIGUE_MS   = 10     # GCT rises ≥10 ms → form fatigue
VR_FATIGUE_PCT   = 0.5    # vertical ratio rises ≥0.5 %-points
CAD_FATIGUE_SPM  = 4      # cadence drops ≥4 spm
LOCK_TOL_BPM     = 2      # |HR − cadence| ≤ 2 counts as a "locked" sample
LOCK_SUSPECT     = 0.40   # ≥40 % locked samples → wrist HR likely tracking cadence
LOCK_UNCLEAR     = 0.20   # 20–40 % → unclear
MIN_SPEED_MPS    = 1.5    # below this = standing/walking pause, excluded


def _val(row, idx):
    if idx is None or row is None or idx >= len(row):
        return None
    v = row[idx]
    return v if isinstance(v, (int, float)) else None


def extract_samples(data: dict) -> list[dict]:
    """Activity-details metrics → list of per-sample dicts (moving samples only)."""
    desc = {d["key"]: d["metricsIndex"] for d in data.get("metricDescriptors", [])}
    ix = {k: desc.get(k) for k in (
        "directHeartRate", "sumDistance", "directSpeed", "directRunCadence",
        "directDoubleCadence", "directGroundContactTime", "directVerticalRatio",
        "directStrideLength", "sumElapsedDuration", "sumDuration")}
    out = []
    for m in data.get("activityDetailMetrics", []):
        row = m.get("metrics", [])
        spd = _val(row, ix["directSpeed"])
        if not spd or spd < MIN_SPEED_MPS:
            continue
        cad = _val(row, ix["directDoubleCadence"])
        if not cad:
            cad = _val(row, ix["directRunCadence"])
            if cad and cad < 120:          # single-leg stream → full spm
                cad *= 2
        dist = _val(row, ix["sumDistance"])
        t = _val(row, ix["sumElapsedDuration"]) or _val(row, ix["sumDuration"])
        stride = _val(row, ix["directStrideLength"])
        out.append({
            "dist_km": dist / 1000 if dist is not None else None,
            "t": t,
            "speed": spd,
            "hr": _val(row, ix["directHeartRate"]),
            "cad": cad,
            "gct": _val(row, ix["directGroundContactTime"]),
            "vr": _val(row, ix["directVerticalRatio"]),
            "stride_m": stride / 100 if stride else None,   # Garmin streams cm
        })
    return out


def _mean(xs):
    xs = [x for x in xs if x]
    return sum(xs) / len(xs) if xs else None


def _pace_str(sec_per_km):
    if not sec_per_km or sec_per_km <= 0 or sec_per_km > 1200:
        return "—"
    s = int(round(sec_per_km))
    return f"{s // 60}:{s % 60:02d}"


def per_km(samples: list[dict], belt_kmh: float | None = None) -> list[dict]:
    """Group samples by whole km of watch distance.

    belt_kmh: treadmill belt speed (km/h). Watch TM pace is accelerometer-based
    and unreliable — when given, every row's pace = belt pace and the row is
    tagged so nobody mistakes the watch pace for real.
    """
    rows: dict[int, list] = {}
    for s in samples:
        if s["dist_km"] is None:
            continue
        rows.setdefault(int(s["dist_km"]), []).append(s)
    out = []
    belt_pace = 3600 / belt_kmh if belt_kmh else None
    for k in sorted(rows):
        g = rows[k]
        spd = _mean(x["speed"] for x in g)
        pace = belt_pace or (1000 / spd if spd else None)
        out.append({
            "km": k + 1,
            "partial": k == max(rows) and (g[-1]["dist_km"] - k) < 0.9,
            "pace_sec": round(pace) if pace else None,
            "pace": _pace_str(pace),
            "pace_source": "belt" if belt_pace else "watch",
            "hr": round(_mean(x["hr"] for x in g)) if _mean(x["hr"] for x in g) else None,
            "cad": round(_mean(x["cad"] for x in g)) if _mean(x["cad"] for x in g) else None,
            "gct": round(_mean(x["gct"] for x in g)) if _mean(x["gct"] for x in g) else None,
            "vr": round(_mean(x["vr"] for x in g), 1) if _mean(x["vr"] for x in g) else None,
            "stride_m": round(_mean(x["stride_m"] for x in g), 2) if _mean(x["stride_m"] for x in g) else None,
        })
    return out


def drift(rows: list[dict]) -> dict | None:
    """First third vs last third of full km rows. Needs ≥3 full km."""
    full = [r for r in rows if not r["partial"]]
    if len(full) < 3:
        return None
    n = max(1, len(full) // 3)
    a, b = full[:n], full[-n:]

    def d(key):
        x, y = _mean(r[key] for r in a), _mean(r[key] for r in b)
        return round(y - x, 1) if x is not None and y is not None else None

    out = {"gct_ms": d("gct"), "vr_pct": d("vr"), "cad_spm": d("cad"),
           "hr_bpm": d("hr"), "pace_sec": d("pace_sec"), "segments_km": n}
    flags = []
    if out["gct_ms"] is not None and out["gct_ms"] >= GCT_FATIGUE_MS:
        flags.append(f"GCT +{out['gct_ms']} ms")
    if out["vr_pct"] is not None and out["vr_pct"] >= VR_FATIGUE_PCT:
        flags.append(f"VR +{out['vr_pct']}")
    if out["cad_spm"] is not None and out["cad_spm"] <= -CAD_FATIGUE_SPM:
        flags.append(f"cadence {out['cad_spm']} spm")
    out["fatigue_flags"] = flags
    out["verdict"] = "form fatigue" if flags else "form held"
    return out


def cadence_lock(samples: list[dict]) -> dict | None:
    """Is the wrist optical HR just echoing cadence? (classic artefact)

    Returns share of samples with |HR − cadence| ≤ LOCK_TOL_BPM, Pearson r
    between HR and cadence, and a verdict. Only meaningful when HR and cadence
    ranges overlap (HR ~150–190 vs cadence ~160–190 spm).
    """
    pairs = [(s["hr"], s["cad"]) for s in samples if s["hr"] and s["cad"]]
    if len(pairs) < 30:
        return None
    locked = sum(1 for h, c in pairs if abs(h - c) <= LOCK_TOL_BPM) / len(pairs)
    hs, cs = [p[0] for p in pairs], [p[1] for p in pairs]
    mh, mc = sum(hs) / len(hs), sum(cs) / len(cs)
    sh = math.sqrt(sum((h - mh) ** 2 for h in hs))
    sc = math.sqrt(sum((c - mc) ** 2 for c in cs))
    r = (sum((h - mh) * (c - mc) for h, c in pairs) / (sh * sc)) if sh and sc else 0.0
    if locked >= LOCK_SUSPECT:
        verdict = "suspected"
    elif locked >= LOCK_UNCLEAR:
        verdict = "unclear"
    else:
        verdict = "not detected"
    return {"locked_share": round(locked, 2), "r": round(r, 2),
            "n": len(pairs), "verdict": verdict}


def analyze_form(data: dict, belt_kmh: float | None = None) -> dict:
    samples = extract_samples(data)
    rows = per_km(samples, belt_kmh)
    return {"per_km": rows, "drift": drift(rows), "cadence_lock": cadence_lock(samples),
            "belt_kmh": belt_kmh}


_LOCK_TH = {"suspected": "⚠️ สงสัย HR ล็อกกับ cadence — อย่าเชื่อ HR ช่วงนี้",
            "unclear": "🟡 ไม่ชัด — ระวังการตีความ HR",
            "not detected": "✅ ไม่พบ"}


def print_form(f: dict) -> None:
    rows = f["per_km"]
    if not rows:
        return
    print("\n🦶 FORM PER KM" + (f"  (pace = belt {f['belt_kmh']} km/h — ไม่ใช่ pace นาฬิกา)"
                                if f.get("belt_kmh") else ""))
    print(f"   {'km':>4} {'pace':>6} {'HR':>4} {'cad':>4} {'GCT':>4} {'VR':>5} {'stride':>6}")
    for r in rows:
        tag = "*" if r["partial"] else " "
        print(f"   {r['km']:>3}{tag} {r['pace']:>6} {r['hr'] or '—':>4} {r['cad'] or '—':>4} "
              f"{r['gct'] or '—':>4} {r['vr'] or '—':>5} {r['stride_m'] or '—':>6}")
    d = f.get("drift")
    if d:
        print(f"   ช่วงแรก→ช่วงท้าย ({d['segments_km']} กม.): GCT {d['gct_ms']:+} ms | "
              f"VR {d['vr_pct']:+} | cad {d['cad_spm']:+} | HR {d['hr_bpm']:+}"
              if None not in (d['gct_ms'], d['vr_pct'], d['cad_spm'], d['hr_bpm'])
              else f"   drift: {d}")
        print(f"   → {'⚠️ ฟอร์มล้า: ' + ', '.join(d['fatigue_flags']) if d['fatigue_flags'] else '✅ ฟอร์มคงที่'}")
    cl = f.get("cadence_lock")
    if cl:
        print(f"   ⌚ HR–cadence lock: {cl['locked_share']*100:.0f}% samples |HR−cad|≤{LOCK_TOL_BPM}, "
              f"r={cl['r']} → {_LOCK_TH[cl['verdict']]}")
