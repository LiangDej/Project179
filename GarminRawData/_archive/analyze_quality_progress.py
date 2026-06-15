import json
import os
import glob
import math
from datetime import datetime

# --- Analysis Functions ---

def calculate_decoupling(metrics, hr_idx, dist_idx):
    def get_val(p, idx):
        if idx is not None and idx < len(p):
            return p[idx]
        return None

    valid_points = [p for p in metrics if get_val(p, hr_idx) is not None and get_val(p, dist_idx) is not None]
    if len(valid_points) < 100:
        return None
    
    mid = len(valid_points) // 2
    first_half = valid_points[:mid]
    second_half = valid_points[mid:]
    
    def get_efficiency(pts):
        avg_hr = sum(get_val(p, hr_idx) for p in pts) / len(pts)
        total_dist = get_val(pts[-1], dist_idx) - get_val(pts[0], dist_idx)
        total_time = len(pts)
        if total_time == 0 or total_dist == 0: return 0
        speed = total_dist / total_time
        return avg_hr / speed if speed > 0 else 0

    eff1 = get_efficiency(first_half)
    eff2 = get_efficiency(second_half)
    
    if eff1 == 0: return 0
    decoupling = ((eff2 / eff1) - 1) * 100
    return round(decoupling, 2)

def process_file(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    date_str = os.path.basename(filepath).split('_')[1]
    
    # Find indices
    descriptors = {d['key']: d['metricsIndex'] for d in data.get('metricDescriptors', [])}
    hr_idx = descriptors.get('directHeartRate')
    dist_idx = descriptors.get('sumDistance')
    speed_idx = descriptors.get('directSpeed')
    cadence_idx = descriptors.get('directRunCadence')
    
    metrics_dicts = data.get('activityDetailMetrics', [])
    metrics = [m.get('metrics', []) for m in metrics_dicts]
    
    def get_val(p, idx):
        if idx is not None and idx < len(p):
            return p[idx]
        return None

    hr_data = [get_val(p, hr_idx) for p in metrics if get_val(p, hr_idx) is not None]
    if not hr_data: return None
    
    avg_hr = sum(hr_data) / len(hr_data)
    max_hr = max(hr_data)
    
    # Avg Speed
    speeds = [get_val(p, speed_idx) for p in metrics if get_val(p, speed_idx) is not None and get_val(p, speed_idx) > 1.0]
    avg_speed = sum(speeds) / len(speeds) if speeds else 0
    pace_min_km = 1000 / (avg_speed * 60) if avg_speed > 0 else 0
    
    # Decoupling
    decoupling = calculate_decoupling(metrics, hr_idx, dist_idx)
    
    return {
        "date": date_str,
        "avg_hr": int(avg_hr),
        "max_hr": max_hr,
        "avg_pace": pace_min_km,
        "decoupling": decoupling
    }

def run_analysis():
    files = sorted(glob.glob("QualityDetails/quality_*.json"))
    results = []
    for f in files:
        res = process_file(f)
        if res:
            results.append(res)
    
    print("# 📈 Quality Run Analysis Report\n")
    print("| Date | Avg Pace | Avg HR | Max HR | Decoupling |")
    print("|---|---|---|---|---|")
    for r in results:
        pace_m = int(r['avg_pace'])
        pace_s = int((r['avg_pace'] - pace_m) * 60)
        dec_str = f"{r['decoupling']}%" if r['decoupling'] is not None else "N/A"
        print(f"| {r['date']} | {pace_m}:{pace_s:02d}/km | {r['avg_hr']} | {r['max_hr']} | {dec_str} |")

if __name__ == "__main__":
    run_analysis()
