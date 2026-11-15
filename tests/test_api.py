import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import connect
from app.main import app


@pytest.fixture
def client():
    app.state.db = connect(":memory:")
    app.dependency_overrides[get_settings] = lambda: Settings(garmin_webhook_token="tajne")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.db


def test_webhook_rejects_wrong_token(client):
    assert client.post("/garmin/webhook/spatne", json={}).status_code == 404


def test_webhook_stores_dailies_and_is_idempotent(client):
    payload = {"dailies": [{"calendarDate": "2026-11-14", "steps": 9000,
                            "restingHeartRateInBeatsPerMinute": 52}]}
    assert client.post("/garmin/webhook/tajne", json=payload).json() == {"stored": 2}
    payload["dailies"][0]["steps"] = 9500  # Garmin posílá aktualizace téhož dne
    client.post("/garmin/webhook/tajne", json=payload)
    rows = client.get("/metrics", params={"metric": "steps"}).json()
    assert [r["value"] for r in rows] == [9500.0]


def test_unknown_summary_is_kept_raw(client):
    client.post("/garmin/webhook/tajne", json={"hrv": [{"calendarDate": "2026-11-14"}]})
    n = app.state.db.execute("SELECT COUNT(*) FROM garmin_raw").fetchone()[0]
    assert n == 1


def test_timeline_merges_labs_and_events(client):
    client.post("/events", json={"day": "2026-10-01", "kind": "exam", "title": "Kolonoskopie"})
    client.post("/labs", json={"taken_on": "2026-11-01", "analyte": "Ferritin",
                               "value": 20, "unit": "µg/l", "ref_low": 30, "ref_high": 300})
    tl = client.get("/timeline").json()
    assert [i["title"] for i in tl] == ["Kolonoskopie", "Ferritin"]
    assert tl[1]["out_of_range"] is True
