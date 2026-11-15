"""Stahování dat z Garmin Connect Personal API (pull).

Postup podle oficiální dokumentace (developer.garmin.com/gc-developer-program/personal):
1. Osobní API klíč (GARMIN_API_KEY) se pošle na token endpoint v hlavičce
   `Authorization: ApiKey <klíč>` a vrátí OAuth2 access token.
2. Access token platí 24 h, refresh token neexistuje, takže si ho server vyžádá znovu.
3. Data se čtou z https://personal.connectapi.garmin.com (jen GET).

Surové odpovědi se ukládají do `garmin_raw`, takže jde mapování polí kdykoli opravit a
data přepočítat bez nového stahování.
"""
import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .config import Settings, get_settings

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = f"{BASE}/personalapitoken-service/personal-api/personal-api-key/token"
TOKEN_TTL = 23 * 3600  # token platí 24 h, obnovíme s rezervou

router = APIRouter(prefix="/garmin", tags=["garmin"])
_token_cache: dict[str, Any] = {"token": None, "exp": 0.0}


class GarminError(Exception):
    """Chyba komunikace s Garminem. Zpráva nikdy neobsahuje klíč ani tělo odpovědi."""


def _parse_token(r: httpx.Response) -> str | None:
    try:
        data = r.json()
    except ValueError:
        return r.text.strip() or None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("access_token", "accessToken", "token"):
            if isinstance(data.get(key), str):
                return data[key]
    return None


