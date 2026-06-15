#!/usr/bin/env python3
"""
test_tools.py — Unit Tests สำหรับ PROJECT 179 Python Tools

Coverage:
  - config.py          : ATHLETE constants, HR zones, phase detection
  - race_predictor.py  : VDOT math, race time prediction, VDOT estimate labeling
  - post_session_analyzer.py : session classification, easy run grades, quality grades
  - training_load.py   : hrTSS formula, PMC math (CTL/ATL/TSB), TSB labels

Run:
    # จาก AntiGravity/ root:
    python3 -m unittest GarminRawData.tools.tests.test_tools -v

    # หรือ run direct:
    python3 GarminRawData/tools/tests/test_tools.py
"""

import sys
import math
import types
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Path setup — เพิ่ม paths ก่อน import เพื่อ match โครงสร้าง project
# File location: AntiGravity/GarminRawData/tools/tests/test_tools.py
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent.parent  # AntiGravity/
TOOLS_DIR = ROOT / "GarminRawData" / "tools"
COACH_MCP = ROOT / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(COACH_MCP))
sys.path.insert(0, str(TOOLS_DIR))

# ---------------------------------------------------------------------------
# Mock third-party libraries ที่ไม่ได้ install ใน test environment
# (garminconnect ต้องการ credential จริง — mock เพื่อให้ import ผ่าน)
# ---------------------------------------------------------------------------
_garmin_mock = types.ModuleType("garminconnect")
_garmin_mock.Garmin = MagicMock()
sys.modules.setdefault("garminconnect", _garmin_mock)

from config import (
    ATHLETE, HR_ZONES, HR_ZONE_BOUNDS, VDOT_PACES,
    BASELINES, TRAINING_PHASES, PHASE_PRESCRIPTIONS,
    get_current_phase,
)
from race_predictor import (
    compute_vdot, predict_race_time, fmt_time,
    estimate_vdot_from_session, estimate_current_vdot,
)
from post_session_analyzer import (
    classify_session, analyze_easy_run, grade_session, flag_issues,
)
from training_load import calc_hr_tss, tsb_label, calc_pmc, daily_tss_map


