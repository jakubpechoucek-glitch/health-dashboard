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
