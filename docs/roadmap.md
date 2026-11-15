# Plán

- [x] Skeleton: FastAPI, SQLite, labs, events, timeline, testy
- [x] Garmin Personal API: klient, token, synchronizace denních souhrnů, spánku a HRV (testy s mock serverem)
- [ ] První ostrá synchronizace: ověřit tvar tokenu a mapování polí na skutečných datech
- [x] Historický import po úsecích s průběhem, zastavením a navázáním; grafy za rok a 5 let (průměry po skupinách dnů)
- [x] Pravidelná automatická synchronizace Garminu uvnitř aplikace (každých 6 h, záznam o bězích, stav v UI)
- [x] UI: grafy metrik z Garminu (kroky, klidový tep, spánek, skóre spánku, HRV, stres, Body Battery), filtr 14 / 30 / 90 dní / rok, tabulka
- [x] UI: události a odběry vyznačené v grafech (▲ na ose, název v tooltipu); formulář pro přidání a mazání událostí
- [x] Import krevních rozborů z PDF (extrakce + náhled a potvrzení), tabulka rozborů po odběrech
- [x] Záložka Krevní rozbory: pruh referenčního rozmezí s oběma výsledky, směr změny a hodnocení podle interpretace ukazatele
- [ ] Vývoj ukazatele v čase jako čára přes víc než dva odběry (až jich bude víc)
- [ ] Slovník analytů a referenčních rozsahů
- [ ] Genetika: formát výsledků, strukturovaný záznam, odkaz na zdrojový soubor
- [ ] Šifrované zálohy
