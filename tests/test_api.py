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
from datetime import date, timedelta

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
    garmin._token_cache.clear()
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
    assert raw == 6                                                                  # 3 zdroje + aktivity, váha, fitness


def test_sync_is_idempotent(client, garmin_calls):
    client.post("/garmin/sync")
    client.post("/garmin/sync")
    assert len(client.get("/metrics", params={"metric": "steps"}).json()) == 1


def test_sync_reports_errors_without_leaking_key(client):
    garmin._token_cache.clear()
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(
        transport=httpx.MockTransport(make_garmin([], token_status=403)))
    r = client.post("/garmin/sync").json()
    app.dependency_overrides.pop(garmin.get_http, None)
    assert r["metrics_stored"] == 0 and len(r["errors"]) == 4
    assert "test-klic" not in str(r) and "HTTP 403" in r["errors"][0]


def test_sync_without_api_key_is_503(client):
    app.dependency_overrides[get_settings] = lambda: Settings(allowed_emails="ja@example.com")
    assert client.post("/garmin/sync").status_code == 503


def test_sync_falls_back_to_per_day_when_v2_returns_500(client):
    """Skutečný případ: v2/dailysummaries vrací 500, kroky a tep se dotáhnou po dnech."""
    garmin._token_cache.clear()
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
    garmin._token_cache.clear()
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
    assert r["Leukocyty (moč, sediment)"]["value"] == 4 and r["Leukocyty (moč, sediment)"]["unit"] == "elem./1u"
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
    db.commit()
    assert [r["person_id"] for r in db.execute("SELECT person_id FROM lab_result")] == [1, 1]          # stará data patří vlastníkovi
    connect(path).close()                                                                         # druhé otevření nesmí nic rozbít


def test_delete_event(client):
    eid = client.post("/events", json={"day": "2026-07-13", "kind": "exam", "title": "Kolonoskopie"}).json()["id"]
    assert client.delete(f"/events/{eid}").status_code == 204
    assert client.get("/events").json() == []
    assert client.delete(f"/events/{eid}").status_code == 404
    assert TestClient(app).delete("/events/1").status_code == 401


def test_sync_window_ends_at_given_day_and_rejects_future(client, garmin_calls):
    client.post("/garmin/sync", params={"days": 3, "end": "2026-10-10"})
    sent = [c for c in garmin_calls if "dailysummaries" in c[1]]
    assert len(sent) == 1                                                   # úsek ≤ 28 dní = jeden požadavek
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post("/garmin/sync", params={"days": 3, "end": future}).status_code == 400


def test_metrics_accepts_several_metrics(client, garmin_calls):
    client.post("/garmin/sync", params={"days": 3})
    got = {m["metric"] for m in client.get("/metrics", params=[("metric", "steps"), ("metric", "sleep_score")]).json()}
    assert got == {"steps", "sleep_score"}


# ---------- stav úložiště a záloha ----------

def test_status_counts_and_requires_login(client, garmin_calls):
    client.post("/events", json={"day": "2026-07-13", "kind": "exam", "title": "Kolonoskopie"})
    client.post("/labs", json={"taken_on": "2026-01-01", "analyte": "Urea", "value": 5.0})
    client.post("/garmin/sync", params={"days": 3})
    st = client.get("/status").json()
    assert (st["events"], st["labs"], st["lab_days"]) == (1, 1, 1)
    assert st["metric_values"] > 0 and st["last_metric_day"] and isinstance(st["persistent_disk"], bool)
    assert TestClient(app).get("/status").status_code == 401


def test_backup_is_a_complete_sqlite_copy(client, tmp_path):
    client.post("/labs", json={"taken_on": "2026-01-01", "analyte": "Urea", "value": 5.0})
    r = client.get("/backup")
    assert r.status_code == 200 and "health-" in r.headers["content-disposition"]
    assert r.content.startswith(b"SQLite format 3\x00")
    copy = tmp_path / "copy.db"; copy.write_bytes(r.content)
    import sqlite3
    assert sqlite3.connect(copy).execute("SELECT analyte, value FROM lab_result").fetchall() == [("Urea", 5.0)]
    assert TestClient(app).get("/backup").status_code == 401


# ---------- jednorázový import dat z proměnné prostředí ----------

import base64, json as _json
from app import seed


def _seed_payload():
    data = {"labs": [{"taken_on": "2026-01-01", "lab": "x", "analyte": "Urea", "value": 5.0, "unit": "mmol/L", "ref_low": 2.8, "ref_high": 8.1},
                     {"taken_on": "2026-01-01", "lab": "x", "analyte": "Glukóza (moč)", "value_text": "neg"}],
            "events": [{"day": "2026-07-13", "kind": "exam", "title": "Kolonoskopie", "note": "n"}]}
    return base64.b64encode(_json.dumps(data).encode()).decode()


def test_seed_imports_once_and_is_idempotent():
    db = connect(":memory:")
    first = seed.apply_seed(db, _seed_payload())
    assert first == {"labs_inserted": 2, "labs_skipped": 0, "events_inserted": 1, "events_skipped": 0}
    again = seed.apply_seed(db, _seed_payload())                         # restart služby s ponechanou proměnnou
    assert again == {"labs_inserted": 0, "labs_skipped": 2, "events_inserted": 0, "events_skipped": 1}
    assert db.execute("SELECT COUNT(*) FROM lab_result").fetchone()[0] == 2


