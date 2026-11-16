"""Jednorázové naplnění databáze z proměnné prostředí SEED_JSON_B64 (JSON v base64).

Hodnota je JSON v base64 ({"persons", "labs", "events", "genetics"}), případně nejdřív zkomprimovaný zlibem (kratší, a poškození se pozná).
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

from .genetics import GeneticIn, save_genetics
from .labs import save_labs
from .records import EventIn, LabIn

log = logging.getLogger("uvicorn.error")


def _person_ids(db: sqlite3.Connection, persons: list[dict]) -> dict[str, int]:
    """Založí osoby ze seedu (vlastník = přejmenování osoby 1) a vrátí slovník jméno -> id."""
    for p in persons:
        name = str(p["name"]).strip()
        if not name:
            continue
        if p.get("owner"):
            db.execute("UPDATE person SET name = ? WHERE id = 1 AND NOT EXISTS (SELECT 1 FROM person WHERE name = ? AND id != 1)", (name, name))
        else:
            db.execute("INSERT OR IGNORE INTO person (name) VALUES (?)", (name,))
    return {r["name"].casefold(): r["id"] for r in db.execute("SELECT id, name FROM person")}


def apply_seed(db: sqlite3.Connection, b64: str) -> dict:
    if not b64.strip():
        return {}
    try:
        raw = base64.b64decode(b64, validate=True)
        if raw[:1] == b"x":                      # zlib (kontrolní součet odhalí poškozený přepis)
            raw = zlib.decompress(raw)
        data = json.loads(raw)
        ids = _person_ids(db, data.get("persons", []))

        def with_person(items):                  # položka může nést „person“ (jméno) místo person_id
            out = []
            for it in items:
                it = dict(it)
                name = it.pop("person", None)
                if name is not None:
                    if str(name).casefold() not in ids:
                        raise ValueError("neznámá osoba")
                    it["person_id"] = ids[str(name).casefold()]
                out.append(it)
            return out

        labs = [LabIn(**i) for i in with_person(data.get("labs", []))]
        events = [EventIn(**i) for i in with_person(data.get("events", []))]
        genetics = [GeneticIn(**i) for i in with_person(data.get("genetics", []))]
    except (binascii.Error, ValueError, TypeError, KeyError, ValidationError, zlib.error) as e:
        log.error("seed: neplatná data (%s), nic se neimportovalo", type(e).__name__)
        db.rollback()
        return {"error": type(e).__name__}
    labs_in, labs_skip = save_labs(db, labs)
    ev_in = ev_skip = 0
    for ev in events:
        dup = db.execute("SELECT 1 FROM health_event WHERE person_id=? AND day=? AND kind=? AND title=?",
                         (ev.person_id, ev.day.isoformat(), ev.kind, ev.title)).fetchone()
        if dup:
            ev_skip += 1
            continue
        db.execute("INSERT INTO health_event (person_id, day, kind, title, note) VALUES (?,?,?,?,?)",
                   (ev.person_id, ev.day.isoformat(), ev.kind, ev.title, ev.note))
        ev_in += 1
    db.commit()
    gen_in, gen_upd = save_genetics(db, genetics)
    result = {"labs_inserted": labs_in, "labs_skipped": labs_skip, "events_inserted": ev_in, "events_skipped": ev_skip,
              "genetics_inserted": gen_in, "genetics_updated": gen_upd}
    log.info("seed: %s", result)
    return result
