# Plán

- [x] Skeleton: FastAPI, SQLite, labs, events, timeline, testy
- [x] Garmin Personal API: klient, token, synchronizace denních souhrnů, spánku a HRV (testy s mock serverem)
- [ ] První ostrá synchronizace: ověřit tvar tokenu a mapování polí na skutečných datech
- [ ] Historický import (`days=365`) a pravidelná synchronizace (Render cron)
- [x] UI: grafy metrik z Garminu (kroky, klidový tep, spánek, skóre spánku, HRV, stres, Body Battery), filtr 14 / 30 / 90 dní / rok, tabulka
- [ ] UI: události a krevní rozbory vyznačené přímo v grafech na společné časové ose
- [x] Import krevních rozborů z PDF (extrakce + náhled a potvrzení), tabulka rozborů po odběrech
- [ ] Grafy vývoje jednotlivých analytů s referenčním pásmem (až bude víc odběrů)
- [ ] Slovník analytů a referenčních rozsahů
- [ ] Genetika: formát výsledků, strukturovaný záznam, odkaz na zdrojový soubor
- [ ] Šifrované zálohy