def test_seed_ignores_empty_and_broken_input_without_crashing(caplog):
    db = connect(":memory:")
    assert seed.apply_seed(db, "") == {}
    for bad in ("není base64!!", base64.b64encode(b"not json").decode(), base64.b64encode(b'{"labs":[{"x":1}]}').decode()):
        assert "error" in seed.apply_seed(db, bad)
    assert db.execute("SELECT COUNT(*) FROM lab_result").fetchone()[0] == 0
    assert "Urea" not in caplog.text                                       # obsah se nikdy neloguje


def test_seed_accepts_zlib_compressed_payload_and_rejects_corruption():
    import zlib
    raw = base64.b64decode(_seed_payload())
    packed = base64.b64encode(zlib.compress(raw, 9)).decode()
    db = connect(":memory:")
    assert seed.apply_seed(db, packed)["labs_inserted"] == 2
    broken = packed[:40] + ("A" if packed[40] != "A" else "B") + packed[41:]          # jeden změněný znak
    assert "error" in seed.apply_seed(connect(":memory:"), broken)


def test_lab_interpretation_settings(client):
    assert client.get("/labs/settings").json() == {}
    assert client.put("/labs/settings", json={"analyte": "LDL cholesterol", "mode": "nizsi"}).status_code == 200
    client.put("/labs/settings", json={"analyte": "Lp(a)", "mode": "nizsi"})
    client.put("/labs/settings", json={"analyte": "LDL cholesterol", "mode": "rozmezi"})        # přepsání
    assert client.get("/labs/settings").json() == {"LDL cholesterol": "rozmezi", "Lp(a)": "nizsi"}
    assert client.put("/labs/settings", json={"analyte": "X", "mode": "hodne"}).status_code == 422   # neznámý režim
    assert TestClient(app).get("/labs/settings").status_code == 401
    assert TestClient(app).put("/labs/settings", json={"analyte": "X", "mode": "nizsi"}).status_code == 401


# ---------- osoby ----------

def test_persons_crud_and_scoping(client):
    assert client.get("/persons").json() == [{"id": 1, "name": "Já", "owner": True}]
    pid = client.post("/persons", json={"name": "Osoba B"}).json()["id"]
    assert client.post("/persons", json={"name": "osoba b"}).status_code == 409                   # jméno je jedinečné, bez ohledu na velikost
    assert client.put("/persons/1", json={"name": "Osoba A"}).json()["name"] == "Osoba A"
    assert client.put("/persons/99", json={"name": "X"}).status_code == 404
    client.post("/labs", json={"person_id": pid, "taken_on": "2026-01-07", "analyte": "TSH", "value": 0.85, "unit": "mU/L"})
    client.post("/labs", json={"taken_on": "2026-02-01", "analyte": "Urea", "value": 5.0})       # výchozí osoba = vlastník
    client.post("/events", json={"person_id": pid, "day": "2026-03-01", "kind": "exam", "title": "Prohlídka"})
    assert [r["analyte"] for r in client.get("/labs").json()] == ["Urea"]
    assert [r["analyte"] for r in client.get("/labs", params={"person_id": pid}).json()] == ["TSH"]
    assert client.get("/events").json() == [] and len(client.get("/events", params={"person_id": pid}).json()) == 1
    tl = client.get("/timeline", params={"person_id": pid}).json()
    assert [i["title"] for i in tl] == ["Krevní rozbory", "Prohlídka"]
    assert client.get("/timeline").json()[0]["count"] == 1
    assert client.get("/labs", params={"person_id": 99}).status_code == 404
    assert client.post("/labs", json={"person_id": 99, "taken_on": "2026-01-01", "analyte": "X", "value": 1}).status_code == 404
    assert TestClient(app).get("/persons").status_code == 401
    assert TestClient(app).post("/persons", json={"name": "X"}).status_code == 401


def test_same_result_for_two_people_is_not_a_duplicate(client):
    pid = client.post("/persons", json={"name": "Jana"}).json()["id"]
    item = {"taken_on": "2026-01-01", "lab": "x", "analyte": "Urea", "value": 5.0, "unit": "mmol/L"}
    assert client.post("/labs/bulk", json={"items": [{**item, "person_id": 1}, {**item, "person_id": pid}]}).json() == {"inserted": 2, "skipped": 0}
    assert client.post("/labs/bulk", json={"items": [{**item, "person_id": pid}]}).json() == {"inserted": 0, "skipped": 1}
    assert client.post("/labs/bulk", json={"items": [{**item, "person_id": 99}]}).status_code == 404


def test_status_counts_per_person(client):
    pid = client.post("/persons", json={"name": "Jana"}).json()["id"]
    client.post("/labs", json={"person_id": pid, "taken_on": "2026-01-01", "analyte": "Urea", "value": 5.0})
    st = client.get("/status").json()["persons"]
    assert {p["name"]: (p["labs"], p["lab_days"]) for p in st} == {"Já": (0, 0), "Jana": (1, 1)}


def test_seed_creates_people_and_assigns_items_by_name():
    db = connect(":memory:")
    data = {"persons": [{"name": "Osoba A", "owner": True}, {"name": "Osoba B"}],
            "labs": [{"person": "Osoba B", "taken_on": "2026-01-07", "lab": "x", "analyte": "TSH", "value": 0.85},
                     {"taken_on": "2026-01-08", "lab": "x", "analyte": "Urea", "value": 5.0}],
            "events": [{"person": "Osoba B", "day": "2026-03-01", "kind": "exam", "title": "Prohlídka"}]}
    packed = base64.b64encode(_json.dumps(data).encode()).decode()
    assert seed.apply_seed(db, packed)["labs_inserted"] == 2
    assert {r["name"]: r["id"] for r in db.execute("SELECT * FROM person")} == {"Osoba A": 1, "Osoba B": 2}
    assert [(r["person_id"], r["analyte"]) for r in db.execute("SELECT * FROM lab_result ORDER BY id")] == [(2, "TSH"), (1, "Urea")]
    assert seed.apply_seed(db, packed)["labs_inserted"] == 0                                # idempotentní i s osobami
    bad = base64.b64encode(_json.dumps({"labs": [{"person": "Nikdo", "taken_on": "2026-01-01", "analyte": "X", "value": 1}]}).encode()).decode()
    assert "error" in seed.apply_seed(connect(":memory:"), bad)


