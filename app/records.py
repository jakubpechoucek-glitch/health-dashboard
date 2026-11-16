import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

router = APIRouter(tags=["data"])


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def require_person(db: sqlite3.Connection, person_id: int) -> None:
    if db.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Osoba neexistuje")


class LabIn(BaseModel):
    person_id: int = 1
    taken_on: date
    analyte: str = Field(min_length=1)
    value: float | None = None
    value_text: str = ""       # kvalitativní výsledek (neg, +, G1), když není číselný
    unit: str = ""
    lab: str = ""
    ref_low: float | None = None
    ref_high: float | None = None
    note: str = ""

    @model_validator(mode="after")
    def _has_value(self):
        if self.value is None and not self.value_text:
            raise ValueError("Chybí hodnota (value nebo value_text)")
        return self


class EventIn(BaseModel):
    person_id: int = 1
    day: date
    kind: str = "other"
    title: str = Field(min_length=1)
    note: str = ""


@router.post("/labs", status_code=201)
def add_lab(item: LabIn, db: sqlite3.Connection = Depends(get_db)):
    require_person(db, item.person_id)
    cur = db.execute(
        "INSERT INTO lab_result (person_id, taken_on, lab, analyte, value, value_text, unit, ref_low, ref_high, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (item.person_id, item.taken_on.isoformat(), item.lab, item.analyte, item.value, item.value_text, item.unit,
         item.ref_low, item.ref_high, item.note),
    )
    db.commit()
    return {"id": cur.lastrowid}


@router.get("/labs")
def list_labs(person_id: int = 1, analyte: str | None = None, db: sqlite3.Connection = Depends(get_db)):
    require_person(db, person_id)
    sql, args = "SELECT * FROM lab_result WHERE person_id = ?", [person_id]
    if analyte:
        sql += " AND analyte = ?"
        args.append(analyte)
    rows = [dict(r) for r in db.execute(sql + " ORDER BY taken_on, id", args)]
    for r in rows:
        v = r["value"]
        r["out_of_range"] = v is not None and (
            (r["ref_low"] is not None and v < r["ref_low"]) or (r["ref_high"] is not None and v > r["ref_high"]))
    return rows


@router.post("/events", status_code=201)
def add_event(item: EventIn, db: sqlite3.Connection = Depends(get_db)):
    require_person(db, item.person_id)
    cur = db.execute(
        "INSERT INTO health_event (person_id, day, kind, title, note) VALUES (?,?,?,?,?)",
        (item.person_id, item.day.isoformat(), item.kind, item.title, item.note),
    )
    db.commit()
    return {"id": cur.lastrowid}


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("DELETE FROM health_event WHERE id = ?", (event_id,))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Událost neexistuje")


@router.get("/events")
def list_events(person_id: int = 1, db: sqlite3.Connection = Depends(get_db)):
    require_person(db, person_id)
    return [dict(r) for r in db.execute("SELECT * FROM health_event WHERE person_id = ? ORDER BY day", (person_id,))]


@router.get("/metrics")
def list_metrics(
    person_id: int = 1,
    metric: list[str] | None = Query(None),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    db: sqlite3.Connection = Depends(get_db),
):
    require_person(db, person_id)
    sql, args, where = "SELECT source, metric, day, value, unit FROM metric_daily", [], ["person_id = ?"]
    args.append(person_id)
    if metric:
        where.append("metric IN (%s)" % ",".join("?" * len(metric))); args.extend(metric)
    if start:
        where.append("day >= ?"); args.append(start.isoformat())
    if end:
        where.append("day <= ?"); args.append(end.isoformat())
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in db.execute(sql + " ORDER BY day, metric", args)]


@router.get("/metrics/catalog")
def metrics_catalog(person_id: int = 1, db: sqlite3.Connection = Depends(get_db)):
    """Přehled všech uložených metrik jedné osoby: kolik dní, od kdy do kdy, rozsah hodnot a poslední hodnota."""
    require_person(db, person_id)
    last = {r["metric"]: r["value"] for r in db.execute(
        "SELECT metric, value FROM metric_daily WHERE person_id = ? AND (metric, day) IN "
        "(SELECT metric, MAX(day) FROM metric_daily WHERE person_id = ? GROUP BY metric)", (person_id, person_id))}
    return [
        {"metric": r["metric"], "unit": r["unit"], "days": r["days"], "first_day": r["first_day"], "last_day": r["last_day"],
         "min": r["vmin"], "max": r["vmax"], "last": last.get(r["metric"])}
        for r in db.execute("SELECT metric, MAX(unit) AS unit, COUNT(*) AS days, MIN(day) AS first_day, MAX(day) AS last_day, "
                            "MIN(value) AS vmin, MAX(value) AS vmax FROM metric_daily WHERE person_id = ? GROUP BY metric ORDER BY metric", (person_id,))
    ]


@router.get("/timeline")
def timeline(person_id: int = 1, db: sqlite3.Connection = Depends(get_db)):
    """Společná časová osa jedné osoby: události + krevní rozbory (metriky Garminu jsou přes /metrics)."""
    require_person(db, person_id)
    items = [
        {"id": r["id"], "day": r["day"], "type": "event", "kind": r["kind"], "title": r["title"], "note": r["note"]}
        for r in db.execute("SELECT * FROM health_event WHERE person_id = ?", (person_id,))
    ]
    for r in db.execute(
        "SELECT taken_on, COUNT(*) AS n, SUM(CASE WHEN value IS NOT NULL AND "
        "((ref_low IS NOT NULL AND value < ref_low) OR (ref_high IS NOT NULL AND value > ref_high)) "
        "THEN 1 ELSE 0 END) AS mimo FROM lab_result WHERE person_id = ? GROUP BY taken_on", (person_id,)
    ):
        items.append({"day": r["taken_on"], "type": "lab", "title": "Krevní rozbory", "count": r["n"],
                      "out_of_range_count": r["mimo"], "out_of_range": r["mimo"] > 0})
    return sorted(items, key=lambda i: i["day"])
