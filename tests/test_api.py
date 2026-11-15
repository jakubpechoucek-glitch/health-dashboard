import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings, get_settings
from app.db import connect
from app.main import app


@pytest.fixture
def client(monkeypatch):
    app.state.db = connect(":memory:")
    app.dependency_overrides[get_settings] = lambda: Settings(
        allowed_emails="ja@example.com", garmin_api_key="test-klic"
    )
    auth._CACHE.clear()
    # "Supabase": token "ok-ja" patří vlastníkovi, "ok-jiny" jinému členu rodiny
    monkeypatch.setattr(
        auth, "fetch_email",
        lambda token, settings: {"ok-ja": "ja@example.com", "ok-jiny": "jiny@example.com"}.get(token),
    )
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer ok-ja"
        yield c
    app.dependency_overrides.clear()
    del app.state.db


def test_timeline_merges_labs_and_events(client):
    client.post("/events", json={"day": "2026-10-01", "kind": "exam", "title": "Kolonoskopie"})
    client.post("/labs", json={"taken_on": "2026-11-01", "analyte": "Ferritin",
                               "value": 20, "unit": "µg/l", "ref_low": 30, "ref_high": 300})
    tl = client.get("/timeline").json()
    assert [i["title"] for i in tl] == ["Kolonoskopie", "Ferritin"]
    assert tl[1]["out_of_range"] is True


def test_data_endpoints_require_login(client):
    anon = TestClient(app)
    assert anon.get("/labs").status_code == 401                                          # bez tokenu
    assert anon.get("/labs", headers={"Authorization": "Bearer spatny"}).status_code == 401  # neplatný token
    assert anon.get("/health").status_code == 200  # health check pro Render


def test_other_family_member_is_forbidden(client):
    r = TestClient(app).get("/timeline", headers={"Authorization": "Bearer ok-jiny"})
    assert r.status_code == 403


def test_refuses_when_no_allowed_emails(client):
    app.dependency_overrides[get_settings] = lambda: Settings(allowed_emails="")
    assert client.get("/labs").status_code == 503


def test_index_page_is_public_and_has_no_data(client):
    r = TestClient(app).get("/")
    assert r.status_code == 200 and "Zdravotní přehled" in r.text


# ---------- Garmin sync (Garmin je nahrazen mock serverem, nikdy se nevolá skutečné API) ----------

import httpx

from app import garmin

DAILY = {"data": [{"calendarDate": "2026-11-14", "movement": {"steps": {"value": 9000}},
                   "heartRate": {"restingValue": 52}, "stress": {"avgLevel": 25},
                   "calories": {"burnedActive": 600}}]}
SLEEP = [{"calendarDate": "2026-11-14", "sleepTimeSeconds": 27000, "deepSleepSeconds": 5400,
          "sleepScores": {"overall": {"value": 81}}}]
HRV = {"hrvSummaries": [{"calendarDate": "2026-11-14", "lastNightAvg": 48, "weeklyAvg": 50}]}


def make_garmin(calls, token_status=200, data_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/personal-api-key/token"):
            return httpx.Response(token_status, json={"access_token": f"tok{len(calls)}"})
        if data_status != 200:
            return httpx.Response(data_status)
        if "dailysummaries" in request.url.path:
            return httpx.Response(200, json=DAILY)
        if "sleep" in request.url.path:
            return httpx.Response(200, json=SLEEP)
        return httpx.Response(200, json=HRV)
    return handler


@pytest.fixture
def garmin_calls():
    garmin._token_cache.update(token=None, exp=0.0)
    calls = []
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(
        transport=httpx.MockTransport(make_garmin(calls)))
    yield calls
    app.dependency_overrides.pop(garmin.get_http, None)


def test_sync_requires_login(client):
    assert TestClient(app).post("/garmin/sync").status_code == 401


def test_sync_stores_metrics_and_uses_api_key_then_bearer(client, garmin_calls):
    r = client.post("/garmin/sync", params={"days": 3}).json()
    assert r["errors"] == [] and r["metrics_stored"] == 9  # denní 4 (kroky, klidový tep, stres, kcal) + spánek 3 + HRV 2
    assert r["by_type"]["sleep"] == {"days_returned": 1, "metrics_stored": 3,
        "metrics": {"sleep_seconds": 1, "sleep_deep_seconds": 1, "sleep_score": 1}}
    got = {m["metric"]: m["value"] for m in client.get("/metrics").json()}
    assert got["steps"] == 9000 and got["resting_hr"] == 52 and got["sleep_score"] == 81
    assert got["hrv_last_night_avg"] == 48 and got["sleep_deep_seconds"] == 5400
    # první volání je token (ApiKey), ostatní používají Bearer a token se nežádá znovu
    assert garmin_calls[0][2] == "ApiKey test-klic"
    assert all(c[2] == "Bearer tok1" for c in garmin_calls[1:])
    assert sum(1 for c in garmin_calls if c[1].endswith("/token")) == 1
    raw = app.state.db.execute("SELECT COUNT(*) FROM garmin_raw").fetchone()[0]
    assert raw == 3


def test_sync_is_idempotent(client, garmin_calls):
    client.post("/garmin/sync")
    client.post("/garmin/sync")
    assert len(client.get("/metrics", params={"metric": "steps"}).json()) == 1


def test_sync_reports_errors_without_leaking_key(client):
    garmin._token_cache.update(token=None, exp=0.0)
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(
        transport=httpx.MockTransport(make_garmin([], token_status=403)))
    r = client.post("/garmin/sync").json()
    app.dependency_overrides.pop(garmin.get_http, None)
    assert r["metrics_stored"] == 0 and len(r["errors"]) == 3
    assert "test-klic" not in str(r) and "HTTP 403" in r["errors"][0]


def test_sync_without_api_key_is_503(client):
    app.dependency_overrides[get_settings] = lambda: Settings(allowed_emails="ja@example.com")
    assert client.post("/garmin/sync").status_code == 503
