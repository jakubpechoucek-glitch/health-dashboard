from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import garmin, records
from .config import get_settings
from .db import connect


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "db"):
        app.state.db = connect(get_settings().health_db_path)
    yield
    app.state.db.close()


app = FastAPI(title="Zdravotní přehled", lifespan=lifespan)
app.include_router(garmin.router)
app.include_router(records.router)


@app.get("/health")
def health():
    return {"ok": True}
