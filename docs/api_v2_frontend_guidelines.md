# API v2 gairės frontend komandai

Šis failas yra trumpa santrauka. Pilna instrukcija: `docs/frontend_svelte5_api_guide.md`.

## 1) URL ir routing taisyklės

- Visi endpointai po `/api/v1/...`.
- Nenaudoti locale segmente (`/api/v1/lt/...` neteisinga).
- Shop kontekstas nustatomas pagal domeną (`Host`), ne per URL.

## 2) Dokumentacija

- Swagger: `/api/v1/docs`
- OpenAPI: `/api/v1/openapi.json`

## 3) Kalba

Fallback tvarka:

1. `?lang=...`
2. `Accept-Language`
3. `Shop.default_language_code`
4. global `LANGUAGE_CODE`

## 4) Auth

- Tik su HttpOnly cookies.
- Visi request su `credentials: 'include'` / `withCredentials: true`.

## 5) Checkout esmė

- Cart:
  - `GET /api/v1/checkout/cart?country_code=LT`
  - `POST /api/v1/checkout/cart/items?country_code=LT`
- Checkout:
  - `POST /api/v1/checkout/checkout/preview`
  - `POST /api/v1/checkout/checkout/confirm`
- Jei `shipping_method` reikalauja pickup point, siųsti `pickup_point_id`.
- `checkout/confirm` siųsti su `Idempotency-Key`.

## 6) Multi-shop rollout

- `SHOP_CONTEXT_ENABLED=false` - legacy/global elgsena.
- `SHOP_CONTEXT_ENABLED=true` - shop-level katalogo, checkout, shipping ir payment filtravimas.
# API v2 gairės front-end komandai

Šis dokumentas aprašo praktinį FE kontraktą su dabartine backend baze (`bz`).

## 1) Bazinis URL kontraktas

- Visi API endpoint'ai yra po:
  - `/api/v1/...`
- Šioje bazėje **nenaudojamas locale segmentas URL'e**:
  - nenaudoti `/api/v1/lt/...`, `/api/v1/pl/...` ir pan.
- Multi-shop kontekstas nustatomas pagal domeną (`Host`), ne per URL prefiksą.

Teisingi pavyzdžiai:

- `/api/v1/auth/me`
- `/api/v1/catalog/products?country_code=LT`
- `/api/v1/checkout/cart?country_code=LT`

Neteisingi pavyzdžiai:

- `/api/v1/lt/auth/me`
- `/api/v1/lt/checkout/cart`

## 2) Dokumentacija ir schema

- Swagger UI: `/api/v1/docs`
- OpenAPI JSON: `/api/v1/openapi.json`

Pastaba:

- `/api/docs` ar `/api/` gali grąžinti 404 ir tai yra expected dabartinėje konfiguracijoje.

## 3) Kalbos perdavimas

Kalba parenkama taip:

1. `?lang=...` query param (pvz. `?lang=lt`)
2. `Accept-Language` header
3. fallback į shop `default_language_code` (pagal domeną)
4. fallback į global `LANGUAGE_CODE`

Rekomendacija FE:

- visiems katalogo endpointams siųsti `Accept-Language`;
- preview/test režime naudoti `?lang=...`.

## 4) Auth ir cookies

Naudojamas HttpOnly cookie auth (`access_token`, `refresh_token`), todėl FE request'ai turi būti su credentials:

- `fetch`: `credentials: 'include'`
- axios: `withCredentials: true`

## 5) Checkout minimalus FE kontraktas

- Cart:
  - `GET /api/v1/checkout/cart?country_code=LT`
  - `POST /api/v1/checkout/cart/items?country_code=LT`
- Checkout:
  - `POST /api/v1/checkout/checkout/preview`
  - `POST /api/v1/checkout/checkout/confirm`

Pastaba:

- kai `shipping_method` reikalauja pickup point, FE privalo paduoti `pickup_point_id`.

## 6) Migracijos laikotarpio pastabos

Šioje fazėje vyksta Presta duomenų bootstrap/importai:

- dalis duomenų gali atsirasti etapais (pilot batch -> full batch);
- rekomenduojama FE daryti tolerantišką būseną, jei kai kurioms prekėms dar trūksta papildomų laukų.

## 7) Multi-shop ir rollout gairės

- Backend turi feature flag `SHOP_CONTEXT_ENABLED`:
  - `false`: veikia legacy/global režimu;
  - `true`: katalogas, checkout, shipping ir payment metodai filtruojami per shop kontekstą.
- FE papildomo `shop_id` siųsti nereikia:
  - produkcijoje shop parenkamas pagal domeną;
  - debug/test scenarijams backend gali turėti vidinius override, bet FE kontrakto nekeičiam.
- Tikėtinas skirtumas tarp domenų:
  - skirtingos kalbos;
  - skirtingas metodų prieinamumas (`shipping`, `payment`);
  - galimai ribotas asortimentas ir B2B kainodara.