class GarminClient:
    def __init__(self, http: httpx.Client, api_key: str):
        self.http, self.api_key = http, api_key

    def _token(self, force: bool = False) -> str:
        if not force and _token_cache["token"] and _token_cache["exp"] > time.monotonic():
            return _token_cache["token"]
        r = self.http.post(TOKEN_URL, headers={"Authorization": f"ApiKey {self.api_key}"})
        if r.status_code != 200:
            raise GarminError(f"Získání tokenu selhalo (HTTP {r.status_code})")
        token = _parse_token(r)
        if not token:
            raise GarminError("Odpověď s tokenem má neočekávaný tvar")
        _token_cache.update(token=token, exp=time.monotonic() + TOKEN_TTL)
        return token

    def get(self, path: str, params: dict | None = None) -> Any:
        for attempt in (0, 1):
            token = self._token(force=attempt == 1)
            r = self.http.get(BASE + path, params=params, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 401 and attempt == 0:
                continue  # token mohl vypršet nebo být zneplatněn, vyžádáme nový
            break
        if r.status_code == 204:
            return None
        if r.status_code != 200:
            raise GarminError(f"{path}: HTTP {r.status_code}")
        return r.json()


# ---------- normalizace: odpověď -> řádky (source, metric, day, value, unit) ----------

def _dig(obj: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _rows(day: str | None, pairs: list[tuple[str, str, Any]]) -> list[tuple]:
    if not day:
        return []
    return [
        ("garmin", metric, day, float(value), unit)
        for metric, unit, value in pairs
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def normalize_daily(item: dict) -> list[tuple]:
    return _rows(item.get("calendarDate"), [
        ("steps", "count", _dig(item, "movement", "steps", "value")),
        ("resting_hr", "bpm", _dig(item, "heartRate", "restingValue")),
        ("min_hr", "bpm", _dig(item, "heartRate", "minValue")),
        ("max_hr", "bpm", _dig(item, "heartRate", "maxValue")),
        ("avg_stress", "score", _dig(item, "stress", "avgLevel")),
        ("body_battery_max", "score", _dig(item, "bodyBattery", "maxValue")),
        ("body_battery_min", "score", _dig(item, "bodyBattery", "minValue")),
        ("active_kcal", "kcal", _dig(item, "calories", "burnedActive")),
        ("total_kcal", "kcal", _dig(item, "calories", "burnedTotal")),
        ("respiration_avg", "brpm", _dig(item, "respiration", "avgValue")),
        ("spo2_avg", "%", _dig(item, "pulseOx", "avgValue")),
        ("intensity_moderate", "min", _dig(item, "intensityMinutes", "moderate")),
        ("intensity_vigorous", "min", _dig(item, "intensityMinutes", "vigorous")),
    ])


def normalize_sleep(item: dict) -> list[tuple]:
    return _rows(item.get("calendarDate"), [
        ("sleep_seconds", "s", item.get("sleepTimeSeconds")),
        ("sleep_deep_seconds", "s", item.get("deepSleepSeconds")),
        ("sleep_light_seconds", "s", item.get("lightSleepSeconds")),
        ("sleep_rem_seconds", "s", item.get("remSleepSeconds")),
        ("sleep_awake_seconds", "s", item.get("awakeSleepSeconds")),
        ("sleep_score", "score", _dig(item, "sleepScores", "overall", "value")),
        ("sleep_avg_hr", "bpm", item.get("avgHeartRate")),
    ])


def normalize_hrv(item: dict) -> list[tuple]:
    return _rows(item.get("calendarDate"), [
        ("hrv_last_night_avg", "ms", item.get("lastNightAvg")),
        ("hrv_weekly_avg", "ms", item.get("weeklyAvg")),
    ])


def _chunks(start: date, end: date, size: int) -> Iterator[tuple[date, date]]:
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


# ---------- synchronizace ----------

def _save(db: sqlite3.Connection, summary_type: str, payload: Any, rows: list[tuple]) -> None:
    db.execute("INSERT INTO garmin_raw (summary_type, payload) VALUES (?, ?)",
               (summary_type, json.dumps(payload)))
    for row in rows:
        db.execute(
            "INSERT INTO metric_daily (source, metric, day, value, unit) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source, metric, day) DO UPDATE SET value=excluded.value, unit=excluded.unit",
            row,
        )
    db.commit()


def sync(db: sqlite3.Connection, client: GarminClient, start: date, end: date) -> dict:
    stored, errors, by_type = 0, [], {}

    def run(name: str, chunk_days: int, fetch, extract, normalize):
        nonlocal stored
        info = by_type[name] = {"days_returned": 0, "metrics_stored": 0, "metrics": {}}
        for a, b in _chunks(start, end, chunk_days):
            try:
                payload = fetch(a.isoformat(), b.isoformat())
                items = extract(payload) if payload else []
                rows = [r for it in items for r in normalize(it)]
                _save(db, name, payload, rows)
                stored += len(rows)
                info["days_returned"] += len(items)
                info["metrics_stored"] += len(rows)
                for r in rows:  # kolik dní má která metrika (bez hodnot, jen počty)
                    info["metrics"][r[1]] = info["metrics"].get(r[1], 0) + 1
            except (GarminError, httpx.HTTPError, ValueError) as e:
                errors.append(f"{name} {a}..{b}: {e}")
                break  # při chybě v tomto typu nemá smysl zkoušet další úseky

    run("dailysummaries", 365,
        lambda a, b: client.get("/usersummary-service/v2/dailysummaries", {"startDate": a, "endDate": b}),
        lambda p: p.get("data", []), normalize_daily)
    run("sleep", 31,
        lambda a, b: client.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": a, "endDate": b}),
        lambda p: p if isinstance(p, list) else [], normalize_sleep)
    run("hrv", 28,
        lambda a, b: client.get(f"/hrv-service/hrv/daily/{a}/{b}"),
        lambda p: p.get("hrvSummaries", []), normalize_hrv)
    return {"from": start.isoformat(), "to": end.isoformat(), "metrics_stored": stored,
            "by_type": by_type, "errors": errors}


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client


@router.post("/sync")
def sync_endpoint(
    days: int = Query(7, ge=1, le=366, description="Kolik posledních dní stáhnout"),
    db: sqlite3.Connection = Depends(get_db),
    http: httpx.Client = Depends(get_http),
    settings: Settings = Depends(get_settings),
):
    if not settings.garmin_api_key:
        raise HTTPException(status_code=503, detail="GARMIN_API_KEY není nastaveno")
    end = date.today()
    return sync(db, GarminClient(http, settings.garmin_api_key), end - timedelta(days=days - 1), end)
