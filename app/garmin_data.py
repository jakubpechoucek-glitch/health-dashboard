"""Zpracování dat z Garminu, která nejsou jen pár vybraných polí: plošné rozbalení všech číselných hodnot,
aktivity, váha a složení těla, odhad fitness věku. Čisté funkce a práce s databází, bez HTTP.
"""
import json
import re
import sqlite3
from typing import Any

# Pole, která jsou identifikátory, časové značky nebo technická metadata, ne měření.
_SKIP = re.compile(r"(?:^|\.)(?:id|uuid|userProfilePk|userProfilePK|deviceId|userId|ownerId|sleepVersion|featureVersion|"
                   r"algorithmVersion|sportTypeId|parentId|activityTypeId)$|timestamp|timeStamp|epoch|Gmt$|Local$|Offset$", re.I)

# Pole, která už se ukládají pod srozumitelným názvem (steps, resting_hr…), aby se neduplikovala.
CURATED = {
    "daily": {"movement.steps.value", "heartRate.restingValue", "heartRate.minValue", "heartRate.maxValue", "stress.avgLevel",
              "bodyBattery.maxValue", "bodyBattery.minValue", "calories.burnedActive", "calories.burnedTotal",
              "respiration.avgValue", "pulseOx.avgValue", "intensityMinutes.moderate", "intensityMinutes.vigorous"},
    "sleep": {"sleepTimeSeconds", "deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds", "awakeSleepSeconds",
              "sleepScores.overall.value", "avgHeartRate"},
    "hrv": {"lastNightAvg", "weeklyAvg"},
}


def unit_for(path: str) -> str:
    p = path.lower()
    if p.endswith("seconds") or p.endswith("insec"):
        return "s"
    if p.endswith("inmillis"):
        return "ms"
    if p.endswith("inmeters") or p.endswith("meters"):
        return "m"
    if p.endswith("inml") or p.endswith("inFractionalMl".lower()):
        return "ml"
    if p.endswith("proportion"):
        return "ratio"
    if "kcal" in p or "calories" in p or "kilocalories" in p:
        return "kcal"
    return ""


def flatten(item: dict, ns: str) -> list[tuple]:
    """Všechny číselné hodnoty objektu jako řádky (source, metric, day, value, unit); metric = `ns.cesta.k.poli`."""
    day = item.get("calendarDate")
    if not day:
        return []
    curated, rows = CURATED.get(ns, set()), []

    def walk(o: Any, path: list[str], depth: int) -> None:
        if depth > 6:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + [k], depth + 1)
        elif isinstance(o, bool) or o is None:
            return
        elif isinstance(o, (int, float)):
            p = ".".join(path)
            if p not in curated and not _SKIP.search(p):
                rows.append(("garmin", f"{ns}.{p}", day, float(o), unit_for(p)))

    walk({k: v for k, v in item.items() if k != "calendarDate"}, [], 0)
    return rows


# ---------- aktivity ----------

_DROP = re.compile(r"^(owner|userRoles|privacy|startLatitude|startLongitude|endLatitude|endLongitude|hasPolyline|"
                   r"hasImages|hasVideo|videoUrl|likedByUser|commentedByUser|numberOfActivity|requestorRelationship)", re.I)


def activity_columns(it: dict) -> dict | None:
    aid = it.get("activityId")
    start = str(it.get("startTimeLocal") or "")
    if aid is None or not re.match(r"\d{4}-\d{2}-\d{2}", start):
        return None
    # GPS poloha a údaje o vlastníkovi se neukládají, zbytek ano (podrobnosti o aktivitě jsou v UI)
    raw = {k: v for k, v in it.items() if not _DROP.match(k)}
    return {
        "activity_id": int(aid), "day": start[:10], "start_local": start,
        "type_key": ((it.get("activityType") or {}).get("typeKey") or ""), "name": it.get("activityName") or "",
        "duration_s": it.get("duration"), "distance_m": it.get("distance"), "calories": it.get("calories"),
        "avg_hr": it.get("averageHR"), "max_hr": it.get("maxHR"), "elevation_gain": it.get("elevationGain"),
        "avg_speed": it.get("averageSpeed"), "steps": it.get("steps"),
        "aerobic_te": it.get("aerobicTrainingEffect"), "anaerobic_te": it.get("anaerobicTrainingEffect"),
        "vo2max": it.get("vO2MaxValue"), "raw": json.dumps(raw, ensure_ascii=False),
    }


def save_activities(db: sqlite3.Connection, items: list[dict], person_id: int = 1) -> list[tuple]:
    """Uloží aktivity (upsert podle id) a vrátí denní souhrny jako řádky metrik (počet, doba, vzdálenost, VO2max…)."""
    days = set()
    for it in items:
        c = activity_columns(it)
        if not c:
            continue
        c["person_id"] = person_id
        cols = ", ".join(c)
        db.execute(f"INSERT INTO activity ({cols}) VALUES ({', '.join('?' * len(c))}) "
                   f"ON CONFLICT(activity_id) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in c if k not in ("activity_id", "person_id")),
                   tuple(c.values()))
        days.add(c["day"])
    rows = []
    for day in sorted(days):
        r = db.execute("SELECT COUNT(*) n, SUM(duration_s) dur, SUM(distance_m) dist, SUM(calories) kcal, MAX(vo2max) vo2, MAX(aerobic_te) te "
                       "FROM activity WHERE day = ? AND person_id = ?", (day, person_id)).fetchone()
        for metric, unit, v in (("activity.count", "count", r["n"]), ("activity.duration_s", "s", r["dur"]), ("activity.distance_m", "m", r["dist"]),
                                ("activity.calories", "kcal", r["kcal"]), ("activity.vo2max", "ml/kg/min", r["vo2"]), ("activity.aerobic_te_max", "", r["te"])):
            if isinstance(v, (int, float)):
                rows.append(("garmin", metric, day, float(v), unit))
    return rows


# ---------- váha a složení těla, fitness věk ----------

def _grams(v: float, limit: float) -> float:
    return v / 1000 if v > limit else v        # Garmin uvádí hmotnosti v gramech


def normalize_weight(item: dict) -> list[tuple]:
    day = item.get("summaryDate")
    lw = item.get("latestWeight") or {}
    if not day or not isinstance(lw, dict):
        return []
    out = []
    for metric, unit, key, limit in (("body.weight_kg", "kg", "weight", 400), ("body.bone_mass_kg", "kg", "boneMass", 40),
                                      ("body.muscle_mass_kg", "kg", "muscleMass", 150), ("body.bmi", "", "bmi", None),
                                      ("body.fat_pct", "%", "bodyFat", None), ("body.water_pct", "%", "bodyWater", None),
                                      ("body.visceral_fat", "", "visceralFat", None), ("body.metabolic_age", "years", "metabolicAge", None),
                                      ("body.physique_rating", "", "physiqueRating", None)):
        v = lw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(("garmin", metric, day, _grams(float(v), limit) if limit else float(v), unit))
    return out


def normalize_fitness(item: dict, day: str) -> list[tuple]:
    if not isinstance(item, dict) or item.get("invalidReason"):
        return []
    return [("garmin", metric, day, float(item[key]), "years")
            for metric, key in (("fitness.age", "fitnessAge"), ("fitness.chronological_age", "chronologicalAge"),
                                ("fitness.achievable_age", "achievableFitnessAge"))
            if isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool)]
