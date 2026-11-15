"""Import krevních rozborů z PDF výsledkového listu (laboratorní systém SmartMEDIX).

Podporuje dvě rozložení, která laboratoř používá:
  A) „Výsledkový list“:  kód  název  hodnota jednotka  příznaky  ref_od - ref_do
  B) „Laboratorní výsledky pacienta“:  kód  název  ref_od / ref_do  hodnota jednotka

PDF se nikdy neukládá. Z textu se čtou jen řádky s výsledky a datum odběru; jméno pacienta,
číslo pojištěnce ani další identifikátory se nezpracovávají ani neukládají.
"""
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import date

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .records import LabIn, get_db

router = APIRouter(prefix="/labs", tags=["labs"])
MAX_PDF_BYTES = 5 * 1024 * 1024

# Kódy močových testů: stejný název (Glukóza, Leukocyty…) existuje i v krvi, proto přípona.
MOC = {
    "03364": "Glukóza (moč)", "03413": "Bílkovina (moč)", "03280": "Bilirubin (moč)",
    "03434": "Urobilinogen (moč)", "03410": "pH (moč)", "03371": "Krev (moč)",
    "03388": "Leukocyty (moč, proužek)", "03377": "Ketolátky (moč)", "03399": "Nitrity (moč)",
    "03137": "Specifická hustota (moč)", "03355": "Erytrocyty (moč, sediment)",
    "03384": "Leukocyty (moč, sediment)", "08462": "Bakterie (moč, sediment)",
    "12334": "Hlen (moč, sediment)",
}

_NUM = r"[<>]?\d+(?:,\d+)?"
_ROW = re.compile(r"^\s*(?P<code>\d{5})\s+(?P<name>\S.*?)\s{2,}(?P<rest>\S.*)$")
_REF_A = re.compile(rf"(?P<lo>\d+(?:,\d+)?)\s*-\s*(?P<hi>\d+(?:,\d+)?)\s*$")           # „… 2,8 - 8,1“
_REF_B = re.compile(rf"^(?P<lo>\d+(?:,\d+)?)?\s*/\s*(?P<hi>\d+(?:,\d+)?)?\s*(?P<rest>.*)$")  # „2,80 / 8,10  4,2 mmol/L“
_DATE = re.compile(r"Datum odběru:\s*(\d{2})\.(\d{2})\.(\d{4})")


class LabParseError(Exception):
    pass


def _f(s: str | None) -> float | None:
    return float(s.replace(",", ".")) if s else None


def _value_unit(text: str) -> tuple[float | None, str, str, str]:
    """Z „5,4 mmol/L  L | |*| |“ udělá (číslo, text, jednotka, předpona <>)."""
    parts = text.split()
    first = parts[0]
    m = re.fullmatch(rf"([<>]?)(\d+(?:,\d+)?)", first)
    if not m:                                   # kvalitativní výsledek: neg, +, G1
        return None, first, "", ""
    unit = parts[1] if len(parts) > 1 and parts[1] not in {"L", "|"} and not parts[1].startswith("|") else ""
    return _f(m.group(2)), "", unit.strip("|"), m.group(1)


def parse_lab_text(text: str) -> dict:
    m = _DATE.search(text)
    if not m:
        raise LabParseError("V dokumentu jsem nenašel datum odběru")
    taken_on = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    lab = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    results, unparsed, seen = [], [], set()

    for line in text.splitlines():
        row = _ROW.match(line)
        if not row:
            continue
        code, name, rest = row["code"], row["name"].strip(), row["rest"].strip()
        lo = hi = None
        b = _REF_B.match(rest)
        if b and ("/" in rest.split()[0] or rest.startswith("/") or re.match(rf"^{_NUM}\s*/", rest)):
            lo, hi, rest = _f(b["lo"]), _f(b["hi"]), b["rest"].strip()        # rozložení B
        else:
            a = _REF_A.search(rest)
            if a:                                                              # rozložení A
                lo, hi, rest = _f(a["lo"]), _f(a["hi"]), rest[:a.start()].strip()
        if not rest:
            unparsed.append(line.strip()); continue
        value, value_text, unit, prefix = _value_unit(rest)
        if value is None and not value_text:
            unparsed.append(line.strip()); continue
        analyte = MOC.get(code, name)
        n, base = 2, analyte
        while analyte in seen:                  # nikdy nepřepsat dva různé řádky se stejným názvem
            analyte, n = f"{base} ({n})", n + 1
        seen.add(analyte)
        results.append({"analyte": analyte, "value": value, "value_text": value_text, "unit": unit,
                        "ref_low": lo, "ref_high": hi, "note": prefix})
    return {"taken_on": taken_on, "lab": lab, "results": results, "unparsed_rows": unparsed}


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
    """Uloží výsledky, již uložené (stejný den, analyt a hodnota) přeskočí. Vrací (uloženo, přeskočeno)."""
    inserted = skipped = 0
    for it in items:
        dup = db.execute(
            "SELECT 1 FROM lab_result WHERE taken_on=? AND analyte=? AND value IS ? AND value_text=?",
            (it.taken_on.isoformat(), it.analyte, it.value, it.value_text),
        ).fetchone()
        if dup:
            skipped += 1
            continue
        db.execute(
            "INSERT INTO lab_result (taken_on, lab, analyte, value, value_text, unit, ref_low, ref_high, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (it.taken_on.isoformat(), it.lab, it.analyte, it.value, it.value_text, it.unit,
             it.ref_low, it.ref_high, it.note),
        )
        inserted += 1
    db.commit()
    return inserted, skipped


@router.post("/bulk", status_code=201)
def bulk(payload: dict, db: sqlite3.Connection = Depends(get_db)):
    """Uloží potvrzené výsledky. Již uložené (stejný den, analyt a hodnota) přeskočí."""
    items = [LabIn(**i) for i in payload.get("items", [])]
    if not items or len(items) > 500:
        raise HTTPException(status_code=400, detail="Očekávám 1 až 500 položek")
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
