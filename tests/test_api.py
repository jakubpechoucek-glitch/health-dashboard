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
    assert isinstance(tl[0]["id"], int)                                  # kvůli mazání v UI
    assert [i["title"] for i in tl] == ["Kolonoskopie", "Krevní rozbory"]
    assert tl[1]["out_of_range"] is True and tl[1]["count"] == 1


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


FLAT = {"calendarDate": "2026-11-14", "totalSteps": 8000, "restingHeartRate": 51,
        "averageStressLevel": 28, "bodyBatteryHighestValue": 85, "activeKilocalories": 500}


def make_garmin(calls, token_status=200, data_status=200, v2_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/personal-api-key/token"):
            return httpx.Response(token_status, json={"access_token": f"tok{len(calls)}"})
        if data_status != 200:
            return httpx.Response(data_status)
        if "dailysummaries" in request.url.path:
            return httpx.Response(v2_status, json=DAILY) if v2_status == 200 else httpx.Response(v2_status)
        if request.url.path.endswith("/usersummary/daily"):
            return httpx.Response(200, json={**FLAT, "calendarDate": request.url.params["calendarDate"]})
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


def test_sync_falls_back_to_per_day_when_v2_returns_500(client):
    """Skutečný případ: v2/dailysummaries vrací 500, kroky a tep se dotáhnou po dnech."""
    garmin._token_cache.update(token=None, exp=0.0)
    calls = []
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(
        transport=httpx.MockTransport(make_garmin(calls, v2_status=500)))
    r = client.post("/garmin/sync", params={"days": 3}).json()
    app.dependency_overrides.pop(garmin.get_http, None)
    steps = client.get("/metrics", params={"metric": "steps"}).json()
    assert len(steps) == 3 and {m["value"] for m in steps} == {8000.0}   # 3 dny po jednom
    assert client.get("/metrics", params={"metric": "body_battery_max"}).json()[0]["value"] == 85
    assert r["by_type"]["usersummary_daily"]["days_returned"] == 3
    assert any("HTTP 500" in e and "záloha po dnech proběhla" in e for e in r["errors"])
    assert sum(1 for c in calls if c[1].endswith("/usersummary/daily")) == 3


def test_fallback_budget_is_capped(client, monkeypatch):
    monkeypatch.setattr(garmin, "FALLBACK_DAYS", 2)
    garmin._token_cache.update(token=None, exp=0.0)
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(
        transport=httpx.MockTransport(make_garmin([], v2_status=500)))
    r = client.post("/garmin/sync", params={"days": 10}).json()
    app.dependency_overrides.pop(garmin.get_http, None)
    assert len(client.get("/metrics", params={"metric": "steps"}).json()) == 2
    assert any("limit" in e for e in r["errors"])


# ---------- import rozborů z PDF (texty jsou VYMYŠLENÉ, jen kopírují rozložení laboratoře) ----------

from app import labs

TEXT_A = """Nějaká ordinace s.r.o.
Výsledkový list
Pacient:     Test Pacient                                  č. pojištěnce: 000000/0000
Datum odběru:             01.02.2026 07:15   Číslo vzorku: X1
Skup.    Kód      Název                                   S         NumVýs.           Hodn.                 Ref. meze
         03085    Urea                                                5,4 mmol/L       L | |*| |             2,8 - 8,1
         17339    GF CKD-EPI                                         1,51 ml/s/1,73 L | |*| |                  1 -3
         55974    Hodnocení CKD dle eGFR                              G1
         18004    Lp(a)                                              123 nmol/L        L | | |* |              0 - 75
         01896    Glukóza                                               5 mmol/L       L | |*| |             3,9 - 5,6
         20662     Krevní obraz
(provedeno)
         03364    Glukóza                                             neg
         03413    Bílkovina                                              +
         03384    Leukocyty                                              4 elem./1µ    L 0 arb.j.            0 - 10
"""

TEXT_B = """Nějaká ordinace s.r.o.
Laboratorní výsledky pacienta
Pacient:     Ing. Test Pacient                               č. pojištěnce: 000000/0000
Datum odběru: 19.12.2023 0 Datum zpracování:       19.12.2023   Číslo vzorku:
Kód        Název                                                   Ref. meze            Hodnota Hodn
03085      Urea                                                   2,80 / 8,10           4,2 mmol/L | |*|
08556      GF CKD-EPI                                             1,00 / 3,00         1,58 ml/s/1,73| |*|
17357      non-HDL cholesterol                                    0,00 / 3,80         3,24 mmol/L
03364      Glukóza                                                     /               neg
20224 MS bez patologie                                          /
"""


def by_name(parsed):
    return {r["analyte"]: r for r in parsed["results"]}


def test_parse_layout_a():
    p = labs.parse_lab_text(TEXT_A)
    r = by_name(p)
    assert p["taken_on"] == "2026-02-01" and p["lab"] == "Nějaká ordinace s.r.o."
    assert (r["Urea"]["value"], r["Urea"]["unit"], r["Urea"]["ref_low"], r["Urea"]["ref_high"]) == (5.4, "mmol/L", 2.8, 8.1)
    assert (r["GF CKD-EPI"]["value"], r["GF CKD-EPI"]["unit"], r["GF CKD-EPI"]["ref_high"]) == (1.51, "ml/s/1,73", 3.0)
    assert r["Hodnocení CKD dle eGFR"]["value"] is None and r["Hodnocení CKD dle eGFR"]["value_text"] == "G1"
    assert r["Lp(a)"]["value"] == 123 and r["Lp(a)"]["ref_high"] == 75
    assert r["Glukóza"]["value"] == 5.0                                  # krev
    assert r["Glukóza (moč)"]["value_text"] == "neg"                     # moč má vlastní název
    assert r["Bílkovina (moč)"]["value_text"] == "+"
    assert r["Leukocyty (moč, sediment)"]["value"] == 4 and r["Leukocyty (moč, sediment)"]["unit"] == "elem./1µ"
    assert p["unparsed_rows"] == []                                      # „Krevní obraz“ je jen nadpis


def test_parse_layout_b_and_patient_data_is_ignored():
    p = labs.parse_lab_text(TEXT_B)
    r = by_name(p)
    assert p["taken_on"] == "2023-12-19"
    assert (r["Urea"]["value"], r["Urea"]["ref_low"], r["Urea"]["ref_high"]) == (4.2, 2.8, 8.1)
    assert r["GF CKD-EPI"]["unit"] == "ml/s/1,73"                        # bez zbylého „|“
    assert r["non-HDL cholesterol"]["value"] == 3.24 and r["non-HDL cholesterol"]["ref_high"] == 3.8
    assert r["Glukóza (moč)"]["value_text"] == "neg" and r["Glukóza (moč)"]["ref_low"] is None
    assert len(p["unparsed_rows"]) == 1 and "MS bez patologie" in p["unparsed_rows"][0]
    assert "Test Pacient" not in str(p) and "000000" not in str(p)       # identifikátory pacienta se nečtou


def test_missing_date_is_an_error():
    with pytest.raises(labs.LabParseError):
        labs.parse_lab_text("Výsledkový list\n03085 Urea     5,4 mmol/L   2,8 - 8,1")


def test_duplicate_names_in_one_report_are_not_lost():
    p = labs.parse_lab_text("Datum odběru: 01.01.2026\n11111  Něco   1 g/L   0 - 2\n22222  Něco   3 g/L   0 - 2\n")
    assert [r["analyte"] for r in p["results"]] == ["Něco", "Něco (2)"]


def test_preview_endpoint(client, monkeypatch):
    monkeypatch.setattr(labs, "extract_text", lambda pdf: TEXT_A)
    ok = client.post("/labs/import/preview", content=b"%PDF-1.4 ...", headers={"Content-Type": "application/pdf"})
    assert ok.status_code == 200 and ok.json()["taken_on"] == "2026-02-01"
    assert client.post("/labs/import/preview", content=b"ahoj").status_code == 400                # není PDF
    big = b"%PDF" + b"0" * (labs.MAX_PDF_BYTES + 1)
    assert client.post("/labs/import/preview", content=big).status_code == 413
    assert client.get("/labs").json() == []                                                       # náhled nic neukládá
    assert TestClient(app).post("/labs/import/preview", content=b"%PDF").status_code == 401


def test_bulk_save_is_idempotent_and_feeds_timeline(client):
    items = [{**r, "taken_on": "2026-02-01", "lab": "x"} for r in labs.parse_lab_text(TEXT_A)["results"]]
    first = client.post("/labs/bulk", json={"items": items}).json()
    assert first == {"inserted": len(items), "skipped": 0}
    assert client.post("/labs/bulk", json={"items": items}).json() == {"inserted": 0, "skipped": len(items)}
    rows = client.get("/labs").json()
    lp = next(r for r in rows if r["analyte"] == "Lp(a)")
    assert lp["out_of_range"] is True                                                             # 123 > 75
    assert next(r for r in rows if r["analyte"] == "Urea")["out_of_range"] is False
    tl = client.get("/timeline").json()
    assert tl == [{"day": "2026-02-01", "type": "lab", "title": "Krevní rozbory", "count": len(items),
                   "out_of_range_count": 1, "out_of_range": True}]


def test_manual_lab_needs_a_value(client):
    r = client.post("/labs", json={"taken_on": "2026-01-01", "analyte": "X"})
    assert r.status_code == 422
    assert client.post("/labs", json={"taken_on": "2026-01-01", "analyte": "X", "value_text": "neg"}).status_code == 201


def test_old_lab_table_is_migrated_with_data():
    import sqlite3, tempfile, os
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE lab_result (id INTEGER PRIMARY KEY, taken_on TEXT NOT NULL, lab TEXT NOT NULL DEFAULT '',
        analyte TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL DEFAULT '', ref_low REAL, ref_high REAL,
        note TEXT NOT NULL DEFAULT '');
        INSERT INTO lab_result (taken_on, analyte, value, unit) VALUES ('2025-01-01', 'Ferritin', 50, 'ug/l');""")
    old.commit(); old.close()
    db = connect(path)
    assert [tuple(r) for r in db.execute("SELECT analyte, value, value_text FROM lab_result")] == [("Ferritin", 50.0, "")]
    db.execute("INSERT INTO lab_result (taken_on, analyte, value_text) VALUES ('2025-01-02', 'Glukóza (moč)', 'neg')")
    connect(path).close()                                                                         # druhé otevření nesmí nic rozbít


def test_delete_event(client):
    eid = client.post("/events", json={"day": "2026-07-13", "kind": "exam", "title": "Kolonoskopie"}).json()["id"]
    assert client.delete(f"/events/{eid}").status_code == 204
    assert client.get("/events").json() == []
    assert client.delete(f"/events/{eid}").status_code == 404
    assert TestClient(app).delete("/events/1").status_code == 401
