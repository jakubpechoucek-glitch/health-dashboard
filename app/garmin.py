"""Příjem dat z Garmin Health API (push) a převod na denní metriky.

POZOR: názvy polí níže vycházejí z dokumentace Garmin Health API, kterou je třeba
ověřit proti aktuální verzi v developer portálu (viz docs/garmin.md). Surový
payload se proto vždy uloží do `garmin_raw`, takže jde normalizaci opravit a
přepočítat bez ztráty dat.
"""
import json
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from .config import Settings, get_settings

router = APIRouter(prefix="/garmin", tags=["garmin"])

# (typ souhrnu, pole v payloadu) -> (název metriky, jednotka)
FIELD_MAP = {
    "dailies": {
        "steps": ("steps", "count"),
        "restingHeartRateInBeatsPerMinute": ("resting_hr", "bpm"),
        "averageStressLevel": ("avg_stress", "score"),
        "activeKilocalories": ("active_kcal", "kcal"),
    },
    "sleeps": {
        "durationInSeconds": ("sleep_seconds", "s"),
    },
}


def normalize(summary_type: str, items: list[dict]) -> list[tuple[str, str, str, float, str]]:
    """Vrátí řádky (source, metric, day, value, unit)."""
    rows = []
    fields = FIELD_MAP.get(summary_type, {})
    for item in items:
        day = item.get("calendarDate")
        if not day:
            continue
        for key, (metric, unit) in fields.items():
            value = item.get(key)
            if isinstance(value, (int, float)):
                rows.append(("garmin", metric, day, float(value), unit))
    return rows


def store(db: sqlite3.Connection, payload: dict) -> int:
    saved = 0
    for summary_type, items in payload.items():
        if not isinstance(items, list):
            continue
        db.execute(
            "INSERT INTO garmin_raw (summary_type, payload) VALUES (?, ?)",
            (summary_type, json.dumps(items)),
        )
        for row in normalize(summary_type, items):
            db.execute(
                "INSERT INTO metric_daily (source, metric, day, value, unit) VALUES (?,?,?,?,?) "
                "ON CONFLICT(source, metric, day) DO UPDATE SET value=excluded.value, unit=excluded.unit",
                row,
            )
            saved += 1
    db.commit()
    return saved


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


@router.post("/webhook/{token}")
async def webhook(
    token: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    expected = settings.garmin_webhook_token
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=404)  # nic neprozrazovat
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Očekávám JSON objekt")
    return {"stored": store(db, payload)}
