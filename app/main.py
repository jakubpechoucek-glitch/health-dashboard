from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from . import garmin, labs, records, status
from .auth import require_auth
from .config import get_settings
from .db import connect
from .seed import apply_seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "db"):
        app.state.db = connect(get_settings().health_db_path)
        apply_seed(app.state.db, get_settings().seed_json_b64)
    yield
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
app.include_router(status.router, dependencies=[Depends(require_auth)])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    # Stránka sama žádná data neobsahuje, ta se načítají přes API s tokenem.
    return FileResponse(Path(__file__).parent / "static" / "index.html")
