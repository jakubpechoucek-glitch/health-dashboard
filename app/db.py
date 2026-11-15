import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS garmin_raw (
    id INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    summary_type TEXT NOT NULL,
    payload TEXT NOT NULL
);

-- Denní metriky ze všech zdrojů (Garmin, později i další)
CREATE TABLE IF NOT EXISTS metric_daily (
    source TEXT NOT NULL,
    metric TEXT NOT NULL,
    day TEXT NOT NULL,          -- YYYY-MM-DD
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source, metric, day)
);

CREATE TABLE IF NOT EXISTS lab_result (
    id INTEGER PRIMARY KEY,
    taken_on TEXT NOT NULL,     -- YYYY-MM-DD
    lab TEXT NOT NULL DEFAULT '',
    analyte TEXT NOT NULL,      -- např. "Ferritin"
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    ref_low REAL,
    ref_high REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_lab_analyte_day ON lab_result (analyte, taken_on);

-- Události na časové ose: kolonoskopie, výsledek genetiky, změna léků...
CREATE TABLE IF NOT EXISTS health_event (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,         -- exam | genetics | medication | other
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
