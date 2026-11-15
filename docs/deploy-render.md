# Nasazení na Render

Rozhodnutí: aplikace běží na Renderu, aby byla dostupná i z mobilu. Znamená to, že zdravotní
data leží na cizím serveru a aplikace je dostupná z internetu, proto platí tato pravidla.

## Nastavení

- Plán **Starter** (perzistentní disk není na free plánu), jedna instance, disk 1 GB na `/data`
  (SQLite v `/data/health.db`). Definice je v `render.yaml`.
- Region Frankfurt (data zůstanou v EU).
- Tajné hodnoty (`APP_PASSWORD`, `GARMIN_API_KEY`) se zadávají ručně v Renderu, nejsou v gitu.
- `APP_PASSWORD` dlouhé a náhodné. Bez něj aplikace odmítá všechny požadavky (503).
- `/docs` a OpenAPI jsou vypnuté (`ENABLE_DOCS=false`).
- Přihlášení je HTTP Basic přes HTTPS, ověřuje se na každý požadavek mimo `/health`.

## Co na Render nepatří

- Surové soubory z genetiky a PDF s rozbory. V databázi jen hodnoty a závěry.
- Zálohy dělat pravidelně stažením šifrovaného exportu databáze (viz roadmap), ne spoléhat jen na disk.

## Omezení

- SQLite jen s jednou instancí. Škálování tu není potřeba.
- Disk není zálohovaný automaticky, snapshoty disku jsou nutné nastavit v Renderu.
