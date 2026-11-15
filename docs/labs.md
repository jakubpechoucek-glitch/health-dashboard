# Krevní rozbory

## Osoby

Aplikace umí sledovat víc lidí (rodinu). Nad záložkami je přepínač **Osoba**: Rozbory a Události se zobrazují vždy pro vybranou osobu,
import PDF ukládá výsledky té osobě, pro kterou je přepínač právě nastavený (náhled to píše do nadpisu, aby se nepletli).
**Garmin patří jen vlastníkovi** (osoba 1), u ostatních se místo něj zobrazí upozornění. Osoby se přidávají a přejmenovávají přímo nad záložkami
(`POST /persons`, `PUT /persons/{id}`). Vybraná osoba se pamatuje v prohlížeči. Interpretace ukazatelů (viz níže) je společná pro všechny osoby.
Ukazatel, který daná osoba nemá, se jí v přehledu vůbec nezobrazí.


## Import z PDF

V sekci **Krevní rozbory** nahraješ PDF výsledkového listu. Server z něj vyčte hodnoty, jednotky a
referenční meze a ukáže **náhled**. Uloží se až po tvém potvrzení. PDF se nikam neukládá.

- Čtení textu zajišťuje `pdftotext -layout` (balíček `poppler-utils`, je v `Dockerfile`).
- Podporovaná jsou čtyři rozložení: SmartMEDIX „Výsledkový list“ a „Laboratorní výsledky pacienta“, EUC Lab „Opis výsledku“ (každý řádek má vlastní datum odběru)
  a Unilabs „Výsledkový list“. Parser je v `app/labparse.py`.
- Výsledky z různých laboratoří se **sjednotí na společné názvy a jednotky**, aby šly porovnat v čase: např. `S_Kreatinin` (Unilabs) a `Kreatinin` (EUC) je jeden ukazatel,
  `µmol/L` a `umol/L` jedna jednotka, měrná hmotnost moči se přepočítá z kg/m3 na g/cm3, nula na močovém proužku (`arb.j.`) je `neg`. Diakritika se normalizuje (NFC).
  Neznámý ukazatel zůstane pod svým názvem a v přehledu se zařadí do skupiny Ostatní.
- Čte se jen **datum odběru a řádky s výsledky**. Jméno pacienta, číslo pojištěnce ani jiné identifikátory
  se nezpracovávají ani neukládají.
- Řádky, které parser nepřečte (např. textová poznámka bez hodnoty), se v náhledu vypíšou.
- Opakovaný import stejného listu nic nezdvojí (přeskočí se položky se stejným dnem, analytem a hodnotou).

## Záložka Krevní rozbory

Stránka má tři záložky: **Garmin**, **Krevní rozbory**, **Události a data**. Rozbory jsou v samostatné záložce a ukazují všechny ukazatele
seskupené (ledviny, elektrolyty, játra, lipidy, cukr, krevní obraz, prostata, moč).

U každého ukazatele je:

- **pruh referenčního rozmezí laboratoře** a v něm značky obou porovnávaných výsledků (prázdný kroužek = starší, plný = novější),
  spojnice se šipkou ukazuje, kam se hodnota posunula. Novější výsledek mimo rozmezí je červený,
- **změna**: směr (↑ ↓), rozdíl a procenta,
- **hodnocení** (✓ zlepšení, ⚠ zhoršení, v rozmezí, beze změny, bez hodnocení). Vždy je s ikonou a textem, barva není jediný signál,
- **interpretace**, kterou si u každého ukazatele nastavíš.

Nahoře jsou dlaždice-filtry (všechny / mimo rozmezí / zlepšeno / zhoršeno), výběr dvou porovnávaných odběrů a přepínač na tabulku se všemi odběry.

### Vysvětlivky

Každý ukazatel má pod sebou rozbalovací **„Co to je“**: celý název (např. AST = aspartátaminotransferáza), co se měří a s čím obecně
může souviset vyšší nebo nižší hodnota. Tlačítko **Rozbalit vysvětlivky** rozbalí všechny najednou. Texty jsou ve slovníku `VYSVETLENI`
v `index.html` a pokrývají všech 46 ukazatelů z dosavadních listů (včetně moči). U neznámého ukazatele se zobrazí, že vysvětlení zatím není.
Jde o obecnou informaci pro orientaci, ne o výklad konkrétního výsledku.

### Interpretace ukazatele

Každý ukazatel se čte jinak, proto je hodnocení změny volitelné:

| Režim | Význam | Změna je zlepšení, když… |
|---|---|---|
| **v rozmezí je v pořádku** (výchozí) | cílem je referenční rozmezí | se hodnota vrátí do rozmezí nebo se k němu přiblíží; uvnitř rozmezí se nehodnotí |
| **nižší je lepší** | např. LDL, celkový cholesterol, triacylglyceroly, Lp(a), PSA, kyselina močová, FIB-4 | hodnota klesne |
| **vyšší je lepší** | např. HDL cholesterol, GF CKD-EPI | hodnota stoupne |
| **nehodnotit změnu** | jen informace | nikdy |

Výchozí režimy jsou v `index.html` (`vychoziMod`). Tvoje změny se ukládají na server (`PUT /labs/settings`, tabulka `lab_setting`),
takže platí na všech zařízeních. Změna menší než 2 % šířky rozmezí se bere jako „beze změny“.
Pokud se u dvou výsledků liší jednotka (např. měrná hmotnost moči), změna se nehodnotí.

**Je to orientační pomůcka podle mezí laboratoře, ne lékařský závěr.** Mezi výsledky rozhoduje lékař.

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
| GET/PUT | `/labs/settings` | vlastní interpretace ukazatelů (`rozmezi`, `nizsi`, `vyssi`, `nehodnotit`) |

## Poznámky k datům

- Číselný výsledek je ve `value`, kvalitativní (`neg`, `+`, `G1`) ve `value_text`.
- Stejný analyt může mít v různých listech jinou jednotku (u měrné hmotnosti moči se v listech lišila),
  proto při porovnání zkontroluj jednotku.
- Referenční meze se berou z listu, takže „mimo rozsah“ znamená mimo meze laboratoře, ne lékařské hodnocení.
