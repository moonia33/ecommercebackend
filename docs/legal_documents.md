# Teisiniai dokumentai ir sutikimai (`legal` app)

Taisyklės, privatumo politika ir kiti teisiniai tekstai laikomi versijomis, kad
būtų galima įrodyti, su kuria redakcija vartotojas sutiko, ir prireikus paprašyti
naujo sutikimo.

## Modeliai

- `LegalDocument`: parduotuvė (tuščia reiškia bendrą), `kind` (`terms`, `privacy`,
  `cookies`, `returns`, `other`), `slug` (viešas `/info/<slug>`), `required_at_checkout`.
  Parduotuvės dokumentas pakeičia bendrą tos pačios rūšies dokumentą.
- `LegalDocumentVersion`: `version` (tuščias reiškia įsigaliojimo datą, pvz. `2026-11-08`),
  `status` (`draft`/`published`), `effective_from`, `requires_reconsent`.
  **Kai publikuota versija įsigalioja, jos ir jos vertimų keisti ar trinti nebegalima.**
  Pataisymui kurkite naują versiją (admin veiksmas „Kopijuoti kaip naują juodraštį“).
- `LegalDocumentVersionTranslation`: tekstas kiekvienai kalbai, `change_summary`,
  `content_hash` (SHA-256 nuo pavadinimo ir teksto).
- `LegalAcceptance`: įrašai tik pridedami: vartotojas, tiksli versija, užsakymas,
  šaltinis (`checkout`/`reconsent`/`account`), kalba, IP, naršyklė.
- `OrderConsent.legal_version`: FK į tikslią versiją (`document_version` lieka kaip tekstas).

Galiojanti versija yra naujausia publikuota, kurios `effective_from <= now`. Publikuotą
versiją su ateities data galima keisti iki jos įsigaliojimo; ji rodoma kaip `upcoming`.

## API

- `GET /cms/pages/{slug}?version=`: jei slug priklauso teisiniam dokumentui, grąžinamas
  versijos tekstas ir `legal` objektas (`version`, `effective_from`, `is_current`,
  `change_summary`, `upcoming`, `versions[]`). Nežinoma versija grąžina 404.
- `GET /legal/documents`: galiojančių dokumentų sąrašas.
- `GET /checkout/consents`: kai yra dokumentų su `required_at_checkout`, versijos imamos
  iš DB. Kai tokių nėra, veikia senas režimas su `CHECKOUT_TERMS_VERSION` / `CHECKOUT_PRIVACY_VERSION`.
- `POST /checkout/confirm`: pasenusi versija grąžina 409 (`refresh /checkout/consents`).
  Sukuriami `OrderConsent` (su `legal_version`) ir `LegalAcceptance`.
- `GET /auth/me` → `legal_pending[]` ir `GET /legal/pending`: dokumentai, kurių naujesnė
  galiojanti versija turi `requires_reconsent`, o vartotojas sutiko su senesne.
  Niekada nesutikusių vartotojų neklausiama: jie sutiks per kitą užsakymą.
- `POST /legal/accept` `{items:[{kind, version}]}`: priima tik galiojančią versiją
  (kitaip grąžina 409) ir atsako atnaujintu `pending` sąrašu.

## Migracija

`legal/0002_import_cms_legal_pages` perkelia CMS puslapius `taisykles` ir `privatumas`
į dokumentus. Versijos pavadinimas imamas iš `CHECKOUT_*_VERSION` (pvz. `v1`), todėl
seni `OrderConsent` įrašai susiejami su ta versija ir papildomi `LegalAcceptance` įrašais.
Seni CMS puslapiai išjungiami (`is_active=False`).
