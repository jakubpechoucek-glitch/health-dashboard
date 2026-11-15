"""Drobná nastavení rozhraní uložená na serveru (např. připnuté metriky), aby platila na všech zařízeních."""
import json
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .records import get_db

router = APIRouter(prefix="/prefs", tags=["prefs"])
_KEY = re.compile(r"^[a-z0-9_.-]{1,60}$")


class PrefIn(BaseModel):
    value: Any


@router.get("")
def get_prefs(db: sqlite3.Connection = Depends(get_db)):
    return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT key, value FROM ui_pref")}


@router.put("/{key}")
def put_pref(key: str, item: PrefIn, db: sqlite3.Connection = Depends(get_db)):
    if not _KEY.match(key):
        raise HTTPException(status_code=422, detail="Neplatný klíč")
    value = json.dumps(item.value, ensure_ascii=False)
    if len(value) > 20000:
        raise HTTPException(status_code=413, detail="Hodnota je příliš velká")
    db.execute("INSERT INTO ui_pref (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    db.commit()
    return {"key": key, "value": item.value}
