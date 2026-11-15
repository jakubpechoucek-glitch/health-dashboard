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

-- Osoby, jejichž zdraví se sleduje. Osoba 1 je vlastník (Garmin patří jemu).
CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
INSERT OR IGNORE INTO person (id, name) VALUES (1, 'Já');

CREATE TABLE IF NOT EXISTS lab_result (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 1,
    taken_on TEXT NOT NULL,     -- YYYY-MM-DD
    lab TEXT NOT NULL DEFAULT '',
    analyte TEXT NOT NULL,      -- např. "Ferritin"
    value REAL,                 -- číselný výsledek, nebo NULL u kvalitativního (neg, +)
    value_text TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    ref_low REAL,
    ref_high REAL,
    note TEXT NOT NULL DEFAULT '',
    CHECK (value IS NOT NULL OR value_text != '')
);

-- Jak se má u ukazatele hodnotit změna (nižší / vyšší je lepší, v rozmezí, nehodnotit); výchozí hodnoty jsou v UI
CREATE TABLE IF NOT EXISTS lab_setting (
    analyte TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('rozmezi', 'nizsi', 'vyssi', 'nehodnotit'))
);

-- Události na časové ose: kolonoskopie, výsledek genetiky, změna léků...
CREATE TABLE IF NOT EXISTS health_event (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 1,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,         -- exam | genetics | medication | other
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Starší tabulka lab_result měla `value REAL NOT NULL` a chyběl `value_text`: přestavět s kopií dat."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lab_result)")}
    if "value_text" not in cols:
        conn.executescript("""
            ALTER TABLE lab_result RENAME TO lab_result_old;
            CREATE TABLE lab_result (
                id INTEGER PRIMARY KEY, taken_on TEXT NOT NULL, lab TEXT NOT NULL DEFAULT '',
                analyte TEXT NOT NULL, value REAL, value_text TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT '', ref_low REAL, ref_high REAL, note TEXT NOT NULL DEFAULT '',
                CHECK (value IS NOT NULL OR value_text != '')
            );
            INSERT INTO lab_result (id, taken_on, lab, analyte, value, unit, ref_low, ref_high, note)
                SELECT id, taken_on, lab, analyte, value, unit, ref_low, ref_high, note FROM lab_result_old;
            DROP TABLE lab_result_old;
        """)
    for table in ("lab_result", "health_event"):               # starší databáze nemají osobu: vše patří vlastníkovi (osoba 1)
        if "person_id" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN person_id INTEGER NOT NULL DEFAULT 1")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_person ON lab_result (person_id, analyte, taken_on)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_event_person ON health_event (person_id, day)")
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
