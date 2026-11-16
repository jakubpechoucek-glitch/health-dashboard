# Zdravotní přehled

Osobní webová aplikace, která na jedné časové ose spojuje data z Garminu, krevní rozbory,
lékařská vyšetření (např. kolonoskopii) a genetiku. **Jen pro jednoho uživatele**, chráněné přihlášením přes Google (stejné jako na Rozcestníku). Nejde o zdravotnický prostředek a nedává diagnózy ani doporučení.

## Stack

Python 3.11+, FastAPI, SQLite (stdlib `sqlite3`), pytest. Frontend je jedna statická stránka (`app/static/index.html`) s vlastními SVG grafy bez externích knihoven: každá metrika má vlastní malý graf, najetí myší nebo šipky na klávesnici ukážou hodnotu dne, k dispozici je i tabulka.

## Rychlý start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # doplň ALLOWED_EMAILS a GARMIN_API_KEY
pre-commit install              # gitleaks + zákaz commitu dat
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dokumentace API (Swagger): <http://127.0.0.1:8000/docs>

## Endpointy

| Metoda | Cesta | K čemu |
|---|---|---|
| POST | `/garmin/sync?days=7` | ručně stáhne data z Garmin Personal API (pull) |
| GET | `/metrics/catalog` | přehled všech uložených metrik (dny, rozsah, poslední hodnota) |
| GET | `/garmin/activities`, `/garmin/activities/{id}` | aktivity z hodinek a všechna jejich pole |
| GET/PUT | `/prefs`, `/prefs/{klíč}` | nastavení rozhraní (připnuté metriky) |
| POST | `/garmin/sync?person_id=` | Garmin lze stahovat za každou osobu s vlastním klíčem (`GARMIN_API_KEY_<id>`) |
| GET | `/garmin/status` | stav automatické synchronizace (poslední běhy) |
| GET | `/metrics?metric=&from=&to=` | denní metriky (kroky, klidový tep, spánek…) |
| POST/GET | `/labs` | krevní rozbory (ruční zadání, výpis) |
| POST | `/labs/import/preview`, `/labs/bulk` | import rozborů z PDF s náhledem |
| POST/GET, DELETE `/events/{id}` | `/events` | události (vyšetření, výsledek genetiky, změna léků) |
| GET | `/timeline` | události + rozbory seřazené v čase, s příznakem mimo referenční rozsah |

| GET/POST/PUT | `/persons`, `/persons/{id}` | osoby (vlastník je osoba 1); `/labs`, `/events`, `/timeline` berou `person_id` |
| GET | `/status` | stav úložiště: zda je databáze na trvalém disku, počty záznamů |
| GET | `/backup` | konzistentní kopie celé databáze (SQLite) ke stažení |

## Dokumentace

- [Architektura a datový model](docs/architecture.md)
- [Garmin Health API](docs/garmin.md)
- [Soukromí a bezpečnost](docs/privacy.md)
- [Krevní rozbory a import z PDF](docs/labs.md)
- [Přihlášení](docs/auth.md)
- [Nasazení na Render](docs/deploy-render.md)
- [Plán](docs/roadmap.md)

## Pravidla

- Do gitu nepatří **žádná zdravotní data ani klíče** (`data/`, `.env`, `*.db`, `*.fit`, `*.pdf`).
  Hlídá to `.gitignore` a pre-commit.
- Testy a vývoj jen s vymyšlenými daty.
