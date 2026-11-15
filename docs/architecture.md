# Architektura

```
Garmin Health API ──push──► /garmin/webhook/{token} ─┐
ruční zadání / import PDF ─► /labs, /events ─────────┼─► SQLite (data/health.db) ─► /timeline, /metrics ─► UI
```

## Princip

1. **Surová data se ukládají vždy** (`garmin_raw`). Normalizace je jen pohled nad nimi, takže
   chybu v mapování polí jde opravit a data přepočítat.
2. **Denní granularita.** Všechno se redukuje na `(zdroj, metrika, den) → hodnota`. Je to
   dost pro dlouhodobé trendy a zároveň jednoduché. Detailní řady (tep v čase, spánkové fáze)
   se případně přidají zvlášť.
3. **Idempotence.** Garmin posílá aktualizace téhož dne opakovaně, zápis je `UPSERT`.

## Tabulky

| Tabulka | Obsah |
|---|---|
| `garmin_raw` | přijaté payloady beze změny |
| `metric_daily` | `source, metric, day, value, unit` (PK `source, metric, day`) |
| `lab_result` | rozbory: analyt, hodnota, jednotka, referenční rozsah, laboratoř |
| `health_event` | události na časové ose (`exam`, `genetics`, `medication`, `other`) |

Genetika zatím existuje jako událost na časové ose (datum výsledku, odkaz na lokální soubor).
Strukturovaná data variant přijdou, až bude jasný formát výsledku.

## Konvence

- Datum vždy `YYYY-MM-DD`, jednotky se ukládají u hodnoty, nepřepočítávají se potichu.
- Názvy analytů zatím volný text. Později slovník s aliasy (různé laboratoře píšou různě).
