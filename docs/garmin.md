# Garmin Connect Personal API

Zdroj: [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api),
[FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/) a Swagger
dokumentace Personal API (OpenAPI 3.0, server `https://personal.connectapi.garmin.com`).

Aplikace data **stahuje sama (pull)**. Žádný webhook ani veřejná adresa pro Garmin není potřeba.

## Jak to funguje

1. V Garmin Connect (web) vytvoř Personal API Key. Zvol platnost (30 / 90 / 180 dní / rok), klíč se zobrazí **jen jednou**.
2. Klíč patří do proměnné prostředí `GARMIN_API_KEY` (lokálně `.env`, na Renderu Environment). Nikdy do gitu ani do chatu.
3. Server pošle klíč na token endpoint a dostane OAuth2 access token:
   `POST /personalapitoken-service/personal-api/personal-api-key/token`, hlavička `Authorization: ApiKey <klíč>`.
4. Access token platí **24 h**, refresh token Garmin nepodporuje. Server si ho po 23 h nebo při 401 vyžádá znovu.
5. Data se čtou přes GET s hlavičkou `Authorization: Bearer <token>`.

Garmin pošle e-mail zhruba týden před vypršením klíče. Pak je potřeba vytvořit nový klíč a vyměnit `GARMIN_API_KEY`.

## Všechny metriky

Kromě sedmi hlavních grafů aplikace ukládá **všechny číselné hodnoty**, které Garmin vrací, a zobrazuje je v katalogu (sekce **Všechny metriky z Garminu**):

| Zdroj (endpoint Garminu) | Co se ukládá | Jak se jmenuje metrika |
|---|---|---|
| `usersummary-service/v2/dailysummaries` | každé číselné pole denního souhrnu: pohyb (kroky, cíle, vzdálenost, patra), srdce, stres (podíly a doby), Body Battery (nabito, vybito…), hydratace, kalorie, dech, SpO2, intenzivní minuty | `daily.<cesta.k.poli>` |
| `sleep-service/sleep/dailySleepSummariesByDate` | fáze spánku, SpO2 a dech ve spánku, probuzení, stres ve spánku, dílčí skóre spánku, potřeba spánku, sladění spánku | `sleep.<cesta>` |
| `hrv-service/hrv/daily` | HRV minulé noci a týdne, pásma základní úrovně | `hrv.<cesta>` |
| `activitylist-service/activities/search/activities` | **aktivity** (tabulka `activity`: běh, kolo, posilování… se všemi poli bez GPS polohy) a denní souhrn aktivit | `activity.count`, `.duration_s`, `.distance_m`, `.calories`, `.vo2max`, `.aerobic_te_max` |
| `weight-service/weight/range` | váha a složení těla (pokud máš chytrou váhu): hmotnost, BMI, tuk, voda, kosti, svaly, viscerální tuk | `body.*` |
| `fitnessage-service/fitnessage/v2` | fitness věk (jedna hodnota ke dni synchronizace) | `fitness.*` |

- **Nic se neukládá, co není číslo**: texty (např. kvalifikátor stresu), pravdivostní hodnoty, seznamy, identifikátory a časové značky se vynechávají.
  Pole, která už mají své pojmenování (kroky, klidový tep, skóre spánku…), se neduplikují.
- **Katalog** (`GET /metrics/catalog`) ukazuje u každé metriky, kolik dní je, od kdy do kdy a poslední hodnotu. V UI se dá hledat, zaškrtnutím se metrika **připne jako graf**
  (výběr se ukládá na server, `GET/PUT /prefs`, a platí na všech zařízeních). Jednotky se převádějí na čitelné (sekundy a milisekundy na hodiny nebo minuty, podíly na %, metry na km).
- **Aktivity** jsou tabulka za zvolené období (`GET /garmin/activities`); klepnutím na řádek se zobrazí všechna pole od Garminu (`GET /garmin/activities/{id}`).
  **GPS poloha a údaje o vlastníkovi se do databáze neukládají.**
- **Zdroje, které nemáš** (např. váha bez chytré váhy), synchronizaci nepokazí: poznamenají se jako „skipped“ a běh se považuje za úspěšný.
- **Přepočet starších dat**: při prvním startu po aktualizaci se z už uložených surových odpovědí (`garmin_raw`) přepočítají nová pravidla, takže historické dny dostanou všechny metriky bez nového stahování.
  Aktivity, váha a fitness věk se ale do surových dat dříve nestahovaly. Pro starší období spusť **Stáhnout celou historii**.
- Nezahrnuto (zatím): vteřinové a minutové řady (tep během dne, stres během dne, Body Battery v čase), podrobné záznamy aktivit (FIT soubory), menstruační cyklus, záznamy jídla.
  Endpointy v API existují, jde je přidat.

## Automatická synchronizace

Aplikace si Garmin stahuje **sama**: každých `AUTO_SYNC_HOURS` hodin (výchozí 6) znovu obnoví posledních `AUTO_SYNC_DAYS + 1` dní (výchozí 8).
Obnovují se i dny zpátky, protože Garmin některé hodnoty (spánek, HRV) dopočítává se zpožděním a hodnota dne se přepisuje.

