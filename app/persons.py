"""Osoby, jejichž zdraví se sleduje (např. rodina). Osoba 1 je vlastník aplikace."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .records import get_db

router = APIRouter(prefix="/persons", tags=["persons"])


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "owner": r["id"] == 1}


@router.get("")
def list_persons(db: sqlite3.Connection = Depends(get_db)):
    return [_row(r) for r in db.execute("SELECT id, name FROM person ORDER BY id")]


@router.post("", status_code=201)
def add_person(item: PersonIn, db: sqlite3.Connection = Depends(get_db)):
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Jméno nesmí být prázdné")
    try:
        cur = db.execute("INSERT INTO person (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Osoba s tímto jménem už existuje")
    db.commit()
    return {"id": cur.lastrowid, "name": name, "owner": False}


@router.put("/{person_id}")
def rename_person(person_id: int, item: PersonIn, db: sqlite3.Connection = Depends(get_db)):
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Jméno nesmí být prázdné")
    if db.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Osoba neexistuje")
    try:
        db.execute("UPDATE person SET name = ? WHERE id = ?", (name, person_id))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Osoba s tímto jménem už existuje")
    db.commit()
    return {"id": person_id, "name": name, "owner": person_id == 1}
