# Krevní rozbory

## Import z PDF

V sekci **Krevní rozbory** nahraješ PDF výsledkového listu. Server z něj vyčte hodnoty, jednotky a
referenční meze a ukáže **náhled**. Uloží se až po tvém potvrzení. PDF se nikam neukládá.

- Čtení textu zajišťuje `pdftotext -layout` (balíček `poppler-utils`, je v `Dockerfile`).
- Podporovaná jsou dvě rozložení laboratorního systému SmartMEDIX: „Výsledkový list“ (hodnota, pak meze)
  a „Laboratorní výsledky pacienta“ (meze, pak hodnota). Parser je v `app/labs.py`.
- Čte se jen **datum odběru a řádky s výsledky**. Jméno pacienta, číslo pojištěnce ani jiné identifikátory
  se nezpracovávají ani neukládají.
- Řádky, které parser nepřečte (např. textová poznámka bez hodnoty), se v náhledu vypíšou.
- Opakovaný import stejného listu nic nezdvojí (přeskočí se položky se stejným dnem, analytem a hodnotou).

## Pojmenování analytů

Stejné názvy se v krvi a v moči opakují (Glukóza, Leukocyty). Moč proto dostane příponu, např.
`Glukóza (moč)` nebo `Leukocyty (moč, sediment)`. Přiřazení je podle kódu zkoušky v tabulce `MOC` v `app/labs.py`.
Pokud laboratoř použije neznámý kód, stejný název se v rámci jednoho listu očísluje `(2)`, nic se nepřepíše.

## Endpointy

| Metoda | Cesta | Popis |
|---|---|---|
| POST | `/labs/import/preview` | tělo = PDF (`Content-Type: application/pdf`, max 5 MB); vrátí náhled, nic neukládá |
| POST | `/labs/bulk` | uloží potvrzené položky (`{"items": [...]}`, max 500), duplicity přeskočí |
| GET | `/labs` | všechny výsledky s příznakem `out_of_range` |

## Poznámky k datům

- Číselný výsledek je ve `value`, kvalitativní (`neg`, `+`, `G1`) ve `value_text`.
- Stejný analyt může mít v různých listech jinou jednotku (u měrné hmotnosti moči se v listech lišila),
  proto při porovnání zkontroluj jednotku.
- Referenční meze se berou z listu, takže „mimo rozsah“ znamená mimo meze laboratoře, ne lékařské hodnocení.