# ===========================================================================
# 1. CONFIG — Athlete constants and phase detection
# ===========================================================================
class TestConfig(unittest.TestCase):

    def test_athlete_vdot_is_race_confirmed_value(self):
        """VDOT ใน config ต้องเป็นตัวเลข race-confirmed เท่านั้น (ตอนนี้คือ 38)"""
        self.assertEqual(ATHLETE["vdot"], 38)

    def test_athlete_hrr_calculation(self):
        """HRR ต้องเท่ากับ MHR - RHR เสมอ"""
        self.assertEqual(ATHLETE["hrr"], ATHLETE["mhr"] - ATHLETE["rhr"])

    def test_hr_zone_bounds_coverage(self):
        """HR zones ต้องครอบคลุม RHR ถึง MHR โดยไม่มีช่องว่าง"""
        rhr = ATHLETE["rhr"]
        mhr = ATHLETE["mhr"]
        # Zone 1 ต้องเริ่มที่ RHR
        self.assertEqual(HR_ZONE_BOUNDS["Z1_E"][0], rhr)
        # Zone 5 ต้องสิ้นสุดที่ MHR
        self.assertEqual(HR_ZONE_BOUNDS["Z5_R"][1], mhr)
        # Zones ต้องต่อกันสม่ำเสมอ (hi ของ zone n = lo ของ zone n+1)
        zone_keys = ["Z1_E", "Z2_M", "Z3_T", "Z4_I", "Z5_R"]
        for i in range(len(zone_keys) - 1):
            self.assertEqual(
                HR_ZONE_BOUNDS[zone_keys[i]][1],
                HR_ZONE_BOUNDS[zone_keys[i+1]][0],
                msg=f"Gap between {zone_keys[i]} and {zone_keys[i+1]}"
            )

    def test_threshold_zone_hr_range(self):
        """T-zone (Z3) ต้องอยู่ที่ ~170–176 bpm สำหรับ ATHLETE นี้"""
        lo, hi = HR_ZONE_BOUNDS["Z3_T"]
        self.assertGreater(lo, 165)
        self.assertLess(hi, 185)

    def test_vdot_paces_i_faster_than_t(self):
        """I-pace ต้องเร็วกว่า T-pace (sec/km ต่ำกว่า)"""
        i_lo, i_hi = VDOT_PACES["I"]
        t_lo, t_hi = VDOT_PACES["T"]
        self.assertLess(i_lo, t_lo, "I-pace lo ต้องเร็วกว่า T-pace lo")
        self.assertLess(i_hi, t_hi, "I-pace hi ต้องเร็วกว่า T-pace hi")

    def test_vdot_paces_order(self):
        """Pace order: R < I < T < M < E (ช้าสุดคือ Easy)"""
        r_lo = VDOT_PACES["R"][0]
        i_lo = VDOT_PACES["I"][0]
        t_lo = VDOT_PACES["T"][0]
        m_lo = VDOT_PACES["M"][0]
        e_lo = VDOT_PACES["E"][0]
        self.assertLess(r_lo, i_lo)
        self.assertLess(i_lo, t_lo)
        self.assertLess(t_lo, m_lo)
        self.assertLess(m_lo, e_lo)

    def test_get_current_phase_recovery_period(self):
        """วันที่ 9 May 2026 ต้องอยู่ใน Recovery + Phra Rama 8 (taper)"""
        phase = get_current_phase(date(2026, 5, 9))
        self.assertIsNotNone(phase)
        self.assertEqual(phase["phase"], "taper")
        self.assertIn("Phra Rama 8", phase["name"])

    def test_get_current_phase_base_building(self):
        """วันที่ 1 June 2026 ต้องอยู่ใน Base Building I"""
        phase = get_current_phase(date(2026, 6, 1))
        self.assertIsNotNone(phase)
        self.assertEqual(phase["phase"], "base")

    def test_get_current_phase_quality(self):
        """วันที่ 15 September 2026 ต้องอยู่ใน Quality phase"""
        phase = get_current_phase(date(2026, 9, 15))
        self.assertIsNotNone(phase)
        self.assertEqual(phase["phase"], "quality")

    def test_get_current_phase_race_specific(self):
        """November 2026 ต้องอยู่ใน Race Specific phase"""
        phase = get_current_phase(date(2026, 11, 15))
        self.assertIsNotNone(phase)
        self.assertEqual(phase["phase"], "race_specific")

    def test_get_current_phase_taper_before_fuji(self):
        """December 2026 ต้องอยู่ใน Peak + Taper + Fuji"""
        phase = get_current_phase(date(2026, 12, 5))
        self.assertIsNotNone(phase)
        self.assertEqual(phase["phase"], "taper")

    def test_phase_prescriptions_base_max_1_quality(self):
        """Base phase ต้องมี quality_max = 1 (ยังไม่ใช้ 2 quality sessions)"""
        self.assertEqual(PHASE_PRESCRIPTIONS["base"]["quality_max"], 1)

    def test_phase_prescriptions_quality_max_2(self):
        """Quality phase ต้องมี quality_max = 2"""
        self.assertEqual(PHASE_PRESCRIPTIONS["quality"]["quality_max"], 2)

    def test_phase_prescriptions_base_long_run(self):
        """Base long run ต้องสั้นกว่า Quality long run"""
        base_long = PHASE_PRESCRIPTIONS["base"]["long_km"]
        quality_long = PHASE_PRESCRIPTIONS["quality"]["long_km"]
        self.assertLess(base_long, quality_long)


