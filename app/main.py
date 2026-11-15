import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from . import garmin, labs, persons, records, status
from .auth import require_auth
from .autosync import scheduler
from .config import get_settings
from .db import connect
from .seed import apply_seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if not hasattr(app.state, "db"):             # v testech je databáze dodaná zvenku a nic na pozadí neběží
        app.state.db = connect(get_settings().health_db_path)
        apply_seed(app.state.db, get_settings().seed_json_b64)
        task = asyncio.create_task(scheduler(get_settings()))
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    app.state.db.close()


_docs = get_settings().enable_docs
app = FastAPI(
    title="Zdravotní přehled",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs else None,
)
app.include_router(garmin.router, dependencies=[Depends(require_auth)])
app.include_router(records.router, dependencies=[Depends(require_auth)])
app.include_router(labs.router, dependencies=[Depends(require_auth)])
app.include_router(persons.router, dependencies=[Depends(require_auth)])
app.include_router(status.router, dependencies=[Depends(require_auth)])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    # Stránka sama žádná data neobsahuje, ta se načítají přes API s tokenem.
    return FileResponse(Path(__file__).parent / "static" / "index.html")
