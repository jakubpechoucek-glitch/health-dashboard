# Přihlášení

Stejný princip jako u ostatních webů na jiné doméně (např. Lukas University):

1. Na **Rozcestníku** se přihlásíš Googlem (Supabase Auth).
2. Dlaždice „Zdravotní přehled“ (`data-sso="zdravi"`) otevře aplikaci s přístupovým tokenem v adrese
   (`#sso=<token>`; část za `#` se na server neposílá).
3. Stránka token uloží do `sessionStorage`, z adresy ho smaže a posílá ho jako `Authorization: Bearer`.
4. Server token ověří u Supabase (`/auth/v1/user`) a pustí jen e-mail z `ALLOWED_EMAILS`.

## Rozdíl oproti ostatním webům

Ostatní weby pouští každého člena rodiny (`je_clen()`). Tady **ne**: zdravotní data jsou jen
pro vlastníka, proto vlastní seznam `ALLOWED_EMAILS`. Supabase klíč v kódu je veřejný
publishable klíč, stejný jako v `rozcestnik/auth.js`.

## Vypršení přihlášení

Token Supabase platí zhruba hodinu. Stránka proto:

- čte čas vypršení z tokenu (pole `exp`) a **5 minut předem** ukáže upozornění s odkazem na nové přihlášení,
  aby sis mohl dopsat a uložit rozdělanou práci,
- při **načtení** stránky s prošlým tokenem se sama vrátí na Rozcestník (`?sso=zdravi`), a pokud jsi tam přihlášený,
  vrátí se hned zpět s novým tokenem. Proti smyčce je nejvýš jeden automatický pokus za 30 s,
- při chybě 401 během akce (uložení, synchronizace) nechá rozepsaný text na místě a ukáže zprávu s odkazem.

Platnost tokenu určuje Supabase (nastavení Auth, JWT expiry), sdílené s ostatními weby, proto ji tu neměníme.

## Omezení
- Pro funkčnost je nutné na Rozcestníku přidat adresu aplikace do `SSO_CILE` v `auth.js`
  (token se předává jen na pevně dané adresy).