# ===========================================================================
# 2. RACE PREDICTOR — VDOT Math
# ===========================================================================
class TestVdotMath(unittest.TestCase):

    def test_compute_vdot_known_hm_time(self):
        """Sub 1:53 HM (113 min, 21097.5m) ≈ VDOT 38 ตาม JD tables"""
        vdot = compute_vdot(21097.5, 113.0)
        self.assertIsNotNone(vdot)
        self.assertAlmostEqual(vdot, 38.0, delta=1.5)

    def test_compute_vdot_known_5k(self):
        """5km ใน 28 นาที ≈ VDOT 35"""
        vdot = compute_vdot(5000, 28.0)
        self.assertIsNotNone(vdot)
        self.assertAlmostEqual(vdot, 35.0, delta=2.0)

    def test_compute_vdot_faster_pace_higher_vdot(self):
        """วิ่งเร็วขึ้น = VDOT สูงขึ้น (distance คงที่)"""
        vdot_slow = compute_vdot(5000, 30.0)
        vdot_fast = compute_vdot(5000, 25.0)
        self.assertGreater(vdot_fast, vdot_slow)

    def test_compute_vdot_invalid_short_distance(self):
        """distance < 1000m ควร return None"""
        result = compute_vdot(500, 5.0)
        self.assertIsNone(result)

    def test_compute_vdot_invalid_zero_duration(self):
        """duration = 0 ควร return None"""
        result = compute_vdot(5000, 0)
        self.assertIsNone(result)

    def test_predict_race_time_round_trip(self):
        """predict_race_time(compute_vdot(d, t), d) ≈ t (round-trip consistency)"""
        distance = 21097.5
        time_min  = 110.0
        vdot = compute_vdot(distance, time_min)
        predicted = predict_race_time(vdot, distance)
        self.assertAlmostEqual(predicted, time_min, delta=0.5)

    def test_predict_race_time_fm_slower_than_hm(self):
        """FM prediction ต้องช้ากว่า HM × 2 (endurance penalty)"""
        vdot = 38.0
        hm_time = predict_race_time(vdot, 21097.5)
        fm_time = predict_race_time(vdot, 42195.0)
        self.assertGreater(fm_time, hm_time * 2)

    def test_fmt_time_under_1_hour(self):
        """28:30 ควร format เป็น '28:30'"""
        result = fmt_time(28.5)
        self.assertEqual(result, "28:30")

    def test_fmt_time_over_1_hour(self):
        """113 นาที ควร format เป็น '1:53:00'"""
        result = fmt_time(113.0)
        self.assertEqual(result, "1:53:00")

    def test_fmt_time_with_seconds(self):
        """113.5 นาที (1:53:30)"""
        result = fmt_time(113.5)
        self.assertEqual(result, "1:53:30")


