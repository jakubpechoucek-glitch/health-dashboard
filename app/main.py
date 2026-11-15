from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import garmin, records
from .auth import require_auth
from .config import get_settings
from .db import connect


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "db"):
        app.state.db = connect(get_settings().health_db_path)
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
app.include_router(garmin.router)
app.include_router(records.router, dependencies=[Depends(require_auth)])


@app.get("/health")
def health():
    return {"ok": True}
