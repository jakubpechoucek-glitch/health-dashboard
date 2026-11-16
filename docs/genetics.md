# Genetika

Záložka **Genetika** ukazuje výsledky genetických vyšetření vybrané osoby. Ukládá se přepis zprávy laboratoře,
ne surová data DNA ani PDF (viz [privacy.md](privacy.md)).

## Co záložka ukazuje

- **Souhrnná lékařská zpráva** klinického genetika: osobní a rodinná anamnéza, lékařský komentář k výsledkům,
  doporučená vyšetření, suplementace a omezení vyšetření. Každá část je rozbalovací.
- **Jednotlivá vyšetření** seskupená podle oblastí zprávy (Imunogenetika, Neurogenetika, Kardiogenetika, Osteoporóza,
  Onkogenetika) a skupin (např. Hypertenze). U každého: vyšetřované geny, úroveň, výsledek, rozbalovací
  **Komentář a doporučení** a **O vyšetření** (popis z laboratorní zprávy). Úvodní texty oblastí a skupin jsou také rozbalovací.
- **Dlaždice-filtry**: všechna vyšetření / mírně zvýšené riziko / bez zvýšeného rizika / informace. Tlačítko **Rozbalit vše**.

Úroveň se odvozuje z formulace laboratoře: „mírně zvyšuje riziko“ → *mírně zvýšené riziko*, „riziko se nemění“ nebo
populační riziko → *bez zvýšeného rizika*, výsledek, který se týká jen životosprávy (např. přeměna omega-3) → *informace*.
Je to orientační třídění pro přehled, ne lékařský závěr.

## Data

Tabulka `genetic_item`: jedna řádka = jedno vyšetření (`kind = vysetreni`), úvodní text oblasti či skupiny (`uvod`)
nebo část souhrnné zprávy (`souhrn`). Zpráva je určená osobou, laboratoří a datem. Text odstavců je oddělený prázdným
řádkem, odrážky začínají „• “ (UI je vykreslí jako seznam).

| Metoda | Cesta | Popis |
|---|---|---|
| GET | `/genetics?person_id=` | všechny položky osoby v pořadí zprávy |
| POST | `/genetics/bulk` | `{"items": [...]}`, max 500; stejná položka téže zprávy se přepíše novým textem |

Import přes `SEED_JSON_B64`: klíč `genetics` se stejnými poli jako `POST /genetics/bulk`, osobu lze zadat jménem (`person`).