# ---------- další rozložení listů a sjednocení názvů (texty jsou VYMYŠLENÉ) ----------

TEXT_C = """Opis výsledku

Provádějící laboratoř:   123456 Nějaká Lab. - biochemie
Požadující lékař:         123456 MUDr. TEST LEKAŘ
Pacient:                   123456 TESTOVACÍ PACIENT
                                               NUMERICKÉ HODNOTY

Odběr       Vzorek            NČLP    Název                     Hodnota    Jednotka Ref.rozmezí Komentář
07.01.2026   1608/OKBH/07      03085    Urea                           4,0   mmol/L         2,8-8,1
07.01.2026   1608/OKBH/07      17339    GF CKD-EPI                   1,91    ml/s/1,73m2   1,00-3,00
07.01.2026   1608/OKBH/07      03077    Kyselina močová              445    umol/L         202-417
07.01.2026   1608/OKBH/07      02380    WBC - leukocyty                5,3   10ˆ9/L         4,0-10,0
07.01.2026   1608/OKBH/07      03048    TSH                         0,851    mU/L         0,510-4,300
07.01.2026   1608/OKBH/07      03410    pH                             6,0   1
07.01.2026   1608/OKBH/07      03137    Specifická hustota          1,009    g/cmˆ3
                                   NEFORMALIZOVANÉ TEXTOVÉ HODNOTY
07.01.2026           1608/OKBH/07      55974     Hodnocení CKD dle eGFR          G1
07.01.2026           1608/OKBH/07      20662     Krevní obraz                    (provedeno)
07.01.2026           1608/OKBH/07      03364     Glukóza                         neg
07.01.2026           1608/OKBH/07      20224     MS bez patologie                bez patologického nálezu
"""

TEXT_D = """Výsledkový list                                       Unilabs Diagnostics, k.s.
 TESTOVACÍ PACIENT                                                             lab.online
 ČP: 123456                      Datum narození: 01.01.2000
 Odběr: 17.07.2026 08:29             Přijato: 17.07.2026 08:29

  Název vyšetření                                     Výsledek         Jednotka                      Meze                       Hodnocení
  Biochemie - krev
  HAD AM   S_Kreatinin                           92                          µmol/L                   45 - 86                   |      | |*      |
  HAD      C_CKD-EPI 2021                        1,78                        ml/s/1,73 m2           1,50 - 3,00                 |      |*|       |
           Hodnocení stádia CKD dle výpočtu CKD-EPI... G1
  HAD AM  P_Glukóza                                    5,5                   mmol/L                   3,9 - 5,6                 |      |*|       |
  HAD    S_25OH vitamin D                              62,3                  nmol/L                75,0 - 250,0                 |    *| |        |
  HAD    U_Hustota                                     1009                  kg/m3                 1010 - 1030                  |    *| |        |
  HAD    U_Bílkovina                                   0                     arb.j.                    0-0                      |     |*|        |
  HAD    U_Glukóza                                     0                     arb.j.                    0-0                      |     |*|        |
  HAD    U_Leukocyty sediment                          <3                    /ul                       0 - 10                   |     |*|        |
  HAD    U_Epitelie dlaždicovité                       0-15                  /ul
  HAD    U_Hlen                                        přítomen
  HAD AM B_Hematokrit                                  0,464                                       0,400 - 0,500                |      |*|       |
  HAD AM B_MCV                                         86,6                  fl                     82,0 - 98,0                 |      |*|       |
  HAD AM B_Leukocyty                                   5,5                   10^9/L                  4,0 - 10,0                 |      |*|       |
  HAD    B_Normoblasty abs. počet                      0,000                 10^9/L                0,000 - 0,015                |      |*|       |
"""


def test_parse_euc_layout_with_row_dates_and_unit_normalization():
    p = labs.parse_lab_text(TEXT_C)
    r = by_name(p)
    assert p["taken_on"] == "2026-01-07" and p["lab"] == "Nějaká Lab. - biochemie"
    assert all(x["taken_on"] == "2026-01-07" for x in p["results"])
    assert (r["Urea"]["value"], r["Urea"]["unit"], r["Urea"]["ref_low"], r["Urea"]["ref_high"]) == (4.0, "mmol/L", 2.8, 8.1)
    assert r["GF CKD-EPI"]["unit"] == "ml/s/1,73m2" and r["GF CKD-EPI"]["ref_high"] == 3.0
    assert r["WBC - leukocyty"]["unit"] == "10^9/L"                                   # ˆ -> ^
    assert r["pH (moč)"]["unit"] == "" and r["pH (moč)"]["value"] == 6.0               # jednotka „1“ je bezrozměrná
    assert r["Specifická hustota (moč)"]["unit"] == "g/cm^3"
    assert r["TSH"]["ref_low"] == 0.51
    assert r["Hodnocení CKD dle eGFR"]["value_text"] == "G1"
    assert r["Glukóza (moč)"]["value_text"] == "neg"
    assert r["MS bez patologie"]["value_text"] == "bez patologického nálezu"
    assert "Krevní obraz" not in r and p["unparsed_rows"] == []                         # „(provedeno)“ není výsledek
    assert "TESTOVAC" not in str(p) and "123456" not in str(p) and "LEKAŘ" not in str(p)