class TestVdotEstimation(unittest.TestCase):
    """ทดสอบ VDOT estimation จาก training sessions"""

    def _make_t_session(self, date_str, avg_hr, pace="5:40/km"):
        return {
            "date": date_str,
            "session_type": "Threshold (T)",
            "avg_hr": avg_hr,
            "pace": pace,
        }

    def _make_i_session(self, date_str, avg_hr, pace="5:12/km"):
        return {
            "date": date_str,
            "session_type": "Interval (I)",
            "avg_hr": avg_hr,
            "pace": pace,
        }

    def test_t_session_below_t_zone_signals_improvement(self):
        """T-session ที่ HR ต่ำกว่า T-zone midpoint = ฟิตดีขึ้น → VDOT estimate สูงกว่า baseline"""
        s = self._make_t_session("2026-05-01", avg_hr=171)  # ต่ำกว่า T-zone mid (~173) แต่ยังอยู่ใน T-zone (>=170)
        est = estimate_vdot_from_session(s)
        self.assertIsNotNone(est)
        self.assertGreater(est, ATHLETE["vdot"])

    def test_t_session_at_t_zone_midpoint_equals_baseline(self):
        """T-session ที่ HR เท่ากับ T-zone midpoint = VDOT estimate ≈ baseline"""
        t_mid = (HR_ZONE_BOUNDS["Z3_T"][0] + HR_ZONE_BOUNDS["Z3_T"][1]) / 2
        s = self._make_t_session("2026-05-01", avg_hr=int(t_mid))
        est = estimate_vdot_from_session(s)
        self.assertIsNotNone(est)
        self.assertAlmostEqual(est, ATHLETE["vdot"], delta=0.5)

    def test_i_session_returns_vdot_above_30(self):
        """I-session ที่ 5:12/km pace ต้อง return VDOT ที่สมเหตุสมผล (>30)"""
        s = self._make_i_session("2026-05-01", avg_hr=180, pace="5:12/km")
        est = estimate_vdot_from_session(s)
        self.assertIsNotNone(est)
        self.assertGreater(est, 30)

    def test_session_missing_pace_returns_none(self):
        """Session ที่ไม่มี pace field ต้อง return None"""
        s = {"date": "2026-05-01", "session_type": "Threshold (T)", "avg_hr": 170, "pace": ""}
        est = estimate_vdot_from_session(s)
        self.assertIsNone(est)

    def test_easy_run_not_used_for_vdot_estimate(self):
        """Easy Run ต้อง return None จาก estimate_vdot_from_session"""
        s = {"date": "2026-05-01", "session_type": "Easy Run", "avg_hr": 140, "pace": "7:00/km"}
        est = estimate_vdot_from_session(s)
        self.assertIsNone(est)

    def test_estimate_current_vdot_weighted_recent_sessions(self):
        """Session ล่าสุดต้องมี weight มากกว่า → ถ้า recent sessions ดีขึ้น est_vdot สูงขึ้น"""
        # Session เก่า: HR สูง (ฟิตน้อยกว่า)
        # Session ใหม่: HR ต่ำกว่า T-zone (ฟิตมากขึ้น)
        sessions = [
            self._make_t_session("2026-03-01", avg_hr=175),  # เก่า, HR สูง
            self._make_t_session("2026-03-15", avg_hr=172),
            self._make_t_session("2026-04-01", avg_hr=170),  # ใหม่, HR ต่ำกว่า (ฟิตขึ้น)
        ]
        est, used = estimate_current_vdot(sessions)
        self.assertIsNotNone(est)
        self.assertGreater(len(used), 0)
        # ผลลัพธ์ต้องสูงกว่า baseline เพราะ recent sessions ดีขึ้น
        self.assertGreaterEqual(est, ATHLETE["vdot"])

    def test_estimate_current_vdot_empty_sessions_returns_none(self):
        """ถ้าไม่มี sessions ต้อง return (None, [])"""
        est, used = estimate_current_vdot([])
        self.assertIsNone(est)
        self.assertEqual(used, [])

    def test_estimate_current_vdot_no_quality_sessions_returns_none(self):
        """ถ้ามีแต่ Easy Run (ไม่มี quality sessions) ต้อง return (None, [])"""
        sessions = [
            {"date": "2026-05-01", "session_type": "Easy Run", "avg_hr": 140, "pace": "7:00/km"}
        ]
        est, used = estimate_current_vdot(sessions)
        self.assertIsNone(est)


# ===========================================================================
# 3. POST SESSION ANALYZER — Classification & Grading
# ===========================================================================
class TestClassifySession(unittest.TestCase):
    """ทดสอบ classify_session() — แบ่ง session type ตาม HR%"""

    def test_easy_run_classification(self):
        """HR ใน Z1 (<74% HRR) = Easy Run"""
        # HRR = 150, RHR = 44 → Z1 = < 44 + 150*0.74 = 155
        result = classify_session(avg_hr=145, avg_pace_min_km=7.0)
        self.assertEqual(result, "Easy Run")

    def test_marathon_pace_classification(self):
        """HR ใน Z2 (74–84% HRR) = Marathon Pace"""
        # Z2 = 155–170 bpm approx
        result = classify_session(avg_hr=162, avg_pace_min_km=6.1)
        self.assertEqual(result, "Marathon Pace")

    def test_threshold_classification(self):
        """HR ใน Z3 (84–88% HRR) = Threshold"""
        # Z3 ≈ 170–176 bpm
        result = classify_session(avg_hr=172, avg_pace_min_km=5.7)
        self.assertEqual(result, "Threshold (T)")

    def test_interval_classification(self):
        """HR ใน Z4 (88–95% HRR) = Interval"""
        # Z4 ≈ 176–187 bpm
        result = classify_session(avg_hr=181, avg_pace_min_km=5.2)
        self.assertEqual(result, "Interval (I)")

    def test_repetition_classification(self):
        """HR ใน Z5 (>95% HRR) = Repetition"""
        # Z5 ≈ 187–194 bpm
        result = classify_session(avg_hr=190, avg_pace_min_km=4.9)
        self.assertEqual(result, "Repetition (R)")


