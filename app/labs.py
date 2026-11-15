"""Import krevních rozborů z PDF (čtení textu je v labparse.py) a ukládání výsledků.

PDF se nikdy neukládá. Z textu se čtou jen řádky s výsledky, datum odběru a název laboratoře; jméno pacienta,
číslo pojištěnce ani další identifikátory se nezpracovávají ani neukládají.
"""
import os
import sqlite3
import subprocess
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .labparse import MOC, LabParseError, parse_lab_text  # noqa: F401  (re-export pro testy a ostatní moduly)
from .records import LabIn, get_db, require_person

router = APIRouter(prefix="/labs", tags=["labs"])
MAX_PDF_BYTES = 5 * 1024 * 1024


def extract_text(pdf: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pdf)
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LabParseError("PDF se nepodařilo přečíst") from e
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if out.returncode != 0:
        raise LabParseError("PDF se nepodařilo přečíst (poškozený soubor?)")
    return out.stdout.decode("utf-8", errors="replace")


@router.post("/import/preview")
async def preview(request: Request):
    """Přijme PDF v těle požadavku (Content-Type: application/pdf), nic neukládá."""
    data = await request.body()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Očekávám PDF soubor")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="Soubor je příliš velký (limit 5 MB)")
    try:
        parsed = parse_lab_text(extract_text(data))
    except LabParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not parsed["results"]:
        raise HTTPException(status_code=422, detail="V dokumentu jsem nenašel žádné výsledky")
    return parsed


def save_labs(db: sqlite3.Connection, items: list[LabIn]) -> tuple[int, int]:
    """Uloží výsledky, již uložené (stejná osoba, den, analyt a hodnota) přeskočí. Vrací (uloženo, přeskočeno)."""
    inserted = skipped = 0
    for it in items:
        dup = db.execute(
            "SELECT 1 FROM lab_result WHERE person_id=? AND taken_on=? AND analyte=? AND value IS ? AND value_text=?",
            (it.person_id, it.taken_on.isoformat(), it.analyte, it.value, it.value_text),
        ).fetchone()
        if dup:
            skipped += 1
            continue
        db.execute(
            "INSERT INTO lab_result (person_id, taken_on, lab, analyte, value, value_text, unit, ref_low, ref_high, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (it.person_id, it.taken_on.isoformat(), it.lab, it.analyte, it.value, it.value_text, it.unit,
             it.ref_low, it.ref_high, it.note),
        )
        inserted += 1
    db.commit()
    return inserted, skipped


@router.post("/bulk", status_code=201)
def bulk(payload: dict, db: sqlite3.Connection = Depends(get_db)):
    """Uloží potvrzené výsledky (každá položka nese `person_id`). Již uložené přeskočí."""
    items = [LabIn(**i) for i in payload.get("items", [])]
    if not items or len(items) > 500:
        raise HTTPException(status_code=400, detail="Očekávám 1 až 500 položek")
    for pid in {i.person_id for i in items}:
        require_person(db, pid)
    inserted, skipped = save_labs(db, items)
    return {"inserted": inserted, "skipped": skipped}


class SettingIn(BaseModel):
    analyte: str = Field(min_length=1, max_length=200)
    mode: Literal["rozmezi", "nizsi", "vyssi", "nehodnotit"]


@router.get("/settings")
def get_settings_(db: sqlite3.Connection = Depends(get_db)):
    """Vlastní interpretace ukazatelů (jen odchylky od výchozích)."""
    return {r["analyte"]: r["mode"] for r in db.execute("SELECT analyte, mode FROM lab_setting")}


@router.put("/settings")
def put_setting(item: SettingIn, db: sqlite3.Connection = Depends(get_db)):
    db.execute("INSERT INTO lab_setting (analyte, mode) VALUES (?, ?) "
               "ON CONFLICT(analyte) DO UPDATE SET mode = excluded.mode", (item.analyte, item.mode))
    db.commit()
    return {"analyte": item.analyte, "mode": item.mode}