def test_parse_unilabs_layout_maps_to_common_names_and_units():
    p = labs.parse_lab_text(TEXT_D)
    r = by_name(p)
    assert p["taken_on"] == "2026-07-17" and p["lab"] == "Unilabs Diagnostics, k.s."
    assert (r["Kreatinin"]["value"], r["Kreatinin"]["unit"], r["Kreatinin"]["ref_low"], r["Kreatinin"]["ref_high"]) == (92.0, "umol/L", 45.0, 86.0)
    assert r["GF CKD-EPI"]["unit"] == "ml/s/1,73m2"
    assert r["Hodnocení CKD dle eGFR"]["value_text"] == "G1"
    assert r["Glukóza"]["value"] == 5.5 and r["Glukóza (moč)"]["value_text"] == "neg"      # krev a moč se nepletou, 0 na proužku = neg
    assert r["25-OH vitamin D"]["value"] == 62.3
    assert (r["Specifická hustota (moč)"]["value"], r["Specifická hustota (moč)"]["unit"], r["Specifická hustota (moč)"]["ref_low"]) == (1.009, "g/cm^3", 1.01)
    assert r["Bílkovina (moč)"]["value_text"] == "neg" and r["Bílkovina (moč)"]["ref_low"] is None
    lk = r["Leukocyty (moč, sediment)"]
    assert (lk["value"], lk["note"], lk["unit"]) == (3.0, "<", "/ul")                        # „<3“ se nesmí změnit na přesné 3
    assert r["Epitelie dlaždicovité (moč, sediment)"]["value_text"] == "0-15"
    assert r["Hlen (moč, sediment)"]["value_text"] == "přítomen"
    assert r["HCT - hematokrit"]["unit"] == "" and r["MCV-stř.obj.ery"]["unit"] == "fL"
    assert r["WBC - leukocyty"]["value"] == 5.5 and "Normoblasty abs. počet" in r
    assert "TESTOVAC" not in str(p) and "123456" not in str(p)


def test_names_from_different_labs_match_despite_unicode_forms():
    import unicodedata
    decomposed = unicodedata.normalize("NFD", TEXT_C)                                         # ř = r + háček apod.
    assert "Kyselina močová" in by_name(labs.parse_lab_text(decomposed))                      # název je vždy v NFC
    c = {k for k in by_name(labs.parse_lab_text(TEXT_C))}
    d = {k for k in by_name(labs.parse_lab_text(TEXT_D))}
    assert {"GF CKD-EPI", "Hodnocení CKD dle eGFR", "WBC - leukocyty", "Glukóza (moč)", "Specifická hustota (moč)"} <= (c & d)


# ---------- automatická synchronizace ----------

from datetime import datetime, timedelta, timezone
from app import autosync


def _auto_settings(**kw):
    return Settings(garmin_api_key="test-klic", auto_sync_hours=6, auto_sync_days=7, allowed_emails="ja@example.com", **kw)


def _factory(calls, **kw):
    return lambda: httpx.Client(transport=httpx.MockTransport(make_garmin(calls, **kw)))


def test_is_due_rules():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    h = timedelta(hours=1)
    assert autosync.is_due(None, None, now, 6)                                   # ještě nikdy neběžela
    assert not autosync.is_due(now - 5 * h, now - 5 * h, now, 6)                 # úspěšná před 5 h, interval 6 h
    assert autosync.is_due(now - 7 * h, now - 7 * h, now, 6)
    assert not autosync.is_due(None, now - timedelta(minutes=10), now, 6)        # po selhání ne dřív než za 30 min
    assert autosync.is_due(None, now - timedelta(minutes=31), now, 6)
    assert not autosync.is_due(now - 1 * h, now - 40 * h, now, 6)


def test_auto_sync_runs_when_due_records_it_and_waits_for_next_interval():
    garmin._token_cache.clear()
    db, calls = connect(":memory:"), []
    r = autosync.maybe_run(db, _auto_settings(), http_factory=_factory(calls))
    assert r["metrics_stored"] > 0 and r["errors"] == []
    run = db.execute("SELECT * FROM sync_run").fetchone()
    assert (run["source"], run["ok"], run["days"], run["error"]) == ("auto", 1, 8, "")
    n = len(calls)
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory(calls)) is None    # hned po tom není na řadě
    assert len(calls) == n                                                                    # a nevolá se Garmin
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert autosync.maybe_run(db, _auto_settings(), now=later, http_factory=_factory(calls)) is not None
    assert db.execute("SELECT COUNT(*) FROM sync_run").fetchone()[0] == 2


def test_auto_sync_failure_is_recorded_without_secrets_and_retried_later():
    garmin._token_cache.clear()
    db = connect(":memory:")
    r = autosync.maybe_run(db, _auto_settings(), http_factory=_factory([], token_status=403))
    assert r["metrics_stored"] == 0
    run = db.execute("SELECT * FROM sync_run").fetchone()
    assert run["ok"] == 0 and "HTTP 403" in run["error"] and "test-klic" not in run["error"]
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory([])) is None                   # počká 30 minut
    soon = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert autosync.maybe_run(db, _auto_settings(), now=soon, http_factory=_factory([], token_status=403)) is not None


