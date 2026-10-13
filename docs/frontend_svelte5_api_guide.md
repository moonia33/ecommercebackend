# Svelte 5 Frontend API instrukcija

## Paskirtis

Šis dokumentas yra pilnas Svelte 5 storefront integracijos kontraktas su Django API.

## 1. Bazinis API kontraktas

- Bazinis prefiksas: `/api/v1`
- Dokumentacija:
  - Swagger: `/api/v1/docs`
  - OpenAPI: `/api/v1/openapi.json`
- Locale URL segmente nenaudojamas:
  - nenaudoti `/api/v1/lt/...`
  - naudoti `/api/v1/...` + `Accept-Language` arba `?lang=lt`
- Ops (vadybininkų) API kontraktas aprašytas atskirai: `docs/ops_admin_api.md`

### Palaikomų kalbų politika

- Numatyta kalbų bazė: `lt`, `pl`, `en`.
- Papildomos kalbos (pvz. `de`) gali būti įjungiamos backend konfigūracijoje (`SUPPORTED_LANGUAGE_CODES`).
- Kiekvienas shop gali turėti savo aktyvių kalbų sąrašą (`ShopLanguage`), todėl FE turi remtis realiu domeno atsaku.
- `ShopLanguage` yra vienintelis šaltinis shop kalboms; `Shop.default_language_code` yra išvestinis default indikatorius.
- Keičiant `SUPPORTED_LANGUAGE_CODES`, backend migracija pašalina nebeleidžiamus kodus (`lv/et` ir pan.), todėl FE neturi hardcodinti kalbų sąrašo.
- `slug` laukai gali būti lokalizuoti, todėl perjungus kalbą URL gali pasikeisti.

## 2. Techniniai Svelte 5 nustatymai

### Request helper

Visiems request naudoti vieną wrapper su:

- `credentials: 'include'`
- `headers.Accept: 'application/json'`
- `headers.Accept-Language: currentLocale`

Privaloma:

- nenaudoti mix tarp `localhost` ir `127.0.0.1`, nes cookies gali nesidalinti.

## 3. Auth kontraktas

### Pagrindiniai endpointai

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `PATCH /api/v1/auth/me`

### OTP srautas

- `POST /api/v1/auth/otp/request`
- `POST /api/v1/auth/otp/verify`

### Frontend taisyklės

- tokenų nelaikyti localStorage;
- remtis HttpOnly cookies;
- `401` atveju vieną kartą bandyti `refresh`, po nesėkmės logout + redirect į login.

## 4. Katalogas

### Endpointai

- `GET /api/v1/catalog/categories`
- `GET /api/v1/catalog/brands`
- `GET /api/v1/catalog/products`
- `GET /api/v1/catalog/products/{slug}`
- `GET /api/v1/catalog/recently-viewed`

### `products` query parametrai

- `country_code` (privalomas, pvz. `LT`)
- `channel` (`normal` arba `outlet`)
- `q`
- `category_slug`
- `brand_slug`
- `page`
- `page_size`

Pastaba:

- `category_slug` ir `brand_slug` priima tiek bazinius, tiek lokalizuotus slug.

### Kainų interpretacija

- kainos gaunamos kaip `Money` breakdown (`net`, `vat`, `gross`);
- UI turi rodyti `gross` galutiniam vartotojui;
- jei yra nuolaida:
  - rodyti `price` kaip aktyvią,
  - `compare_at_price` kaip perbrauktą.

## 5. Checkout srautas (privalomas)

### Cart

- `GET /api/v1/checkout/cart?country_code=LT`
- `POST /api/v1/checkout/cart/items?country_code=LT`
- `PATCH /api/v1/checkout/cart/items/{item_id}?country_code=LT`
- `DELETE /api/v1/checkout/cart/items/{item_id}?country_code=LT`

`offer_id`:

- jei pateikiamas, backend deda konkretų offer;
- jei nepateikiamas, backend parenka geriausią prieinamą.

### Shipping / payment pasirinkimai

- `GET /api/v1/shipping/countries`
- `GET /api/v1/checkout/shipping-methods?country_code=LT`
- `GET /api/v1/checkout/payment-methods?country_code=LT`
- `GET /api/v1/checkout/payment-options?country_code=LT`

### Checkout preview/confirm

- `POST /api/v1/checkout/checkout/preview`
- `POST /api/v1/checkout/checkout/confirm`

Jei shipping metodas reikalauja pickup:

- `pickup_point_id` privalomas.

Idempotency:

- `checkout/confirm` siųsti su `Idempotency-Key` header.

## 6. Užsakymai

- `GET /api/v1/checkout/orders`
- `GET /api/v1/checkout/orders/{order_id}`

UI laukams svarbu:

- `payment_status`, `payment_provider`
- `delivery_status`, `carrier_code`, `tracking_number`
- `payment_instructions` (bank transfer atvejis)

## 7. DPD / Unisend pickup sąrašai

- DPD: `GET /api/v1/dpd/lockers?...`
- Unisend: `GET /api/v1/unisend/terminals?...`

UI rekomendacija:

- pickup component turi būti atskiras nuo checkout form,
- selected `pickup_point_id` laikyti checkout state.

## 8. Klaidos ir jų tvarkymas

Bendros taisyklės:

- `400` - validacijos klaida, rodyti field-level jei įmanoma;
- `401` - auth sesija pasibaigusi, bandyti refresh;
- `403` - nėra teisių;
- `404` - objektas nerastas arba blogas route;
- `409` - konfliktas (pvz. pasenę consent duomenys).

## 9. Vertimų laukų naudojimo taisyklės FE pusėje

Katalogo objektuose laikykite skirtį tarp:

- **stabilių identifikatorių**: `id`, `code`, canonical `value`
- **lokalizuotų atvaizdavimo laukų**: `name`, `label`, `value_label`, lokalizuotas `slug`

Praktika:

- filtrų request'ams naudokite stabilius kodus/reikšmes;
- UI rodykite lokalizuotus tekstinius laukus.

## 10. Svelte 5 architektūrinės rekomendacijos

- turėti atskirus sluoksnius:
  - `lib/api/client.ts` - HTTP klientas;
  - `lib/api/services/*.ts` - endpoint funkcijos;
  - `lib/stores/*.svelte.ts` - state modelis;
  - `routes/*` - tik UI kompozicija.
- idempotency, retry ir toast logika laikyti service sluoksnyje, ne komponentuose.

## 11. Go-live checklist frontend komandai

- visi request su `credentials: 'include'`;
- nėra locale URL segmente;
- checkout confirm siunčia `Idempotency-Key`;
- pickup privalomumas įgyvendintas;
- klaidų žemėlapis (`400/401/403/404/409`) apdorotas;
- e2e testai: guest cart, login cart merge, confirm, payment callback, order detail.
