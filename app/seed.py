"""Jednorázové naplnění databáze z proměnné prostředí SEED_JSON_B64 (JSON v base64).

Hodnota je JSON v base64, případně nejdřív zkomprimovaný zlibem (kratší, a poškození se pozná).
Slouží k dodání dat, která nemají být v gitu (rozbory, vyšetření). Import je idempotentní: co už v databázi je, se
přeskočí, takže proměnná může zůstat i po restartu. Po úspěšném importu ji ale smaž (data zůstanou v databázi).
Do logu se píšou jen počty, nikdy obsah.
"""
import base64
import binascii
import json
import logging
import sqlite3
import zlib

from pydantic import ValidationError

from .labs import save_labs
from .records import EventIn, LabIn

log = logging.getLogger("uvicorn.error")


def apply_seed(db: sqlite3.Connection, b64: str) -> dict:
    if not b64.strip():
        return {}
    try:
        raw = base64.b64decode(b64, validate=True)
        if raw[:1] == b"x":                      # zlib (kontrolní součet odhalí poškozený přepis)
            raw = zlib.decompress(raw)
        data = json.loads(raw)
        labs = [LabIn(**i) for i in data.get("labs", [])]
        events = [EventIn(**i) for i in data.get("events", [])]
    except (binascii.Error, ValueError, TypeError, ValidationError, zlib.error) as e:
        log.error("seed: neplatná data (%s), nic se neimportovalo", type(e).__name__)
        return {"error": type(e).__name__}
    labs_in, labs_skip = save_labs(db, labs)
    ev_in = ev_skip = 0
    for ev in events:
        dup = db.execute("SELECT 1 FROM health_event WHERE day=? AND kind=? AND title=?",
                         (ev.day.isoformat(), ev.kind, ev.title)).fetchone()
        if dup:
            ev_skip += 1
            continue
        db.execute("INSERT INTO health_event (day, kind, title, note) VALUES (?,?,?,?)",
                   (ev.day.isoformat(), ev.kind, ev.title, ev.note))
        ev_in += 1
    db.commit()
    result = {"labs_inserted": labs_in, "labs_skipped": labs_skip, "events_inserted": ev_in, "events_skipped": ev_skip}
    log.info("seed: %s", result)
    return result