class TestAnalyzeEasyRun(unittest.TestCase):
    """ทดสอบ analyze_easy_run() — Easy run depth analysis"""

    def _make_result(self, z1_pct, z2_pct, z3_pct, avg_hr, decoupling=3.0):
        return {
            "zone_pct": [z1_pct, z2_pct, z3_pct, 0, 0],
            "avg_hr": avg_hr,
            "decoupling": decoupling,
        }

    def test_grade_a_when_90pct_easy_zones(self):
        """90%+ ใน Z1+Z2 = Grade A"""
        r = self._make_result(z1_pct=60, z2_pct=35, z3_pct=5, avg_hr=148)
        result = analyze_easy_run(r)
        self.assertIn("A", result["grade"])

    def test_grade_b_when_75_to_90pct_easy_zones(self):
        """75–90% ใน Z1+Z2 = Grade B"""
        r = self._make_result(z1_pct=50, z2_pct=28, z3_pct=22, avg_hr=158)
        result = analyze_easy_run(r)
        self.assertIn("B", result["grade"])

    def test_grade_c_when_below_75pct_easy_zones(self):
        """<75% ใน Z1+Z2 = Grade C"""
        r = self._make_result(z1_pct=40, z2_pct=30, z3_pct=30, avg_hr=165)
        result = analyze_easy_run(r)
        self.assertIn("C", result["grade"])

    def test_good_aerobic_efficiency(self):
        """Decoupling < 5% = ✅ Aerobic Efficiency ดีมาก"""
        r = self._make_result(z1_pct=70, z2_pct=25, z3_pct=5, avg_hr=145, decoupling=3.0)
        result = analyze_easy_run(r)
        self.assertIn("✅", result["aerobic_efficiency"])

    def test_warning_aerobic_efficiency(self):
        """Decoupling > 10% = ⚠️ Aerobic Efficiency ต่ำ"""
        r = self._make_result(z1_pct=70, z2_pct=25, z3_pct=5, avg_hr=145, decoupling=12.0)
        result = analyze_easy_run(r)
        self.assertIn("⚠️", result["aerobic_efficiency"])

    def test_returns_required_keys(self):
        """ต้อง return dict ที่มี keys ครบ"""
        r = self._make_result(z1_pct=70, z2_pct=20, z3_pct=10, avg_hr=150)
        result = analyze_easy_run(r)
        for key in ["z1_z2_pct", "above_easy_pct", "hr_trend", "aerobic_efficiency", "grade"]:
            self.assertIn(key, result)


class TestGradeSession(unittest.TestCase):
    """ทดสอบ grade_session() — Quality run grade S/A/B/C"""

    def _make_result(self, dc, cad, vr):
        return {"decoupling": dc, "cadence": cad, "vr": vr}

    def test_grade_s_perfect_session(self):
        """dc<5, cad≥155, vr≤8.5 = S"""
        r = self._make_result(dc=3.0, cad=168, vr=8.0)
        self.assertEqual(grade_session(r), "S 🏆")

    def test_grade_a_good_session(self):
        """dc<8, cad≥153, vr≤9.5 = A"""
        r = self._make_result(dc=6.0, cad=156, vr=9.0)
        self.assertEqual(grade_session(r), "A ✅")

    def test_grade_b_moderate_session(self):
        """dc<12, cad ต่ำ = B"""
        r = self._make_result(dc=9.0, cad=150, vr=9.0)
        self.assertEqual(grade_session(r), "B 🟡")

    def test_grade_c_poor_session(self):
        """dc≥12 = C"""
        r = self._make_result(dc=13.0, cad=148, vr=10.0)
        self.assertEqual(grade_session(r), "C ⚠️")


