"""Build Shanghai satellite-overflight data from CelesTrak GP/OMM CSV.

The production path downloads one ACTIVE catalog snapshot.  A local TLE input is
supported only to make the repository reproducible before its first Action run.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sgp4.api import Satrec, SatrecArray, jday
from sgp4 import omm


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
HISTORY_PATH = ROOT / "data" / "history.json"
ARTIFACTS = ROOT / "artifacts"
SOURCE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=ACTIVE&FORMAT=CSV"
LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
LAT_DEG, LON_DEG, OBS_ALT_KM = 31.2304, 121.4737, 0.012
MIN_ELEV_DEG, STEP_MIN = 10.0, 5
EARTH_RADIUS_KM = 6371.0088


def classify(name: str) -> str:
    n = name.upper()
    groups = [
        ("Starlink", ("STARLINK",)),
        ("其他通信", ("ONEWEB", "IRIDIUM", "GLOBALSTAR", "ORBCOMM", "INTELSAT", "EUTELSAT", "INMARSAT", "SES ", "TELSTAR", "ASIASAT", "CHINASAT", "ZHONGXING", "APSTAR", "QIANFAN", "GUOWANG", "KUIPER")),
        ("导航定位", ("GPS ", "NAVSTAR", "GLONASS", "GALILEO", "BEIDOU", "QZSS", "NAVIC", "IRNSS")),
        ("气象", ("NOAA", "FENGYUN", "FY-", "METEOR-M", "METEOSAT", "GOES", "HIMAWARI", "ELEKTRO", "JPSS", "SUOMI NPP")),
        ("遥感观测", ("GAOFEN", "YAOGAN", "SENTINEL", "LANDSAT", "WORLDVIEW", "PLEIADES", "SKYSAT", "FLOCK", "PLANET", "JILIN", "ZIYUAN", "CARTOSAT", "SPOT ", "ICEYE", "CAPELLA", "RADARSAT", "KOMPSAT", "FORMOSAT", "EROS ", "GEOEYE")),
        ("科研/载人", ("ISS", "TIANHE", "WENTIAN", "MENGTIAN", "HST", "CHEOPS", "TESS", "SWIFT", "FERMI", "CHANDRA", "XMM", "ASTROSAT", "PROBA", "GRACE", "SWARM")),
    ]
    for label, terms in groups:
        if any(term in n for term in terms):
            return label
    return "其他/未分类"


def infer_operator(name: str, category: str) -> tuple[str, bool]:
    n = name.upper()
    if category == "Starlink":
        return "SpaceX", False
    rules = [
        ("Eutelsat OneWeb", ("ONEWEB",)),
        ("中国（名称推断）", ("BEIDOU", "GAOFEN", "FENGYUN", "FY-", "YAOGAN", "ZIYUAN", "TIANHE", "WENTIAN", "MENGTIAN", "CHINASAT", "ZHONGXING", "QIANFAN", "GUOWANG")),
        ("美国（名称推断）", ("GPS ", "NAVSTAR", "NOAA", "GOES", "LANDSAT", "JPSS", "SUOMI", "HST", "ISS")),
        ("欧洲（名称推断）", ("GALILEO", "SENTINEL", "METEOSAT")),
        ("俄罗斯（名称推断）", ("GLONASS", "METEOR-M", "COSMOS ")),
    ]
    for label, terms in rules:
        if any(term in n for term in terms):
            return label, label.endswith("推断）")
    return "未分类", True


def fetch_omm() -> tuple[bytes, str]:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ShanghaiOrbitalField/2.0 (+GitHub Pages science project)"})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            if response.status != 200:
                raise RuntimeError(f"CelesTrak returned HTTP {response.status}")
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"CelesTrak download failed once; deployment stopped: {exc}") from exc
    if len(body) < 1_000_000:
        raise RuntimeError(f"Catalog response is unexpectedly small ({len(body)} bytes)")
    return body, "CelesTrak ACTIVE GP/OMM CSV"


def load_omm_bytes(body: bytes):
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    required = {"OBJECT_NAME", "NORAD_CAT_ID", "EPOCH", "MEAN_MOTION"}
    if not rows or not required.issubset(rows[0]):
        raise RuntimeError("OMM CSV is missing required fields")
    names, ids, sats, epochs = [], [], [], []
    for row in rows:
        try:
            sat = Satrec()
            init_row = dict(row)
            if "." not in init_row["EPOCH"]:
                init_row["EPOCH"] += ".000000"
            omm.initialize(sat, init_row)
            names.append(row["OBJECT_NAME"].strip())
            ids.append(int(row["NORAD_CAT_ID"]))
            sats.append(sat)
            epochs.append(row["EPOCH"])
        except (ValueError, KeyError):
            continue
    return names, ids, sats, epochs


def load_tle(path: Path):
    lines = [line.rstrip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    names, ids, sats, epochs = [], [], [], []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if not l1.startswith("1 ") or not l2.startswith("2 "):
            continue
        try:
            sat = Satrec.twoline2rv(l1, l2)
            sat_id = int(l1[2:7])
        except ValueError:
            continue
        names.append(name); ids.append(sat_id); sats.append(sat); epochs.append("legacy TLE")
    return names, ids, sats, epochs


def gmst_rad(dates_utc):
    unix = np.array([d.timestamp() for d in dates_utc], dtype=float)
    jd = unix / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    deg = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t * t * t / 38710000.0
    return np.deg2rad(np.mod(deg, 360.0))


def observer_ecef():
    a, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    lat, lon = math.radians(LAT_DEG), math.radians(LON_DEG)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return np.array([(n + OBS_ALT_KM) * math.cos(lat) * math.cos(lon), (n + OBS_ALT_KM) * math.cos(lat) * math.sin(lon), (n * (1 - e2) + OBS_ALT_KM) * math.sin(lat)])


def footprint(alt_km: float) -> tuple[int, int]:
    e = math.radians(MIN_ELEV_DEG)
    h = max(1.0, alt_km)
    psi = math.acos(min(1.0, (EARTH_RADIUS_KM / (EARTH_RADIUS_KM + h)) * math.cos(e))) - e
    return round(2 * math.pi * EARTH_RADIUS_KM**2 * (1 - math.cos(psi))), round(2 * EARTH_RADIUS_KM * psi)


def analyze(day: datetime, names, ids, sats, cats, operators, epochs):
    times = [day + timedelta(minutes=STEP_MIN * i) for i in range(24 * 60 // STEP_MIN)]
    array, obs = SatrecArray(sats), observer_ecef()
    lat, lon = math.radians(LAT_DEG), math.radians(LON_DEG)
    catalog, frames, active, passes, daily_seen = {}, [], {}, [], set()
    hourly_seen, hourly_star = [set() for _ in range(24)], [set() for _ in range(24)]
    peak = 0

    def finish(idx, rec, end_minute):
        area, diameter = footprint(rec[3])
        passes.append([ids[idx], rec[0], end_minute, round(rec[1] * 10), round(rec[3]), round(rec[4] * 100), diameter, area])

    for c0 in range(0, len(times), 48):
        chunk = times[c0:c0 + 48]
        utc = [d.astimezone(timezone.utc) for d in chunk]
        jd, fr = jday(np.array([d.year for d in utc]), np.array([d.month for d in utc]), np.array([d.day for d in utc]), np.array([d.hour for d in utc]), np.array([d.minute for d in utc]), np.zeros(len(utc)))
        err, r_teme, v_teme = array.sgp4(jd, fr)
        theta = gmst_rad(utc); ct, st = np.cos(theta)[None, :], np.sin(theta)[None, :]
        x = ct * r_teme[:, :, 0] + st * r_teme[:, :, 1]
        y = -st * r_teme[:, :, 0] + ct * r_teme[:, :, 1]
        z = r_teme[:, :, 2]
        dx, dy, dz = x - obs[0], y - obs[1], z - obs[2]
        east = -math.sin(lon) * dx + math.cos(lon) * dy
        north = -math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz
        up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz
        elev = np.degrees(np.arctan2(up, np.hypot(east, north)))
        az = np.degrees(np.mod(np.arctan2(east, north), 2 * math.pi))
        elev[err != 0] = -90
        alt = np.linalg.norm(r_teme, axis=2) - EARTH_RADIUS_KM
        speed = np.linalg.norm(v_teme, axis=2)

        for j, dt in enumerate(chunk):
            minute = c0 * STEP_MIN + j * STEP_MIN
            above = set(map(int, np.flatnonzero(elev[:, j] >= MIN_ELEV_DEG)))
            peak = max(peak, len(above)); daily_seen.update(above); hourly_seen[dt.hour].update(above)
            hourly_star[dt.hour].update(idx for idx in above if cats[idx] == "Starlink")
            points = []
            for idx in above:
                key = str(ids[idx])
                if key not in catalog:
                    catalog[key] = [names[idx], cats[idx], ids[idx], operators[idx][0], operators[idx][1], epochs[idx]]
                el, al, sp = float(elev[idx, j]), float(alt[idx, j]), float(speed[idx, j])
                points.append([ids[idx], round(float(az[idx, j]) * 10), round(el * 10), round(al), round(sp * 100)])
                if idx not in active:
                    active[idx] = [minute, el, minute, al, sp]
                elif el > active[idx][1]:
                    active[idx][1:] = [el, minute, al, sp]
            ended = [idx for idx in active if idx not in above]
            for idx in ended:
                finish(idx, active.pop(idx), minute)
            frames.append([dt.strftime("%H:%M"), points])
    for idx, rec in list(active.items()):
        finish(idx, rec, 1440)

    type_counts = Counter(cats[idx] for idx in daily_seen)
    stats = {
        "date": day.strftime("%Y-%m-%d"), "unique_satellites": len(daily_seen), "pass_count": len(passes),
        "starlink_unique": sum(cats[idx] == "Starlink" for idx in daily_seen),
        "starlink_passes": sum(catalog.get(str(p[0]), [None, None])[1] == "Starlink" for p in passes),
        "hourly_unique": [len(x) for x in hourly_seen], "hourly_starlink": [len(x) for x in hourly_star],
        "type_unique": dict(type_counts), "peak_simultaneous": peak,
    }
    passes.sort(key=lambda p: (-p[3], p[1]))
    return stats, catalog, frames, passes


def update_history(stats):
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    summary = {k: stats[k] for k in ("date", "unique_satellites", "pass_count", "starlink_unique", "starlink_passes", "peak_simultaneous")}
    history = [x for x in history if x.get("date") != stats["date"]] + [summary]
    history = sorted(history, key=lambda x: x["date"])[-90:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Shanghai local date YYYY-MM-DD; default today")
    parser.add_argument("--input", type=Path, help="Local .csv OMM or legacy .tle seed")
    args = parser.parse_args()
    day_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(LOCAL_TZ).date()
    day = datetime(day_date.year, day_date.month, day_date.day, tzinfo=LOCAL_TZ)
    retrieved = datetime.now(LOCAL_TZ)
    ARTIFACTS.mkdir(parents=True, exist_ok=True); PUBLIC_DATA.mkdir(parents=True, exist_ok=True)

    if args.input:
        if args.input.suffix.lower() == ".csv":
            body = args.input.read_bytes(); names, ids, sats, epochs = load_omm_bytes(body); source = "Local OMM seed"
            shutil.copy2(args.input, ARTIFACTS / f"celestrak-active-{day_date}.csv")
        else:
            names, ids, sats, epochs = load_tle(args.input); source = "Local legacy TLE seed (first local build only)"
    else:
        body, source = fetch_omm(); names, ids, sats, epochs = load_omm_bytes(body)
        (ARTIFACTS / f"celestrak-active-{day_date}.csv").write_bytes(body)
    if len(sats) < 10_000:
        raise RuntimeError(f"Only {len(sats)} valid active objects; refusing to publish")

    cats = [classify(n) for n in names]
    operators = [infer_operator(n, c) for n, c in zip(names, cats)]
    stats, catalog, frames, passes = analyze(day, names, ids, sats, cats, operators, epochs)
    history = update_history(stats)
    epoch_values = [e for e in epochs if e != "legacy TLE"]
    payload = {
        "schema_version": 2, "date": str(day_date), "step_min": STEP_MIN, "minimum_elevation_deg": MIN_ELEV_DEG,
        "location": {"name": "上海市中心", "lat": LAT_DEG, "lon": LON_DEG, "timezone": "Asia/Shanghai"},
        "generated_at": retrieved.isoformat(timespec="seconds"), "catalog_source": source, "catalog_source_url": SOURCE_URL,
        "source_epoch_range": [min(epoch_values), max(epoch_values)] if epoch_values else ["legacy TLE", "legacy TLE"],
        "method": "SGP4；每5分钟采样；地心惯性坐标近似转换为上海站心方位角/仰角；仰角≥10°；连续采样段计为一次过境。",
        "caveat": "这是基于公开轨道根数的全天预测，不等于肉眼可见；类型和运营方部分依据名称保守推断。",
        "daily": stats, "history": history, "catalog": catalog, "frames": frames, "passes": passes,
        "starlink_user_rate_note": "Starlink 官方用户端典型下载速率 45–280 Mbps；这不是单颗卫星容量。",
    }
    out = PUBLIC_DATA / "latest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"objects": len(sats), "unique": stats["unique_satellites"], "passes": stats["pass_count"], "frames": len(frames), "output_mb": round(out.stat().st_size / 1024**2, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