def test_auto_sync_disabled_or_without_key_does_nothing():
    db = connect(":memory:")
    assert autosync.maybe_run(db, Settings(garmin_api_key="k", auto_sync_hours=0)) is None
    assert autosync.maybe_run(db, Settings(garmin_api_key="", auto_sync_hours=6)) is None
    assert db.execute("SELECT COUNT(*) FROM sync_run").fetchone()[0] == 0


def test_garmin_status_reports_auto_and_manual_runs(client, garmin_calls):
    assert client.get("/garmin/status").json()["last_auto"] is None
    client.post("/garmin/sync", params={"days": 3})                                         # ruční běh se zaznamená jako manual
    st = client.get("/garmin/status").json()
    assert st["key_configured"] is True and st["auto"] == {"enabled": True, "interval_hours": 6, "days": 7}
    assert st["last_auto"] is None and st["last_success"]["source"] == "manual" and st["last_success"]["ok"] is True
    assert TestClient(app).get("/garmin/status").status_code == 401


def test_manual_sync_waits_for_running_one_then_gives_409(client, garmin_calls, monkeypatch):
    monkeypatch.setattr(garmin, "LOCK_TIMEOUT", 0.05)
    with garmin.SYNC_LOCK:                                                                  # simuluje běžící automatickou synchronizaci
        assert client.post("/garmin/sync", params={"days": 3}).status_code == 409
        assert autosync.maybe_run(connect(":memory:"), _auto_settings(), http_factory=_factory([])) is None
    assert client.post("/garmin/sync", params={"days": 3}).status_code == 200                # po uvolnění zase funguje


def test_scheduler_exits_quietly_when_disabled():
    import asyncio
    asyncio.run(autosync.scheduler(Settings(auto_sync_hours=0)))
    asyncio.run(autosync.scheduler(Settings(auto_sync_hours=6, garmin_api_key="")))


# ---------- všechny metriky z Garminu: plošné rozbalení, aktivity, váha, fitness věk (data VYMYŠLENÁ, tvar podle specifikace) ----------

DAILY_FULL = {"data": [{
    "calendarDate": "2026-11-14", "uuid": "abc", "userProfilePk": 123, "includesWellnessData": True,
    "wellnessChronology": {"totalDurationInMillis": 86400000, "startTimestampGmt": "2026-11-13T23:00:00.0"},
    "movement": {"steps": {"goal": 10000, "value": 9000, "distanceInMeters": 6500.5},
                 "floorsAscended": {"goal": 10, "value": 12, "distanceInMeters": 36.0}},
    "heartRate": {"restingValue": 52, "minValue": 45, "maxValue": 160, "alertCount": 0},
    "stress": {"avgLevel": 25, "qualifier": "BALANCED", "lowStressProportion": 0.4, "totalDurationInMillis": 50000000},
    "bodyBattery": {"maxValue": 90, "chargedValue": 55, "drainedValue": 60, "featureVersion": "3.0",
                    "dynamicFeedbackEvent": {"eventTimestampGmt": "2026-11-14T05:00:00.0", "bodyBatteryLevel": "HIGH"}},
    "hydration": {"goalInMl": 2500, "valueInMl": 1800},
}]}
SLEEP_FULL = [{
    "calendarDate": "2026-11-14", "sleepTimeSeconds": 27000, "deepSleepSeconds": 5400, "averageSpO2Value": 95.5,
    "lowestRespirationValue": 12.0, "awakeCount": 2, "avgSleepStress": 18.2, "sleepWindowConfirmed": True,
    "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}, "totalDuration": {"value": 90, "qualifierKey": "EXCELLENT", "optimalStart": 7.0}},
    "sleepNeed": {"baseline": 480, "actual": 470, "displayedForTheDay": True, "recommendedBedtimeStartMins": 1320},
    "dailyNapDTOS": [{"napTimeSec": 600}], "sleepStartTimestampGMT": "2026-11-13T21:00:00.0",
}]
HRV_FULL = {"hrvSummaries": [{"calendarDate": "2026-11-14", "lastNightAvg": 48, "weeklyAvg": 50, "status": "BALANCED",
                              "baseline": {"lowUpper": 40, "balancedLow": 42, "balancedUpper": 58, "markerValue": 0.5}}]}


def _activity(i, day="2026-11-14", **kw):
    return {"activityId": 9000 + i, "activityName": f"Běh {i}", "startTimeLocal": f"{day} 0{i % 9}:30:00", "startTimeGMT": f"{day} 0{i % 9}:30:00",
            "activityType": {"typeId": 1, "typeKey": "running"}, "distance": 5000.0 + i, "duration": 1800.0 + i, "calories": 400.0,
            "averageHR": 150.0, "maxHR": 175.0, "vO2MaxValue": 50.0 + i, "aerobicTrainingEffect": 3.1,
            "startLatitude": 50.1, "startLongitude": 14.4, "ownerFullName": "Nějaká Osoba", "hasPolyline": True, **kw}


