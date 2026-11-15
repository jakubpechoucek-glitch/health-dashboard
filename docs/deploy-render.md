# Nasazení na Render

Rozhodnutí: aplikace běží na Renderu, aby byla dostupná i z mobilu. Znamená to, že zdravotní
data leží na cizím serveru a aplikace je dostupná z internetu, proto platí tato pravidla.

## Nastavení

- Plán **Starter** (perzistentní disk není na free plánu), jedna instance, disk 1 GB na `/data`
  (SQLite v `/data/health.db`). Definice je v `render.yaml`.
- Region Frankfurt (data zůstanou v EU).
- Tajné hodnoty a osobní údaje (`ALLOWED_EMAILS`, `GARMIN_API_KEY`) se zadávají ručně v Renderu, nejsou v gitu.
- `ALLOWED_EMAILS` = jen vlastníkův e-mail. Bez něj aplikace odmítá všechny požadavky (503).
- `/docs` a OpenAPI jsou vypnuté (`ENABLE_DOCS=false`).
- Přihlášení Googlem přes Supabase jako na Rozcestníku (viz [auth.md](auth.md)); ověřuje se na každý požadavek mimo `/` a `/health`.

## Co na Render nepatří

- Surové soubory z genetiky a PDF s rozbory. V databázi jen hodnoty a závěry.
- Zálohy dělat pravidelně stažením šifrovaného exportu databáze (viz roadmap), ne spoléhat jen na disk.

## Trvalost dat a zálohy

- Databáze je na připojeném disku (`/data`). Nové nasazení aplikace ji **nemaže**, mění se jen kód. Smazala by se jen při odstranění disku
  nebo služby. Aplikace to ukazuje v sekci **Úložiště dat** (✓ trvalý disk / ⚠ upozornění).
- Render navíc dělá **automatické denní snapshoty disku** (uchovávají se několik dní, viz nastavení disku v Renderu).
- Vlastní záloha: tlačítko **Stáhnout zálohu databáze** (`GET /backup`) stáhne konzistentní kopii celé databáze. Soubor obsahuje
  všechna zdravotní data, ukládej ho jen na šifrovaný disk nebo do správce hesel s přílohami.
- Obnova ze zálohy se zatím dělá ručně (nahrání souboru na disk přes SSH služby). Nemá tlačítko, aby šlo omylem přepsat data.

## Omezení

- SQLite jen s jednou instancí. Škálování tu není potřeba.
- Disk není zálohovaný automaticky, snapshoty disku jsou nutné nastavit v Renderu.