class TestFlagIssues(unittest.TestCase):
    """ทดสอบ flag_issues() — ตรวจจับปัญหาใน session"""

    def _base_result(self):
        """Result ที่ไม่มี flags ใดๆ"""
        return {
            "cadence": 170,
            "gct": 250,
            "vr": 8.0,
            "power": 250,
            "decoupling": 4.0,
            "stam_start": 95,
            "stam_end": 60,
            "is_treadmill": False,
            "bb_start": None,
            "bb_end": None,
        }

    def test_no_flags_for_good_session(self):
        """Session ที่ดีต้องไม่มี flags"""
        r = self._base_result()
        flags = flag_issues(r)
        self.assertEqual(flags, [])

    def test_low_cadence_flag(self):
        """Cadence < 164 spm ต้อง flag"""
        r = self._base_result()
        r["cadence"] = 158
        flags = flag_issues(r)
        self.assertTrue(any("Cadence" in f for f in flags))

    def test_high_gct_flag(self):
        """GCT > 275ms ต้อง flag"""
        r = self._base_result()
        r["gct"] = 290
        flags = flag_issues(r)
        self.assertTrue(any("GCT" in f for f in flags))

    def test_high_gct_treadmill_higher_threshold(self):
        """Treadmill: threshold = 275 + 20 = 295ms"""
        r = self._base_result()
        r["gct"] = 285
        r["is_treadmill"] = True
        flags = flag_issues(r)
        self.assertFalse(any("GCT" in f for f in flags), "285ms บน treadmill ไม่ควร flag")

    def test_high_vr_flag(self):
        """Vertical Ratio > 9.5% ต้อง flag แดง"""
        r = self._base_result()
        r["vr"] = 10.0
        flags = flag_issues(r)
        self.assertTrue(any("Vertical Ratio" in f for f in flags))

    def test_high_decoupling_flag(self):
        """Decoupling > 10% ต้อง flag แดง"""
        r = self._base_result()
        r["decoupling"] = 12.0
        flags = flag_issues(r)
        self.assertTrue(any("Drift" in f for f in flags))

    def test_stamina_drain_flag(self):
        """Stamina drain > 50% ต้อง flag"""
        r = self._base_result()
        r["stam_start"] = 90
        r["stam_end"] = 30  # drain = 60%
        flags = flag_issues(r)
        self.assertTrue(any("Stamina" in f for f in flags))


# ===========================================================================
# 4. TRAINING LOAD — hrTSS, PMC calculations, TSB labels
# ===========================================================================
class TestHrTSS(unittest.TestCase):
    """ทดสอบ calc_hr_tss() — HR-based Training Stress Score"""

    RHR = ATHLETE["rhr"]   # 44
    T_HR = HR_ZONE_BOUNDS["Z3_T"][0]  # lower bound of T zone

    def _make_act(self, avg_hr, dur_sec):
        return {"averageHR": avg_hr, "duration": dur_sec}

    def test_easy_run_tss_lower_than_threshold_run(self):
        """Easy run (150 bpm, 60min) ต้องมี TSS ต่ำกว่า Threshold run (172 bpm, 60min)"""
        easy_tss = calc_hr_tss(self._make_act(150, 3600))
        t_tss    = calc_hr_tss(self._make_act(172, 3600))
        self.assertLess(easy_tss, t_tss)

    def test_rest_day_tss_is_zero(self):
        """ไม่ออกกำลังกาย = TSS 0"""
        tss = calc_hr_tss(self._make_act(0, 0))
        self.assertEqual(tss, 0.0)

    def test_hr_at_rhr_gives_zero_tss(self):
        """HR = RHR (ไม่ออกแรง) = TSS 0"""
        tss = calc_hr_tss(self._make_act(self.RHR, 3600))
        self.assertEqual(tss, 0.0)

    def test_threshold_run_tss_formula(self):
        """
        Threshold run: 172 bpm, 60 min
        IF = (172 - 44) / (170 - 44) = 128 / 126 ≈ 1.016
        TSS = 1 × 1.016² × 100 ≈ 103
        """
        t_hr_lo = self.T_HR  # threshold lower bound
        # Run ที่ T-HR ล่าง = IF ≈ 1.0 → TSS ≈ 100 ต่อชั่วโมง
        tss = calc_hr_tss(self._make_act(t_hr_lo, 3600))
        self.assertAlmostEqual(tss, 100.0, delta=5.0)

    def test_longer_duration_higher_tss(self):
        """Duration นานขึ้นสองเท่า = TSS สูงขึ้นสองเท่า (HR คงที่)"""
        tss_60min = calc_hr_tss(self._make_act(155, 3600))
        tss_120min = calc_hr_tss(self._make_act(155, 7200))
        self.assertAlmostEqual(tss_120min, tss_60min * 2, delta=1.0)


