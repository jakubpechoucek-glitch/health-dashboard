import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS garmin_raw (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 1,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    summary_type TEXT NOT NULL,
    payload TEXT NOT NULL
);

-- Denní metriky ze všech zdrojů (Garmin, později i další)
CREATE TABLE IF NOT EXISTS metric_daily (
    person_id INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    metric TEXT NOT NULL,
    day TEXT NOT NULL,          -- YYYY-MM-DD
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (person_id, source, metric, day)
);

-- Aktivity z hodinek (běh, jízda na kole, posilování…); raw = všechna pole aktivity bez GPS polohy
CREATE TABLE IF NOT EXISTS activity (
    activity_id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 1,
    day TEXT NOT NULL,
    start_local TEXT NOT NULL,
    type_key TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    duration_s REAL, distance_m REAL, calories REAL, avg_hr REAL, max_hr REAL, elevation_gain REAL, avg_speed REAL,
    steps INTEGER, aerobic_te REAL, anaerobic_te REAL, vo2max REAL,
    raw TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_activity_day ON activity (day);

-- Drobná nastavení uživatelského rozhraní (např. připnuté metriky), sdílená mezi zařízeními
CREATE TABLE IF NOT EXISTS ui_pref (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Technické značky (např. že se surová data Garminu už jednou přepočítala)
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Historie synchronizací Garminu (automatických i ručních)
CREATE TABLE IF NOT EXISTS sync_run (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,      -- ISO, UTC
    finished_at TEXT NOT NULL,
    source TEXT NOT NULL,          -- auto | manual
    ok INTEGER NOT NULL,
    days INTEGER NOT NULL,
    metrics_stored INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT ''
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
    for table in ("lab_result", "health_event", "garmin_raw", "activity", "sync_run"):   # starší databáze nemají osobu: vše patří vlastníkovi (osoba 1)
        if "person_id" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN person_id INTEGER NOT NULL DEFAULT 1")
    if "person_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(metric_daily)")}:   # osoba je součástí klíče: přestavět s kopií dat
        conn.executescript("""
            CREATE TABLE metric_daily_new (
                person_id INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL, metric TEXT NOT NULL, day TEXT NOT NULL,
                value REAL NOT NULL, unit TEXT NOT NULL DEFAULT '', PRIMARY KEY (person_id, source, metric, day));
            INSERT INTO metric_daily_new (person_id, source, metric, day, value, unit)
                SELECT 1, source, metric, day, value, unit FROM metric_daily;
            DROP TABLE metric_daily;
            ALTER TABLE metric_daily_new RENAME TO metric_daily;
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_activity_person_day ON activity (person_id, day)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_sync_person ON sync_run (person_id, source, ok)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_person ON lab_result (person_id, analyte, taken_on)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_event_person ON health_event (person_id, day)")
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":              # WAL: čtení neblokuje zápis, takže automatická synchronizace nebrzdí aplikaci
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
