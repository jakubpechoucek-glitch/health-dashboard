# Soukromí a bezpečnost

Data jsou citlivá (zdraví, genetika) a po úniku nejdou vzít zpět.

## Pravidla

- **Přihlášení vždy.** Každý požadavek na data vyžaduje heslo (`APP_PASSWORD`). Při jeho absenci aplikace nic nevydá.
- **Hosting:** aktuálně Render (viz [deploy-render.md](deploy-render.md)). Data tak leží na cizím serveru; lokální běh zůstává možnou alternativou.
- **Do repa nikdy:** `data/`, `.env`, `*.db`, `*.fit`, `*.pdf`. Chrání `.gitignore`,
  pre-commit hook `no-health-data` a `gitleaks`.
- **Veřejně je jen webhook** a jen přes tunel, který propouští jedinou cestu. Token v URL je
  druhá vrstva, ne jediná.
- **Zálohy šifrovaně** (`age`, `restic`), nikdy nešifrované do cloudu.
- **Žádné nahrávání reálných dat** do cloudových nástrojů, ani AI asistentů. Vývoj a testy
  jen se syntetickými daty.
- **Genetika:** surový soubor (např. VCF/TXT od laboratoře) zůstává mimo repo a ideálně na
  šifrovaném disku. V databázi jen odkaz a závěry, které potřebuješ.
- **GDPR:** vlastní data pro osobní použití jsou mimo rozsah GDPR. Pokud by aplikaci
  používal někdo další, je to nová situace (zvláštní kategorie údajů).

## Rizika, na která dávat pozor

- Zapomenutý port otevřený do sítě (`--host 0.0.0.0`).
- Logy s osobními údaji (payloady neposílat do logů).
- Přepínání na hosting „jen na chvíli“. Data pak zůstanou na cizím disku.