def make_full(calls, activities=None, weight_status=200, fitness_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path, dict(request.url.params)))
        if path.endswith("/personal-api-key/token"):
            return httpx.Response(200, json={"access_token": "tok"})
        if "dailysummaries" in path:
            return httpx.Response(200, json=DAILY_FULL)
        if "dailySleepSummariesByDate" in path:
            return httpx.Response(200, json=SLEEP_FULL)
        if "/hrv-service/" in path:
            return httpx.Response(200, json=HRV_FULL)
        if "activities/search" in path:
            acts = activities if activities is not None else [_activity(1), _activity(2)]
            off = int(request.url.params.get("start", 0)); lim = int(request.url.params.get("limit", 100))
            return httpx.Response(200, json=acts[off:off + lim])
        if "/weight-service/" in path:
            if weight_status != 200:
                return httpx.Response(weight_status)
            return httpx.Response(200, json={"dailyWeightSummaries": [{"summaryDate": "2026-11-14", "latestWeight": {
                "weight": 82500.0, "bmi": 24.1, "bodyFat": 18.5, "boneMass": 3300, "muscleMass": 38000}}]})
        if "/fitnessage-service/" in path:
            if fitness_status != 200:
                return httpx.Response(fitness_status)
            return httpx.Response(200, json=[{"fitnessAge": 35.0, "chronologicalAge": 42, "achievableFitnessAge": 30.5, "invalidReason": None}])
        return httpx.Response(404)
    return handler


@pytest.fixture
def full_garmin():
    garmin._token_cache.clear()
    calls = []
    def use(**kw):
        app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(transport=httpx.MockTransport(make_full(calls, **kw)))
        return calls
    use()
    yield use
    app.dependency_overrides.pop(garmin.get_http, None)


def _metrics(client, **params):
    return {m["metric"]: m for m in client.get("/metrics", params=params).json()}


def test_every_numeric_field_is_stored_but_not_ids_timestamps_or_text(client, full_garmin):
    r = client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"}).json()
    assert r["errors"] == []
    m = _metrics(client)
    assert m["daily.movement.steps.goal"]["value"] == 10000 and m["daily.movement.steps.distanceInMeters"]["unit"] == "m"
    assert m["daily.movement.floorsAscended.value"]["value"] == 12
    assert m["daily.stress.lowStressProportion"]["unit"] == "ratio" and m["daily.stress.totalDurationInMillis"]["unit"] == "ms"
    assert m["daily.bodyBattery.chargedValue"]["value"] == 55 and m["daily.hydration.valueInMl"]["unit"] == "ml"
    assert m["daily.wellnessChronology.totalDurationInMillis"]["value"] == 86400000
    assert m["sleep.averageSpO2Value"]["value"] == 95.5 and m["sleep.sleepScores.totalDuration.value"]["value"] == 90
    assert m["sleep.sleepNeed.baseline"]["value"] == 480 and m["sleep.awakeCount"]["value"] == 2
    assert m["hrv.baseline.balancedLow"]["value"] == 42
    for nepatri in ("daily.userProfilePk", "daily.uuid", "daily.includesWellnessData", "daily.stress.qualifier", "sleep.sleepWindowConfirmed",
                    "daily.bodyBattery.dynamicFeedbackEvent.bodyBatteryLevel", "daily.bodyBattery.featureVersion",
                    "sleep.sleepNeed.displayedForTheDay", "hrv.status"):
        assert nepatri not in m, nepatri
    assert not [k for k in m if "imestamp" in k or "Nap" in k]                              # časové značky a seznamy se neukládají


def test_curated_fields_are_not_duplicated(client, full_garmin):
    client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"})
    m = _metrics(client)
    assert m["steps"]["value"] == 9000 and m["sleep_score"]["value"] == 81 and m["hrv_last_night_avg"]["value"] == 48
    for dup in ("daily.movement.steps.value", "daily.heartRate.restingValue", "sleep.sleepTimeSeconds", "sleep.sleepScores.overall.value", "hrv.lastNightAvg"):
        assert dup not in m, dup


def test_activities_are_stored_once_without_gps_and_summarised_per_day(client, full_garmin):
    client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"})
    client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"})                    # opakování nic nezdvojí
    acts = client.get("/garmin/activities").json()
    assert [a["activity_id"] for a in acts] == [9002, 9001]                                   # nejnovější první
    assert len(acts) == 2 and acts[0]["type_key"] == "running" and acts[0]["avg_hr"] == 150.0
    m = _metrics(client)
    assert m["activity.count"]["value"] == 2 and m["activity.duration_s"]["value"] == 1801 + 1802
    assert m["activity.vo2max"]["value"] == 52.0 and m["activity.distance_m"]["unit"] == "m"
    raw = client.get(f"/garmin/activities/{acts[0]['activity_id']}").json()["raw"]
    assert raw["activityName"].startswith("Běh") and "startLatitude" not in raw and "ownerFullName" not in raw   # poloha a vlastník se neukládají
    assert client.get("/garmin/activities/1").status_code == 404
    assert client.get("/garmin/activities", params={"from": "2026-11-15"}).json() == []
    assert TestClient(app).get("/garmin/activities").status_code == 401


def test_activities_are_paged_beyond_100(client, full_garmin):
    full_garmin(activities=[_activity(i, day="2026-11-1%d" % (i % 4)) for i in range(1, 106)])
    client.post("/garmin/sync", params={"days": 10, "end": "2026-11-14"})
    assert len(client.get("/garmin/activities", params={"limit": 1000}).json()) == 105


def test_weight_and_fitness_age(client, full_garmin):
    client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"})
    m = _metrics(client)
    assert m["body.weight_kg"]["value"] == 82.5 and m["body.muscle_mass_kg"]["value"] == 38.0 and m["body.bone_mass_kg"]["value"] == 3.3   # gramy -> kg
    assert m["body.fat_pct"]["value"] == 18.5 and m["body.bmi"]["unit"] == ""
    assert m["fitness.age"]["value"] == 35.0 and m["fitness.chronological_age"]["value"] == 42 and m["fitness.age"]["day"] == "2026-11-14"


