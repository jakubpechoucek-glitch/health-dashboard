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

## Omezení

- Token Supabase platí zhruba hodinu. Po vypršení stránka nabídne odkaz zpět na Rozcestník.
- Pro funkčnost je nutné na Rozcestníku přidat adresu aplikace do `SSO_CILE` v `auth.js`
  (token se předává jen na pevně dané adresy).