- **Běží uvnitř webové služby** (`app/autosync.py`), ne jako samostatný cron na Renderu: databáze leží na disku, který jde připojit jen k jedné službě,
  a cron by ji neviděl. Navíc aplikace zná `GARMIN_API_KEY` a nepotřebuje přihlášení přes Google.
- **Stav je v databázi** (`sync_run`), takže restart služby nic nezpůsobí: po startu (zhruba za 90 s) se zjistí, kdy proběhla poslední úspěšná synchronizace,
  a spustí se jen když je na řadě. Kontrola běží každých 10 minut.
- **Po chybě** se další pokus zkusí nejdřív za 30 minut (bez bušení do API). Ruční synchronizace čeká na dokončení běžící (max. 90 s), potom vrátí 409.
- Na záložce **Garmin** je řádek se stavem (kdy proběhla poslední úspěšná, případně čím selhala). Když automatické pokusy selhávají, nejčastější příčinou je
  **vypršelý API klíč** (platí 30 dní až rok, Garmin pošle e-mail týden předem): vytvoř nový v Garmin Connect a vlož ho do `GARMIN_API_KEY`.
- Vypnutí: `AUTO_SYNC_HOURS=0`. Databáze používá režim WAL (soubory `health.db-wal` a `-shm` vedle databáze jsou v pořádku), aby synchronizace nebrzdila aplikaci.
- Do logu se píšou jen počty (`autosync: hotovo, uloženo N hodnot`), nikdy obsah dat ani klíč. Záznamy o bězích jsou v `GET /garmin/status`.

## Synchronizace

`POST /garmin/sync?days=7&end=2026-11-15` (jen přihlášený vlastník) stáhne N dní (1 až 366) končících dnem `end` (výchozí dnes).

### Celá historie

Tlačítko **Stáhnout celou historii** v aplikaci volá `/garmin/sync` opakovaně po 56denních úsecích od dneška do minulosti.
Důvod: jeden dlouhý požadavek by na Renderu mohl vypršet a Garmin na velké rozsahy vrací chybu 500.

- Stahuje se, dokud tři úseky po sobě nevrátí nic (před začátkem dat), nejvýš 40 úseků (≈ 6 let).
- Zobrazuje průběh, jde kdykoli **zastavit** a později **navázat** (bod pro navázání je v prohlížeči, v `localStorage`).
- Po třech chybných úsecích po sobě se zastaví, aby nebouchalo do API. Opakování je bezpečné (UPSERT).
- Stránku při stahování nezavírej a nenech vypršet přihlášení.
- Surové odpovědi se ukládají do `garmin_raw`, takže data jde později přepočítat bez nového stahování.

| Endpoint Garminu | Úsek | Uložené metriky |
|---|---|---|
| `/usersummary-service/v2/dailysummaries` | po 28 dnech (dokumentace povoluje až rok) | kroky, klidový/min/max tep, stres, Body Battery, kcal, dech, SpO2, intenzivní minuty |
| `/sleep-service/sleep/dailySleepSummariesByDate` | max 31 dní | délka spánku, hluboký/lehký/REM/bdělost, skóre spánku, tep ve spánku |
| `/hrv-service/hrv/daily/{od}/{do}` | po 28 dnech | HRV minulé noci a týdenní průměr |

Surové odpovědi se ukládají do `garmin_raw`, takže jde mapování polí opravit a data přepočítat bez nového stahování.
Opakované spuštění je bezpečné (UPSERT podle zdroje, metriky a dne).

### Chyba HTTP 500 u denních souhrnů

Při prvním ostrém spuštění za rok vrátil `v2/dailysummaries` HTTP 500, zatímco spánek a HRV prošly.
Proto se denní souhrny stahují po 28 dnech od nejnovějších. Pokud úsek přesto selže, aplikace ho
dotáhne **po jednom dni** přes `/usersummary-service/usersummary/daily?calendarDate=` (jiná pole,
`normalize_daily_flat`). Záložní stahování je omezeno na 60 dnů za jedno spuštění, aby požadavek
netrval příliš dlouho. Starší období se doplní dalšími spuštěními (např. po 30 dnech).

## Co zatím není ověřeno proti skutečnému účtu

Specifikace nepopisuje všechno, proto při prvním spuštění zkontroluj:

- **Tvar odpovědi token endpointu.** Kód čeká JSON s `access_token` (nebo `accessToken` / `token`), případně holý text.
- **Hlavička pro data** je `Authorization: Bearer <token>` (předpoklad z „OAuth2 access token“, Swagger bezpečnostní schéma neuvádí).
- **Klíč `sleepScores.overall`** pro celkové skóre spánku.
- **Limity požadavků** nejsou zveřejněné. Server posílá požadavky postupně a při chybě daný typ dat přeskočí a chybu vrátí v odpovědi.

Chyby se vrací v poli `errors` (jen cesta a HTTP kód, nikdy klíč ani obsah odpovědi).

## Další endpointy ve specifikaci (zatím nepoužité)

Aktivity (`/activitylist-service/activities/search/activities`, stažení FIT souboru), denní tep,
Body Battery, SpO2, váha (`/weight-service/weight/...`), výživa, fitness age, zóny tepu, profil, menstruační cyklus.