def test_missing_optional_sources_do_not_fail_the_sync(client, full_garmin):
    full_garmin(weight_status=404, fitness_status=404)                                       # např. nemáš chytrou váhu
    r = client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"}).json()
    assert r["errors"] == [] and "skipped" in r["by_type"]["weight"] and "skipped" in r["by_type"]["fitness"]
    assert client.get("/garmin/status").json()["last_success"]["ok"] is True
    assert "steps" in _metrics(client)


def test_reprocess_raw_applies_new_rules_to_downloaded_data_once():
    db = connect(":memory:")
    import json as j
    for t, payload in (("dailysummaries", {"data": [{**DAILY_FULL["data"][0], "movement": {"steps": {"value": 1}}}]}),    # starší stažení
                       ("dailysummaries", DAILY_FULL), ("sleep", SLEEP_FULL), ("hrv", HRV_FULL), ("weight", {"x": 1})):
        db.execute("INSERT INTO garmin_raw (summary_type, payload) VALUES (?, ?)", (t, j.dumps(payload)))
    db.commit()
    n = garmin.reprocess_if_needed(db)
    assert n and n > 20
    got = {r["metric"]: r["value"] for r in db.execute("SELECT metric, value FROM metric_daily")}
    assert got["steps"] == 9000 and got["daily.hydration.valueInMl"] == 1800 and got["sleep.sleepNeed.baseline"] == 480   # pozdější stažení vyhrává
    assert garmin.reprocess_if_needed(db) is None                                            # podruhé se nic nepřepočítává


def test_metric_catalog(client, full_garmin):
    client.post("/garmin/sync", params={"days": 3, "end": "2026-11-14"})
    cat = {c["metric"]: c for c in client.get("/metrics/catalog").json()}
    h = cat["daily.hydration.valueInMl"]
    assert (h["unit"], h["days"], h["first_day"], h["last_day"], h["min"], h["max"], h["last"]) == ("ml", 1, "2026-11-14", "2026-11-14", 1800, 1800, 1800)
    assert "steps" in cat and "activity.count" in cat and len(cat) > 40
    assert TestClient(app).get("/metrics/catalog").status_code == 401


def test_prefs_roundtrip_and_validation(client):
    assert client.get("/prefs").json() == {}
    assert client.put("/prefs/pripnute", json={"value": ["steps", "daily.hydration.valueInMl"]}).status_code == 200
    client.put("/prefs/pripnute", json={"value": ["steps"]})                                  # přepsání
    assert client.get("/prefs").json() == {"pripnute": ["steps"]}
    assert client.put("/prefs/ŠPATNÝ KLÍČ", json={"value": 1}).status_code == 422
    assert client.put("/prefs/velke", json={"value": "x" * 25000}).status_code == 413
    assert TestClient(app).get("/prefs").status_code == 401 and TestClient(app).put("/prefs/a", json={"value": 1}).status_code == 401


def test_first_run_after_new_sources_happens_immediately_even_if_recent_success():
    garmin._token_cache.clear()
    db, calls = connect(":memory:"), []
    garmin.record_run(db, garmin.now_iso(), "auto", True, 8, 100, "")                       # před chvílí úspěšný běh starého typu
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory(calls)) is not None   # přesto proběhne, kvůli novým zdrojům
    assert db.execute("SELECT 1 FROM meta WHERE key = ?", (autosync.SOURCES_FLAG,)).fetchone()
    n = len(calls)
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory(calls)) is None    # a od teď zase jen po intervalu
    assert len(calls) == n


def test_first_run_flag_is_not_set_after_failure_and_retry_waits_30_minutes():
    garmin._token_cache.clear()
    db = connect(":memory:")
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory([], token_status=403)) is not None
    assert db.execute("SELECT 1 FROM meta WHERE key = ?", (autosync.SOURCES_FLAG,)).fetchone() is None
    assert autosync.maybe_run(db, _auto_settings(), http_factory=_factory([], token_status=403)) is None      # počká
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert autosync.maybe_run(db, _auto_settings(), now=later, http_factory=_factory([])) is not None
    assert db.execute("SELECT 1 FROM meta WHERE key = ?", (autosync.SOURCES_FLAG,)).fetchone()


# ---------- Garmin pro víc osob: vlastní klíč, data, aktivity a stav ----------

def _two_people_handler(seen):
    import copy
    def handler(request: httpx.Request) -> httpx.Response:
        auth, path = request.headers.get("authorization", ""), request.url.path
        if path.endswith("/personal-api-key/token"):
            seen.append(auth)
            return httpx.Response(200, json={"access_token": "T-" + auth.split()[-1]})
        lukas = auth.endswith("klic-druheho")
        if "dailysummaries" in path:
            d = copy.deepcopy(DAILY_FULL)
            d["data"][0]["movement"]["steps"]["value"] = 111 if lukas else 9000
            return httpx.Response(200, json=d)
        if "activities/search" in path:
            return httpx.Response(200, json=[_activity(5)] if lukas else [_activity(1)])
        if "dailySleepSummariesByDate" in path or "/fitnessage-service/" in path:
            return httpx.Response(200, json=[])
        if "/hrv-service/" in path:
            return httpx.Response(200, json={"hrvSummaries": []})
        if "/weight-service/" in path:
            return httpx.Response(200, json={"dailyWeightSummaries": []})
        return httpx.Response(404)
    return handler


