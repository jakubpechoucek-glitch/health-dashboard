import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["data"])


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


class LabIn(BaseModel):
    taken_on: date
    analyte: str = Field(min_length=1)
    value: float
    unit: str = ""
    lab: str = ""
    ref_low: float | None = None
    ref_high: float | None = None
    note: str = ""


class EventIn(BaseModel):
    day: date
    kind: str = "other"
    title: str = Field(min_length=1)
    note: str = ""


@router.post("/labs", status_code=201)
def add_lab(item: LabIn, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "INSERT INTO lab_result (taken_on, lab, analyte, value, unit, ref_low, ref_high, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (item.taken_on.isoformat(), item.lab, item.analyte, item.value, item.unit,
         item.ref_low, item.ref_high, item.note),
    )
    db.commit()
    return {"id": cur.lastrowid}


@router.get("/labs")
def list_labs(analyte: str | None = None, db: sqlite3.Connection = Depends(get_db)):
    sql, args = "SELECT * FROM lab_result", []
    if analyte:
        sql += " WHERE analyte = ?"
        args.append(analyte)
    return [dict(r) for r in db.execute(sql + " ORDER BY taken_on, analyte", args)]


@router.post("/events", status_code=201)
def add_event(item: EventIn, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "INSERT INTO health_event (day, kind, title, note) VALUES (?,?,?,?)",
        (item.day.isoformat(), item.kind, item.title, item.note),
    )
    db.commit()
    return {"id": cur.lastrowid}


@router.get("/events")
def list_events(db: sqlite3.Connection = Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT * FROM health_event ORDER BY day")]


@router.get("/metrics")
def list_metrics(
    metric: str | None = None,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    db: sqlite3.Connection = Depends(get_db),
):
    sql, args, where = "SELECT * FROM metric_daily", [], []
    if metric:
        where.append("metric = ?"); args.append(metric)
    if start:
        where.append("day >= ?"); args.append(start.isoformat())
    if end:
        where.append("day <= ?"); args.append(end.isoformat())
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in db.execute(sql + " ORDER BY day, metric", args)]


@router.get("/timeline")
def timeline(db: sqlite3.Connection = Depends(get_db)):
    """Společná časová osa: události + krevní rozbory (metriky jsou přes /metrics)."""
    items = [
        {"day": r["day"], "type": "event", "kind": r["kind"], "title": r["title"], "note": r["note"]}
        for r in db.execute("SELECT * FROM health_event")
    ]
    items += [
        {
            "day": r["taken_on"], "type": "lab", "title": r["analyte"],
            "value": r["value"], "unit": r["unit"],
            "out_of_range": (r["ref_low"] is not None and r["value"] < r["ref_low"])
            or (r["ref_high"] is not None and r["value"] > r["ref_high"]),
        }
        for r in db.execute("SELECT * FROM lab_result")
    ]
    return sorted(items, key=lambda i: i["day"])
