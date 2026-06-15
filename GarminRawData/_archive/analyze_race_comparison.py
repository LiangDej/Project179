import json
import os
import glob
from datetime import datetime

def get_val(p, idx):
    if idx is not None and idx < len(p):
        return p[idx]
    return None

def calculate_decoupling(metrics, hr_idx, dist_idx, speed_idx):
    valid_points = [p for p in metrics if get_val(p, hr_idx) is not None and get_val(p, dist_idx) is not None]
    if len(valid_points) < 500: return None
    mid = len(valid_points) // 2
    h1 = valid_points[:mid]; h2 = valid_points[mid:]
    def get_eff(pts):
        hr_avg = sum(get_val(p, hr_idx) for p in pts) / len(pts)
        spd_pts = [get_val(p, speed_idx) for p in pts if get_val(p, speed_idx) and get_val(p, speed_idx) > 1.0]
        spd_avg = sum(spd_pts) / len(spd_pts) if spd_pts else 1
        return hr_avg / spd_avg if spd_avg > 0 else 0
    e1 = get_eff(h1); e2 = get_eff(h2)
    return round(((e2/e1)-1)*100, 2) if e1 > 0 else 0

def process_race(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)
    name = os.path.basename(filepath).split('_')[0] + "_" + os.path.basename(filepath).split('_')[1]
    descriptors = {d['key']: d['metricsIndex'] for d in data.get('metricDescriptors', [])}
    hr_idx = descriptors.get('directHeartRate')
    dist_idx = descriptors.get('sumDistance')
    speed_idx = descriptors.get('directSpeed')
    cad_idx = descriptors.get('directRunCadence')
    gct_idx = descriptors.get('directGroundContactTime')
    vr_idx = descriptors.get('directVerticalRatio')
    stride_idx = descriptors.get('directStrideLength')

    metrics_dicts = data.get('activityDetailMetrics', [])
    metrics = [m.get('metrics', []) for m in metrics_dicts]
    
    hrs = [get_val(p, hr_idx) for p in metrics if get_val(p, hr_idx)]
    avg_hr = sum(hrs)/len(hrs) if hrs else 0
    max_hr = max(hrs) if hrs else 0
    
    speeds = [get_val(p, speed_idx) for p in metrics if get_val(p, speed_idx) and get_val(p, speed_idx) > 1.5]
    avg_speed = sum(speeds)/len(speeds) if speeds else 0
    pace = 1000/(avg_speed*60) if avg_speed > 0 else 0
    
    cads = [get_val(p, cad_idx) for p in metrics if get_val(p, cad_idx)]
    avg_cad = (sum(cads)/len(cads))*2 if cads else 0 # double cadence
    
    gcts = [get_val(p, gct_idx) for p in metrics if get_val(p, gct_idx)]
    avg_gct = sum(gcts)/len(gcts) if gcts else 0
    
    vrs = [get_val(p, vr_idx) for p in metrics if get_val(p, vr_idx)]
    avg_vr = sum(vrs)/len(vrs) if vrs else 0

    strides = [get_val(p, stride_idx) for p in metrics if get_val(p, stride_idx)]
    avg_stride = (sum(strides)/len(strides)) / 100 if strides else 0
    
    dec = calculate_decoupling(metrics, hr_idx, dist_idx, speed_idx)
    
    elapsed_idx = descriptors.get('sumElapsedDuration')
    duration_sec = get_val(metrics[-1], elapsed_idx) if metrics and elapsed_idx is not None else 0
    
    return {
        "name": name,
        "time": duration_sec,
        "pace": pace,
        "avg_hr": int(avg_hr),
        "max_hr": max_hr,
        "cadence": int(avg_cad),
        "gct": int(avg_gct),
        "vr": round(avg_vr, 1),
        "stride": round(avg_stride, 2),
        "decoupling": dec
    }

def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def run_comparison():
    files = sorted(glob.glob("RaceHistory/*.json"))
    results = []
    for f in files:
        results.append(process_race(f))
    
    print("# 🏁 Historical Race Comparison\n")
    print("| Race | Time | Avg Pace | Avg HR | Decoupling | Cadence | GCT | Stride | VR |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        pm = int(r['pace']); ps = int((r['pace']-pm)*60)
        print(f"| {r['name']} | {format_time(r['time'])} | {pm}:{ps:02d}/km | {r['avg_hr']} | {r['decoupling']}% | {r['cadence']} | {r['gct']}ms | {r['stride']}m | {r['vr']}% |")

if __name__ == "__main__":
    run_comparison()
