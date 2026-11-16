"""Genetická vyšetření: přepis výsledků zprávy (oblast, vyšetření, geny, výsledek, doporučení).

Surové soubory z laboratoře (VCF, TXT) ani PDF se sem neukládají, jen text výsledků, jak je laboratoř formulovala.
"""
import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .records import get_db, require_person

router = APIRouter(prefix="/genetics", tags=["genetics"])


class GeneticIn(BaseModel):
    person_id: int = 1
    report_date: date
    lab: str = ""
    kind: Literal["vysetreni", "uvod", "souhrn"] = "vysetreni"
    section: str = ""
    grp: str = ""
    title: str = Field(min_length=1, max_length=300)
    genes: str = ""
    level: Literal["", "zvysene", "mirne", "bez", "info"] = ""
    description: str = ""
    result: str = ""
    recommendation: str = ""
    ord: int = 0


def save_genetics(db: sqlite3.Connection, items: list[GeneticIn]) -> tuple[int, int]:
    """Uloží položky; stejná položka téže zprávy (osoba, datum, laboratoř, druh, oblast, skupina, název) se přepíše novým textem."""
    inserted = updated = 0
    for it in items:
        key = (it.person_id, it.report_date.isoformat(), it.lab, it.kind, it.section, it.grp, it.title)
        exists = db.execute("SELECT 1 FROM genetic_item WHERE person_id=? AND report_date=? AND lab=? AND kind=? AND section=? "
                            "AND grp=? AND title=?", key).fetchone()
        db.execute(
            "INSERT INTO genetic_item (person_id, report_date, lab, kind, section, grp, title, genes, level, description, result, "
            "recommendation, ord) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (person_id, report_date, lab, kind, section, grp, title) DO UPDATE SET genes=excluded.genes, "
            "level=excluded.level, description=excluded.description, result=excluded.result, "
            "recommendation=excluded.recommendation, ord=excluded.ord",
            (*key, it.genes, it.level, it.description, it.result, it.recommendation, it.ord))
        updated += bool(exists)
        inserted += not exists
    db.commit()
    return inserted, updated


@router.get("")
def list_genetics(person_id: int = 1, db: sqlite3.Connection = Depends(get_db)):
    require_person(db, person_id)
    return [dict(r) for r in db.execute(
        "SELECT * FROM genetic_item WHERE person_id = ? ORDER BY report_date, lab, ord, id", (person_id,))]


@router.post("/bulk", status_code=201)
def bulk(payload: dict, db: sqlite3.Connection = Depends(get_db)):
    try:
        items = [GeneticIn(**i) for i in payload.get("items", [])]
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False, include_input=False))
    if not items or len(items) > 500:
        raise HTTPException(status_code=400, detail="Očekávám 1 až 500 položek")
    for pid in {i.person_id for i in items}:
        require_person(db, pid)
    inserted, updated = save_genetics(db, items)
    return {"inserted": inserted, "updated": updated}
