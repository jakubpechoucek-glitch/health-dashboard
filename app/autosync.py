"""Pravidelná automatická synchronizace Garminu uvnitř aplikace.

Proč ne samostatný cron na Renderu: databáze leží na disku, který jde připojit jen k jedné službě, a cron by ji neviděl.
Smyčka běží jako úloha na pozadí webové služby, každých ~10 minut zkontroluje, jestli je synchronizace na řadě
(poslední úspěšná je starší než interval), a když ano, obnoví posledních několik dní. Stav je v databázi (`sync_run`),
takže restart služby nic nezpůsobí: po startu se jen zjistí, kdy proběhla poslední úspěšná.
Po neúspěšném pokusu se další zkouší nejdřív za 30 minut, aby se nebušilo do API.
"""
import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx

from .config import Settings
from .db import connect
from .garmin import GarminClient, SyncBusy, last_run_time, run_sync

log = logging.getLogger("uvicorn.error")
INITIAL_DELAY = 90          # po startu chvíli počkat, ať se služba usadí
CHECK_EVERY = 600           # jak často se kontroluje, jestli je synchronizace na řadě
RETRY_AFTER = timedelta(minutes=30)
SOURCES_FLAG = "sync_sources_v2"   # značka, že se po přidání aktivit, váhy a kondice už proběhl první běh


def is_due(last_ok: datetime | None, last_try: datetime | None, now: datetime, interval_hours: float) -> bool:
    if last_ok is not None and now - last_ok < timedelta(hours=interval_hours):
        return False
    return last_try is None or now - last_try >= RETRY_AFTER


def maybe_run(db, settings: Settings, now: datetime | None = None,
              http_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=30)) -> dict | None:
    """Spustí synchronizaci, je-li na řadě. Vrací výsledek, nebo None, když se nic nedělo."""
    if settings.auto_sync_hours <= 0 or not settings.garmin_api_key:
        return None
    now = now or datetime.now(timezone.utc)
    last_ok, last_try = last_run_time(db, ok_only=True), last_run_time(db, source="auto")
    novy_zdroj = db.execute("SELECT 1 FROM meta WHERE key = ?", (SOURCES_FLAG,)).fetchone() is None
    # po aktualizaci o nové zdroje dat (aktivity, váha…) proběhne první běh hned, ne až za interval; při chybě se opakuje po 30 minutách
    if novy_zdroj:
        last_fail = last_run_time(db, source="auto", failed_only=True)
        if last_fail is not None and now - last_fail < RETRY_AFTER:
            return None
    elif not is_due(last_ok, last_try, now, settings.auto_sync_hours):
        return None
    end = date.today()
    try:
        with http_factory() as http:
            result = run_sync(db, GarminClient(http, settings.garmin_api_key),
                              end - timedelta(days=settings.auto_sync_days), end, "auto")    # o den víc: pásmo časových zón
    except SyncBusy:
        return None
    if novy_zdroj and not result["errors"]:
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (SOURCES_FLAG, now.isoformat(timespec="seconds")))
        db.commit()
    return result


def _job(settings: Settings) -> dict | None:
    db = connect(settings.health_db_path)       # vlastní spojení pro vlákno úlohy
    try:
        return maybe_run(db, settings)
    finally:
        db.close()


async def scheduler(settings: Settings) -> None:
    if settings.auto_sync_hours <= 0:
        log.info("autosync: vypnuto (AUTO_SYNC_HOURS=0)")
        return
    if not settings.garmin_api_key:
        log.info("autosync: neběží, chybí GARMIN_API_KEY")
        return
    log.info("autosync: každých %s h, obnovuje posledních %s dní", settings.auto_sync_hours, settings.auto_sync_days + 1)
    await asyncio.sleep(INITIAL_DELAY)
    while True:
        try:
            result = await asyncio.to_thread(_job, settings)
            if result is not None:
                log.info("autosync: hotovo, uloženo %s hodnot, chyb %s", result["metrics_stored"], len(result["errors"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:                  # úloha nesmí nikdy shodit aplikaci; obsah chyby se do logu nepíše
            log.error("autosync: selhalo (%s)", type(e).__name__)
        await asyncio.sleep(CHECK_EVERY)
