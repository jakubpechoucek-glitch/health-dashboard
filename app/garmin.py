"""Stahování dat z Garmin Connect Personal API (pull).

Postup podle oficiální dokumentace (developer.garmin.com/gc-developer-program/personal):
1. Osobní API klíč (GARMIN_API_KEY) se pošle na token endpoint v hlavičce
   `Authorization: ApiKey <klíč>` a vrátí OAuth2 access token.
2. Access token platí 24 h, refresh token neexistuje, takže si ho server vyžádá znovu.
3. Data se čtou z https://personal.connectapi.garmin.com (jen GET).

Surové odpovědi se ukládají do `garmin_raw`, takže jde mapování polí kdykoli opravit a
data přepočítat bez nového stahování.
"""
import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .config import Settings, garmin_key, garmin_key_var, get_settings
from .records import require_person
from .garmin_data import flatten, normalize_fitness, normalize_weight, save_activities

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = f"{BASE}/personalapitoken-service/personal-api/personal-api-key/token"
TOKEN_TTL = 23 * 3600  # token platí 24 h, obnovíme s rezervou

router = APIRouter(prefix="/garmin", tags=["garmin"])
_token_cache: dict[str, dict[str, Any]] = {}      # klíč = otisk API klíče, aby víc osob nesdílelo token


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
        slot = hashlib.sha256(self.api_key.encode()).hexdigest()
        cached = _token_cache.get(slot)
        if not force and cached and cached["exp"] > time.monotonic():
            return cached["token"]
        r = self.http.post(TOKEN_URL, headers={"Authorization": f"ApiKey {self.api_key}"})
        if r.status_code != 200:
            raise GarminError(f"Získání tokenu selhalo (HTTP {r.status_code})")
        token = _parse_token(r)
        if not token:
            raise GarminError("Odpověď s tokenem má neočekávaný tvar")
        _token_cache[slot] = {"token": token, "exp": time.monotonic() + TOKEN_TTL}
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
    return flatten(item, "daily") + _rows(item.get("calendarDate"), [
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


def normalize_daily_flat(item: dict) -> list[tuple]:
    """Záložní endpoint /usersummary/daily vrací plochý objekt (jiná pole než v2)."""
    return _rows(item.get("calendarDate"), [
        ("steps", "count", item.get("totalSteps")),
        ("resting_hr", "bpm", item.get("restingHeartRate")),
        ("min_hr", "bpm", item.get("minHeartRate")),
        ("max_hr", "bpm", item.get("maxHeartRate")),
        ("avg_stress", "score", item.get("averageStressLevel")),
        ("body_battery_max", "score", item.get("bodyBatteryHighestValue")),
        ("body_battery_min", "score", item.get("bodyBatteryLowestValue")),
        ("active_kcal", "kcal", item.get("activeKilocalories")),
        ("total_kcal", "kcal", item.get("totalKilocalories")),
        ("respiration_avg", "brpm", item.get("avgWakingRespirationValue")),
        ("spo2_avg", "%", item.get("averageSpo2")),
        ("intensity_moderate", "min", item.get("moderateIntensityMinutes")),
        ("intensity_vigorous", "min", item.get("vigorousIntensityMinutes")),
    ])


def normalize_sleep(item: dict) -> list[tuple]:
    return flatten(item, "sleep") + _rows(item.get("calendarDate"), [
        ("sleep_seconds", "s", item.get("sleepTimeSeconds")),
        ("sleep_deep_seconds", "s", item.get("deepSleepSeconds")),
        ("sleep_light_seconds", "s", item.get("lightSleepSeconds")),
        ("sleep_rem_seconds", "s", item.get("remSleepSeconds")),
        ("sleep_awake_seconds", "s", item.get("awakeSleepSeconds")),
        ("sleep_score", "score", _dig(item, "sleepScores", "overall", "value")),
        ("sleep_avg_hr", "bpm", item.get("avgHeartRate")),
    ])


def normalize_hrv(item: dict) -> list[tuple]:
    return flatten(item, "hrv") + _rows(item.get("calendarDate"), [
        ("hrv_last_night_avg", "ms", item.get("lastNightAvg")),
        ("hrv_weekly_avg", "ms", item.get("weeklyAvg")),
    ])


def _chunks(start: date, end: date, size: int) -> Iterator[tuple[date, date]]:
    """Úseky po `size` dnech, od nejnovějšího (nejčerstvější data jsou nejužitečnější)."""
    cur = end
    while cur >= start:
        first = max(cur - timedelta(days=size - 1), start)
        yield first, cur
        cur = first - timedelta(days=1)


# ---------- synchronizace ----------

def _upsert_metric(db: sqlite3.Connection, person_id: int, row: tuple) -> None:
    db.execute(
        "INSERT INTO metric_daily (person_id, source, metric, day, value, unit) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(person_id, source, metric, day) DO UPDATE SET value=excluded.value, unit=excluded.unit",
        (person_id, *row),
    )


def _save(db: sqlite3.Connection, summary_type: str, payload: Any, rows: list[tuple], person_id: int = 1) -> None:
    db.execute("INSERT INTO garmin_raw (person_id, summary_type, payload) VALUES (?, ?, ?)",
               (person_id, summary_type, json.dumps(payload)))
    for row in rows:
        _upsert_metric(db, person_id, row)
    db.commit()


def fetch_activities(client: "GarminClient", a: str, b: str) -> list[dict]:
    """Všechny aktivity v období (stránkování po 100, nejvýš 2000)."""
    out, offset = [], 0
    while True:
        page = client.get("/activitylist-service/activities/search/activities",
                          {"startDate": a, "endDate": b, "start": offset, "limit": 100})
        if not isinstance(page, list) or not page:
            return out
        out += page
        if len(page) < 100 or len(out) >= 2000:
            return out
        offset += 100


FALLBACK_DAYS = 60       # nejvýš tolik dnů se za jedno spuštění dotáhne záložním endpointem po jednom dni
DAILY_CHUNK = 28         # v2/dailysummaries při dlouhém období vrací HTTP 500, proto kratší úseky


def sync(db: sqlite3.Connection, client: GarminClient, start: date, end: date, person_id: int = 1) -> dict:
    stored, errors, by_type = 0, [], {}
    fallback_budget = FALLBACK_DAYS

    def info_for(name):
        return by_type.setdefault(name, {"days_returned": 0, "metrics_stored": 0, "metrics": {}})

    def account(name, items_count, rows):
        nonlocal stored
        info = info_for(name)
        stored += len(rows)
        info["days_returned"] += items_count
        info["metrics_stored"] += len(rows)
        for r in rows:  # kolik dní má která metrika (bez hodnot, jen počty)
            info["metrics"][r[1]] = info["metrics"].get(r[1], 0) + 1

    def daily_fallback(a: date, b: date) -> str | None:
        """Po jednom dni přes /usersummary/daily. Vrací text chyby, nebo None."""
        nonlocal fallback_budget
        fails, day = 0, b
        while day >= a:
            if fallback_budget <= 0:
                return f"záložní stahování po dnech vyčerpalo limit {FALLBACK_DAYS} dnů, zkus kratší období"
            fallback_budget -= 1
            try:
                item = client.get("/usersummary-service/usersummary/daily", {"calendarDate": day.isoformat()})
            except (GarminError, httpx.HTTPError, ValueError) as e:
                fails += 1
                if fails >= 3:
                    return f"záložní endpoint selhal 3× po sobě: {e}"
            else:
                fails = 0
                if isinstance(item, dict):
                    rows = normalize_daily_flat(item)
                    _save(db, "usersummary_daily", item, rows, person_id)
                    account("usersummary_daily", 1, rows)
            day -= timedelta(days=1)
        return None

    def run(name, chunk_days, fetch, extract, normalize, fallback=None, optional=False, post=None, raw=None):
        """optional: chyba zdroje (např. váha, když nemáš váhu) se jen poznamená a synchronizace se kvůli ní nepovažuje za neúspěšnou.
        post(items) uloží další věci (aktivity) a vrátí řádky metrik; raw(payload) zmenší, co se uloží do garmin_raw."""
        failed = 0
        for a, b in _chunks(start, end, chunk_days):
            try:
                payload = fetch(a.isoformat(), b.isoformat())
                items = extract(payload) if payload else []
                rows = [r for it in items for r in normalize(it)]
                if post:
                    rows += post(items)
                _save(db, name, raw(payload) if raw else payload, rows, person_id)
                account(name, len(items), rows)
                failed = 0
            except (GarminError, httpx.HTTPError, ValueError) as e:
                failed += 1
                info_for(name)
                if optional:
                    info_for(name)["skipped"] = str(e)[:200]
                    break
                msg = f"{name} {a}..{b}: {e}"
                if fallback:
                    problem = fallback(a, b)
                    msg += " → záloha po dnech " + ("selhala: " + problem if problem else "proběhla")
                errors.append(msg)
                if failed >= 3 or (fallback and fallback_budget <= 0):
                    break

    run("dailysummaries", DAILY_CHUNK,
        lambda a, b: client.get("/usersummary-service/v2/dailysummaries", {"startDate": a, "endDate": b}),
        lambda p: p.get("data", []), normalize_daily,
        fallback=daily_fallback)
    run("sleep", 31,
        lambda a, b: client.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": a, "endDate": b}),
        lambda p: p if isinstance(p, list) else [], normalize_sleep)
    run("hrv", 28,
        lambda a, b: client.get(f"/hrv-service/hrv/daily/{a}/{b}"),
        lambda p: p.get("hrvSummaries", []), normalize_hrv)
    run("activities", 31, lambda a, b: fetch_activities(client, a, b), lambda p: p, lambda it: [],
        post=lambda items: save_activities(db, items, person_id), raw=lambda p: {"count": len(p)})          # celé aktivity jsou v tabulce activity
    run("weight", 90, lambda a, b: client.get(f"/weight-service/weight/range/{a}/{b}"),
        lambda p: p.get("dailyWeightSummaries", []), normalize_weight, optional=True)
    try:                                                                                          # fitness věk: jedna hodnota k poslednímu dni
        fa = client.get(f"/fitnessage-service/fitnessage/v2/{end.isoformat()}")
        fitness = normalize_fitness(fa[0] if isinstance(fa, list) and fa else fa, end.isoformat())
        _save(db, "fitness", fa, fitness, person_id)
        account("fitness", 1 if fitness else 0, fitness)
    except (GarminError, httpx.HTTPError, ValueError) as e:
        info_for("fitness")["skipped"] = str(e)[:200]
    return {"from": start.isoformat(), "to": end.isoformat(), "metrics_stored": stored,
            "by_type": by_type, "errors": errors}


SYNC_LOCK = threading.Lock()      # ruční a automatická synchronizace se nesmí prolínat (sdílený token i databáze)
LOCK_TIMEOUT = 90.0               # jak dlouho ruční synchronizace čeká na dokončení běžící


class SyncBusy(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_run(db: sqlite3.Connection, started: str, source: str, ok: bool, days: int, stored: int, error: str, person_id: int = 1) -> None:
    db.execute("INSERT INTO sync_run (person_id, started_at, finished_at, source, ok, days, metrics_stored, error) VALUES (?,?,?,?,?,?,?,?)",
               (person_id, started, now_iso(), source, int(ok), days, stored, error[:500]))
    db.execute("DELETE FROM sync_run WHERE id NOT IN (SELECT id FROM sync_run ORDER BY id DESC LIMIT 600)")
    db.commit()


def last_run_time(db: sqlite3.Connection, source: str | None = None, ok_only: bool = False, failed_only: bool = False,
                  person_id: int = 1) -> datetime | None:
    sql, args = "SELECT MAX(finished_at) FROM sync_run WHERE person_id = ?", [person_id]
    if source:
        sql += " AND source = ?"; args.append(source)
    if ok_only:
        sql += " AND ok = 1"
    if failed_only:
        sql += " AND ok = 0"
    v = db.execute(sql, args).fetchone()[0]
    return datetime.fromisoformat(v) if v else None


def run_sync(db: sqlite3.Connection, client: GarminClient, start: date, end: date, source: str, person_id: int = 1) -> dict:
    """Synchronizace pod zámkem, s uloženým záznamem o výsledku (bez obsahu dat, jen počty a text chyby)."""
    if not SYNC_LOCK.acquire(timeout=LOCK_TIMEOUT):
        raise SyncBusy()
    started = now_iso()
    try:
        result = sync(db, client, start, end, person_id)
        record_run(db, started, source, not result["errors"], (end - start).days + 1, result["metrics_stored"], "; ".join(result["errors"]), person_id)
        return result
    except Exception as e:
        record_run(db, started, source, False, (end - start).days + 1, 0, f"{type(e).__name__}: {e}", person_id)
        raise
    finally:
        SYNC_LOCK.release()


REPROCESS_VERSION = "flatten_v1"


def reprocess_raw(db: sqlite3.Connection) -> int:
    """Znovu zpracuje už stažená surová data (garmin_raw) novými pravidly, bez nového stahování z Garminu.
    Pozdější stažení přepíše dřívější, protože se jde od nejstaršího."""
    n, last = 0, 0
    sources = {"dailysummaries": (lambda p: p.get("data", []) if isinstance(p, dict) else [], normalize_daily),
               "sleep": (lambda p: p if isinstance(p, list) else [], normalize_sleep),
               "hrv": (lambda p: p.get("hrvSummaries", []) if isinstance(p, dict) else [], normalize_hrv)}
    while True:
        rows = db.execute("SELECT id, person_id, summary_type, payload FROM garmin_raw WHERE id > ? ORDER BY id LIMIT 40", (last,)).fetchall()
        if not rows:
            break
        for r in rows:
            last = r["id"]
            if r["summary_type"] not in sources:
                continue
            try:
                payload = json.loads(r["payload"])
            except ValueError:
                continue
            extract, normalize = sources[r["summary_type"]]
            for item in extract(payload):
                for row in normalize(item):
                    _upsert_metric(db, r["person_id"], row)
                    n += 1
        db.commit()
    return n


def reprocess_if_needed(db: sqlite3.Connection) -> int | None:
    if db.execute("SELECT 1 FROM meta WHERE key = ?", (REPROCESS_VERSION,)).fetchone():
        return None
    n = reprocess_raw(db)
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (REPROCESS_VERSION, now_iso()))
    db.commit()
    logging.getLogger("uvicorn.error").info("reprocess: z uložených surových dat přepočteno %s hodnot", n)
    return n


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client


@router.post("/sync")
def sync_endpoint(
    person_id: int = Query(1, description="Osoba, jejíž Garmin se stahuje (každá má vlastní API klíč)"),
    days: int = Query(7, ge=1, le=366, description="Kolik dní stáhnout (počítáno zpět od `end`)"),
    end: date | None = Query(None, description="Poslední stahovaný den, výchozí je dnes"),
    db: sqlite3.Connection = Depends(get_db),
    http: httpx.Client = Depends(get_http),
    settings: Settings = Depends(get_settings),
):
    """Stáhne úsek [end - days + 1, end]. Historii stahuje UI po oknech (end se posouvá do minulosti),
    aby jediný požadavek netrval dlouho a šel kdykoli přerušit a navázat."""
    require_person(db, person_id)
    key = garmin_key(settings, person_id)
    if not key:
        raise HTTPException(status_code=503, detail=f"{garmin_key_var(person_id)} není nastaveno")
    last = end or date.today()
    if last > date.today():
        raise HTTPException(status_code=400, detail="end nesmí být v budoucnosti")
    try:
        return run_sync(db, GarminClient(http, key), last - timedelta(days=days - 1), last, "manual", person_id)
    except SyncBusy:
        raise HTTPException(status_code=409, detail="Právě běží jiná synchronizace, zkus to za chvíli")


def _run_json(db: sqlite3.Connection, where: str, person_id: int) -> dict | None:
    r = db.execute(f"SELECT started_at, finished_at, source, ok, days, metrics_stored, error FROM sync_run WHERE person_id = ? AND {where} ORDER BY id DESC LIMIT 1", (person_id,)).fetchone()
    return {**dict(r), "ok": bool(r["ok"])} if r else None


@router.get("/status")
def garmin_status(person_id: int = 1, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Stav Garminu a automatické synchronizace jedné osoby pro UI (včetně názvu proměnné, do které patří její API klíč)."""
    require_person(db, person_id)
    return {
        "key_configured": bool(garmin_key(settings, person_id)),
        "key_variable": garmin_key_var(person_id),
        "auto": {"enabled": settings.auto_sync_hours > 0, "interval_hours": settings.auto_sync_hours, "days": settings.auto_sync_days},
        "last_auto": _run_json(db, "source = 'auto'", person_id),
        "last_success": _run_json(db, "ok = 1", person_id),
    }


_ACT_COLS = ("activity_id, day, start_local, type_key, name, duration_s, distance_m, calories, avg_hr, max_hr, "
             "elevation_gain, avg_speed, steps, aerobic_te, anaerobic_te, vo2max")


@router.get("/activities")
def list_activities(
    person_id: int = 1,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
):
    require_person(db, person_id)
    sql, args = f"SELECT {_ACT_COLS} FROM activity WHERE person_id = ?", [person_id]
    if start:
        sql += " AND day >= ?"; args.append(start.isoformat())
    if end:
        sql += " AND day <= ?"; args.append(end.isoformat())
    return [dict(r) for r in db.execute(sql + " ORDER BY start_local DESC LIMIT ?", (*args, limit))]


@router.get("/activities/{activity_id}")
def activity_detail(activity_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Všechna pole aktivity, jak je poslal Garmin (bez GPS polohy)."""
    r = db.execute("SELECT activity_id, raw FROM activity WHERE activity_id = ?", (activity_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="Aktivita neexistuje")
    return {"activity_id": r["activity_id"], "raw": json.loads(r["raw"])}