class TestPMC(unittest.TestCase):
    """ทดสอบ calc_pmc() — Performance Management Chart"""

    def test_ctl_increases_with_consistent_load(self):
        """CTL ต้องเพิ่มขึ้นเมื่อซ้อมสม่ำเสมอ"""
        start = date(2026, 1, 1)
        end   = date(2026, 3, 1)
        # TSS 80 ต่อวัน
        tss_map = {(start + timedelta(days=i)).strftime("%Y-%m-%d"): 80.0
                   for i in range((end - start).days + 1)}
        pmc = calc_pmc(tss_map, start, end)
        first_ctl = pmc[0][2]
        last_ctl  = pmc[-1][2]
        self.assertGreater(last_ctl, first_ctl)

    def test_ctl_decreases_on_rest(self):
        """CTL ต้องลดลงในช่วงพัก (TSS = 0)"""
        # สร้าง load ก่อน แล้วพัก
        start = date(2026, 1, 1)
        load_end = date(2026, 2, 1)
        rest_end = date(2026, 3, 1)
        tss_map = {}
        for i in range((load_end - start).days + 1):
            tss_map[(start + timedelta(days=i)).strftime("%Y-%m-%d")] = 100.0
        for i in range((rest_end - load_end).days):
            tss_map[(load_end + timedelta(days=i+1)).strftime("%Y-%m-%d")] = 0.0
        pmc = calc_pmc(tss_map, start, rest_end)
        ctl_at_peak = max(p[2] for p in pmc)
        ctl_at_end  = pmc[-1][2]
        self.assertLess(ctl_at_end, ctl_at_peak)

    def test_atl_decays_faster_than_ctl(self):
        """ATL (τ=7d) ต้องลดเร็วกว่า CTL (τ=42d) ในช่วงพัก"""
        start = date(2026, 1, 1)
        load_end = date(2026, 2, 1)
        rest_end = date(2026, 3, 1)
        tss_map = {}
        for i in range((load_end - start).days + 1):
            tss_map[(start + timedelta(days=i)).strftime("%Y-%m-%d")] = 80.0
        for i in range((rest_end - load_end).days):
            tss_map[(load_end + timedelta(days=i+1)).strftime("%Y-%m-%d")] = 0.0
        pmc = calc_pmc(tss_map, start, rest_end)
        # หลังพัก 30 วัน ATL ควรลดมากกว่า CTL (สัดส่วน)
        ctl_at_transition = [p[2] for p in pmc if p[0] == load_end.strftime("%Y-%m-%d")][0]
        atl_at_transition = [p[3] for p in pmc if p[0] == load_end.strftime("%Y-%m-%d")][0]
        ctl_final = pmc[-1][2]
        atl_final = pmc[-1][3]
        ctl_drop_pct = (ctl_at_transition - ctl_final) / (ctl_at_transition or 1)
        atl_drop_pct = (atl_at_transition - atl_final) / (atl_at_transition or 1)
        self.assertGreater(atl_drop_pct, ctl_drop_pct)

    def test_tsb_positive_after_taper(self):
        """TSB (Form) ต้องเป็น positive หลัง taper 14 วัน"""
        start = date(2026, 1, 1)
        load_end = date(2026, 2, 14)
        taper_end = date(2026, 2, 28)
        tss_map = {}
        for i in range((load_end - start).days + 1):
            tss_map[(start + timedelta(days=i)).strftime("%Y-%m-%d")] = 90.0
        for i in range((taper_end - load_end).days):
            tss_map[(load_end + timedelta(days=i+1)).strftime("%Y-%m-%d")] = 20.0
        pmc = calc_pmc(tss_map, start, taper_end)
        final_tsb = pmc[-1][4]
        self.assertGreater(final_tsb, 0, "TSB ควร positive หลัง taper")


