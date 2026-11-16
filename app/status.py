"""Stav úložiště a záloha databáze (jen pro přihlášeného vlastníka)."""
import os
import sqlite3
import tempfile
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .config import Settings, get_settings
from .records import get_db

router = APIRouter(tags=["status"])


def _on_separate_disk(path: str) -> bool:
    """True, když soubor leží na jiném souboru systému než kořen kontejneru (tedy na připojeném disku)."""
    try:
        return os.stat(os.path.dirname(os.path.abspath(path)) or "/").st_dev != os.stat("/").st_dev
    except OSError:
        return False


@router.get("/status")
def status(db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    one = lambda sql: db.execute(sql).fetchone()[0]
    path = settings.health_db_path
    return {
        "db_path": path,
        "db_bytes": os.path.getsize(path) if os.path.exists(path) else None,
        "persistent_disk": _on_separate_disk(path),
        "labs": one("SELECT COUNT(*) FROM lab_result"),
        "lab_days": one("SELECT COUNT(DISTINCT taken_on) FROM lab_result"),
        "metric_values": one("SELECT COUNT(*) FROM metric_daily"),
        "metric_days": one("SELECT COUNT(DISTINCT day) FROM metric_daily"),
        "first_metric_day": one("SELECT MIN(day) FROM metric_daily"),
        "last_metric_day": one("SELECT MAX(day) FROM metric_daily"),
        "events": one("SELECT COUNT(*) FROM health_event"),
        "persons": [
            {"id": r["id"], "name": r["name"],
             "labs": db.execute("SELECT COUNT(*) FROM lab_result WHERE person_id = ?", (r["id"],)).fetchone()[0],
             "lab_days": db.execute("SELECT COUNT(DISTINCT taken_on) FROM lab_result WHERE person_id = ?", (r["id"],)).fetchone()[0],
             "events": db.execute("SELECT COUNT(*) FROM health_event WHERE person_id = ?", (r["id"],)).fetchone()[0],
             "metric_days": db.execute("SELECT COUNT(DISTINCT day) FROM metric_daily WHERE person_id = ?", (r["id"],)).fetchone()[0]}
            for r in db.execute("SELECT id, name FROM person ORDER BY id")
        ],
    }


@router.get("/backup")
def backup(db: sqlite3.Connection = Depends(get_db)):
    """Konzistentní kopie celé databáze (SQLite backup API), ke stažení jako soubor."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    target = sqlite3.connect(tmp)
    try:
        db.backup(target)
    finally:
        target.close()
    return FileResponse(
        tmp, media_type="application/vnd.sqlite3", filename=f"health-{date.today().isoformat()}.db",
        background=BackgroundTask(os.remove, tmp),
    )
