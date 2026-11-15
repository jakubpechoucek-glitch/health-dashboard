# Zdravotní přehled

Osobní webová aplikace, která na jedné časové ose spojuje data z Garminu, krevní rozbory,
lékařská vyšetření (např. kolonoskopii) a genetiku. **Jen pro jednoho uživatele**, chráněné přihlášením (`APP_USER` / `APP_PASSWORD`). Nejde o zdravotnický prostředek a nedává diagnózy ani doporučení.

## Stack

Python 3.11+, FastAPI, SQLite (stdlib `sqlite3`), pytest. Frontend (HTMX + Plotly/ECharts) přijde v další etapě.

## Rychlý start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # doplň GARMIN_WEBHOOK_TOKEN
pre-commit install              # gitleaks + zákaz commitu dat
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dokumentace API (Swagger): <http://127.0.0.1:8000/docs>

## Endpointy

| Metoda | Cesta | K čemu |
|---|---|---|
| POST | `/garmin/webhook/{token}` | příjem push dat z Garmin Health API |
| GET | `/metrics?metric=&from=&to=` | denní metriky (kroky, klidový tep, spánek…) |
| POST/GET | `/labs` | krevní rozbory |
| POST/GET | `/events` | události (vyšetření, výsledek genetiky, změna léků) |
| GET | `/timeline` | události + rozbory seřazené v čase, s příznakem mimo referenční rozsah |

## Dokumentace

- [Architektura a datový model](docs/architecture.md)
- [Garmin Health API](docs/garmin.md)
- [Soukromí a bezpečnost](docs/privacy.md)
- [Nasazení na Render](docs/deploy-render.md)
- [Plán](docs/roadmap.md)

## Pravidla

- Do gitu nepatří **žádná zdravotní data ani klíče** (`data/`, `.env`, `*.db`, `*.fit`, `*.pdf`).
  Hlídá to `.gitignore` a pre-commit.
- Testy a vývoj jen s vymyšlenými daty.