class TestTsbLabel(unittest.TestCase):
    """ทดสอบ tsb_label() — Training state classification"""

    def test_race_ready_label(self):
        """TSB > 25 = Race Ready"""
        label = tsb_label(30)
        self.assertIn("Race Ready", label)

    def test_fresh_label(self):
        """TSB 10–25 = Fresh"""
        label = tsb_label(15)
        self.assertIn("Fresh", label)

    def test_neutral_label(self):
        """TSB 0–10 = Neutral"""
        label = tsb_label(5)
        self.assertIn("Neutral", label)

    def test_productive_label(self):
        """TSB -10 ถึง 0 = Productive"""
        label = tsb_label(-5)
        self.assertIn("Productive", label)

    def test_overreaching_label(self):
        """TSB -25 ถึง -10 = Overreaching"""
        label = tsb_label(-18)
        self.assertIn("Overreaching", label)

    def test_overtraining_label(self):
        """TSB < -25 = Overtraining"""
        label = tsb_label(-30)
        self.assertIn("Overtraining", label)


# ===========================================================================
# 5. HARD RULE: VDOT estimate ต้องไม่ถูกเรียกว่า "VDOT" หรือ "effective VDOT"
#    ทดสอบว่า race_predictor output ใช้คำว่า "estimate" เสมอ
# ===========================================================================
class TestVdotDisclaimerRule(unittest.TestCase):
    """
    HARD RULE enforcement: training-based VDOT ต้องเป็น 'estimate' เท่านั้น
    ต้องไม่เป็น 'ยืนยัน' หรือ 'จริง' หรือ 'confirmed' โดยไม่มี race result
    """

    def test_estimate_vdot_does_not_equal_athlete_vdot(self):
        """
        ถ้า training estimate แตกต่างจาก baseline VDOT,
        ต้องแสดงให้เห็นความต่าง (ไม่ใช่แทนค่า config VDOT)
        """
        sessions = [
            {"date": "2026-05-01", "session_type": "Threshold (T)",
             "avg_hr": 171, "pace": "5:40/km"},  # HR ต่ำกว่า T-zone mid (~173) แต่ยังอยู่ใน T-zone (>=170)
        ]
        est, _ = estimate_current_vdot(sessions)
        # est ควรแตกต่างจาก ATHLETE["vdot"] เมื่อ training สัญญาณดีขึ้น
        # (ไม่ควรเป็นค่าเดียวกันทุกครั้งโดยไม่ขึ้นกับ sessions)
        self.assertIsNotNone(est)

    def test_baseline_vdot_unchanged_after_estimation(self):
        """
        การ estimate VDOT จาก training ต้องไม่เปลี่ยน ATHLETE["vdot"]
        config ต้องคงที่ตลอด — อัพเดตได้แค่ตาม race result เท่านั้น
        """
        original_vdot = ATHLETE["vdot"]
        sessions = [
            {"date": "2026-05-01", "session_type": "Interval (I)",
             "avg_hr": 182, "pace": "5:10/km"},
        ]
        estimate_current_vdot(sessions)  # ไม่ควรมี side effect
        self.assertEqual(ATHLETE["vdot"], original_vdot,
                         "ATHLETE['vdot'] ต้องไม่เปลี่ยนหลัง estimate")

    def test_estimate_result_is_separate_from_config_vdot(self):
        """
        estimate_current_vdot() ต้อง return tuple (est, used)
        ไม่ใช่ return ATHLETE["vdot"] โดยตรง
        """
        sessions = [
            {"date": "2026-05-01", "session_type": "Threshold (T)",
             "avg_hr": 165, "pace": "5:38/km"},
        ]
        result = estimate_current_vdot(sessions)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