def test_each_person_has_own_garmin_key_data_activities_and_status(client, monkeypatch):
    garmin._token_cache.clear()
    monkeypatch.setenv("GARMIN_API_KEY_2", "klic-druheho")
    seen = []
    app.dependency_overrides[garmin.get_http] = lambda: httpx.Client(transport=httpx.MockTransport(_two_people_handler(seen)))
    pid = client.post("/persons", json={"name": "Osoba C"}).json()["id"]
    assert pid == 2
    for person in (1, 2):
        r = client.post("/garmin/sync", params={"person_id": person, "days": 3, "end": "2026-11-14"}).json()
        assert r["errors"] == [], (person, r["errors"])
    assert sorted(seen) == ["ApiKey klic-druheho", "ApiKey test-klic"]                         # každá osoba se přihlásila svým klíčem, tokeny se nemíchají
    steps = lambda **q: [m["value"] for m in client.get("/metrics", params={"metric": "steps", **q}).json()]
    assert steps() == [9000.0] and steps(person_id=1) == [9000.0] and steps(person_id=2) == [111.0]
    assert [a["activity_id"] for a in client.get("/garmin/activities", params={"person_id": 1}).json()] == [9001]
    assert [a["activity_id"] for a in client.get("/garmin/activities", params={"person_id": 2}).json()] == [9005]
    c1 = {c["metric"] for c in client.get("/metrics/catalog").json()}
    c2 = {c["metric"]: c for c in client.get("/metrics/catalog", params={"person_id": 2}).json()}
    assert "steps" in c1 and c2["steps"]["last"] == 111 and c2["activity.count"]["days"] == 1
    s1, s2 = client.get("/garmin/status").json(), client.get("/garmin/status", params={"person_id": 2}).json()
    assert (s1["key_configured"], s1["key_variable"]) == (True, "GARMIN_API_KEY") and (s2["key_configured"], s2["key_variable"]) == (True, "GARMIN_API_KEY_2")
    assert s1["last_success"]["ok"] is True and s2["last_success"]["ok"] is True
    assert client.get("/status").json()["persons"][1]["metric_days"] >= 1


def test_person_without_key_gets_clear_message_and_unknown_person_is_404(client):
    pid = client.post("/persons", json={"name": "Osoba D"}).json()["id"]
    r = client.post("/garmin/sync", params={"person_id": pid})
    assert r.status_code == 503 and f"GARMIN_API_KEY_{pid}" in r.json()["detail"]
    st = client.get("/garmin/status", params={"person_id": pid}).json()
    assert st["key_configured"] is False and st["key_variable"] == f"GARMIN_API_KEY_{pid}"
    for url in ("/metrics", "/metrics/catalog", "/garmin/status", "/garmin/activities"):
        assert client.get(url, params={"person_id": 99}).status_code == 404, url
    assert client.post("/garmin/sync", params={"person_id": 99}).status_code == 404


def test_auto_sync_runs_per_person_with_own_key(monkeypatch):
    garmin._token_cache.clear()
    monkeypatch.setenv("GARMIN_API_KEY_2", "klic-druheho")
    db, seen = connect(":memory:"), []
    db.execute("INSERT INTO person (name) VALUES ('Osoba C')"); db.commit()
    factory = lambda: httpx.Client(transport=httpx.MockTransport(_two_people_handler(seen)))
    assert autosync.maybe_run(db, _auto_settings(), http_factory=factory, person_id=2) is not None
    assert seen == ["ApiKey klic-druheho"]
    assert [r["person_id"] for r in db.execute("SELECT person_id FROM sync_run")] == [2]
    assert autosync.maybe_run(db, _auto_settings(), http_factory=factory, person_id=2) is None          # není na řadě
    assert autosync.maybe_run(db, _auto_settings(), http_factory=factory, person_id=3) is None          # osoba bez klíče se přeskočí
    assert db.execute("SELECT COUNT(*) FROM metric_daily WHERE person_id = 1").fetchone()[0] == 0


def test_old_garmin_tables_are_migrated_to_the_owner(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE metric_daily (source TEXT NOT NULL, metric TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL,
                                   unit TEXT NOT NULL DEFAULT '', PRIMARY KEY (source, metric, day));
        INSERT INTO metric_daily VALUES ('garmin', 'steps', '2026-11-14', 9000, 'count');
        CREATE TABLE garmin_raw (id INTEGER PRIMARY KEY, received_at TEXT NOT NULL DEFAULT (datetime('now')), summary_type TEXT NOT NULL, payload TEXT NOT NULL);
        INSERT INTO garmin_raw (summary_type, payload) VALUES ('sleep', '[]');
        CREATE TABLE sync_run (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, source TEXT NOT NULL, ok INTEGER NOT NULL,
                               days INTEGER NOT NULL, metrics_stored INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '');
        INSERT INTO sync_run VALUES (1, '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:05+00:00', 'auto', 1, 8, 5, '');
    """)
    old.commit(); old.close()
    db = connect(path)
    assert [tuple(r) for r in db.execute("SELECT person_id, metric, value FROM metric_daily")] == [(1, "steps", 9000.0)]
    assert db.execute("SELECT person_id FROM garmin_raw").fetchone()[0] == 1 and db.execute("SELECT person_id FROM sync_run").fetchone()[0] == 1
    garmin._upsert_metric(db, 2, ("garmin", "steps", "2026-11-14", 111.0, "count"))                    # klíč už zahrnuje osobu
    garmin._upsert_metric(db, 1, ("garmin", "steps", "2026-11-14", 9500.0, "count"))
    db.commit()
    assert {r["person_id"]: r["value"] for r in db.execute("SELECT person_id, value FROM metric_daily")} == {1: 9500.0, 2: 111.0}
    connect(path).close()                                                                                # druhé otevření nic nerozbije
