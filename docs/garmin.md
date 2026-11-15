# Garmin Health API

Garmin Health API funguje jako **push**: po autorizaci uživatele posílá Garmin souhrny
(`dailies`, `sleeps`, `hrv`, `stressDetails`, `activities`…) na tvoje URL. Naše URL je
`POST /garmin/webhook/{token}`.

## Nastavení

1. V developer portálu Garminu zaregistruj endpoint (HTTPS, veřejně dostupný) a zapni typy souhrnů, které chceš.
2. Do `.env` dej `GARMIN_WEBHOOK_TOKEN` (náhodný řetězec). Je součástí URL, takže ji zná jen Garmin.
3. Autorizuj vlastní účet přes OAuth (údaje `GARMIN_CLIENT_ID` / `GARMIN_CLIENT_SECRET`).
4. Historická data: portál nabízí *backfill*, který dopošle starší období přes stejný webhook.

## Co je nutné ověřit v aktuální dokumentaci portálu

Dokumentace Garmin Health API je dostupná jen po přihlášení, proto je v kódu pár míst, která
je třeba proti ní zkontrolovat:

- **Názvy polí** v `app/garmin.py` (`FIELD_MAP`).
- **Režim doručení**: *push* (data přímo v těle) vs. *ping* (v těle jen odkaz `callbackURL`,
  data se stahují zvlášť). Ping zatím není implementován, ale surový payload se uloží.
- **Způsob autorizace** (OAuth verze) a zda Garmin podepisuje požadavky.

## Veřejně dostupný endpoint a soukromí

Garmin potřebuje volat naši URL z internetu, zbytek aplikace ale zveřejnit nechceme.
Doporučení: reverzní proxy / tunel (Cloudflare Tunnel, Tailscale Funnel), který propustí
**jen** cestu `/garmin/webhook/*`. Aplikace jinak běží na `127.0.0.1`.
Detaily v [privacy.md](privacy.md).
