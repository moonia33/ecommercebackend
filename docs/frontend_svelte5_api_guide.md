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

### Variantų nuotraukos

`ProductDetailOut.images[]` yra **bendra galerija** (`ProductImage.variants` tuščias).
`variants[].images[]` — nuotraukos priskirtos konkrečiam variantui (kai jos skiriasi, pvz. skirtingos spalvos).

Atvaizdavimo taisyklė:

- variantas **nepasirinktas** arba variantas neturi savų nuotraukų → rodyti `images[]`;
- variantas **pasirinktas ir turi `images[]`** → rodyti jo nuotraukas (gali būti kelios);
- varianto `images[]` yra papildomos prie bendrų — jei dizainas nori visų nuotraukų, naudok `variant.images + product.images` su `sort_order`.

### Produkto video

`ProductDetailOut.videos[]` — išorinės video nuorodos (pvz. YouTube):

- `url` — embed'uojama nuoroda;
- `video_type` — `product` | `instructional` | `tutorial`;
- `language_code` — video kalba ("" = universali visoms kalboms);
- `sort_order` — rūšiavimas.

Backend jau filtruoja: `videos[]` grąžina tik `language_code == ""` arba sutampančią su request kalba — FE papildomo filtravimo nereikia. Rodoma PDP galerijos/modal zonoje, paprastai kaip tab'as šalia nuotraukų.

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
- `GET /api/v1/checkout/payment-methods?country_code=LT&shipping_method=<code>`
- `GET /api/v1/checkout/payment-options?country_code=LT&shipping_method=<code>`

`shipping_method` param filtruoja mokėjimo būdus, kurie priklauso nuo
pristatymo būdo (pvz. `cod` — mokėjimas atsiimant — rodomas tik LT
kurjeriui ir `store_pickup`, paštomatams paslėptas). FE turėtų kviesti
payment-options **dar kartą** pasikeitus shipping metodui.

### Checkout preview/confirm

- `POST /api/v1/checkout/checkout/preview`
- `POST /api/v1/checkout/checkout/confirm`

Jei shipping metodas reikalauja pickup:

- `pickup_point_id` privalomas.

Idempotency:

- `checkout/confirm` siųsti su `Idempotency-Key` header.

### Online mokėjimas (Montonio)

`payment_method` reikšmės `checkout/confirm` payload'e:

- `bank_transfer` — pavedimas ranka; `payment_instructions` laukai grąžinami;
- `montonio` — Montonio Stargate redirect flow (kai sukonfigūruota backend'e);
- `cod` — mokėjimas atsiimant (LT kurjeris / `store_pickup`). Intent lieka
  `pending` — klientas moka pristatymo metu; `payment_instructions`
  grąžinami. COD užsakymas **niekada nebaigia** rezervacijos galiojimo;
- `inbank` — Inbank lizingas (e-POS redirect, LT). Reikalauja
  `INBANK_*` env konfigūracijos; atsakyme `redirect_url` → e-POS.
  Būsenas gauna `payment_status` webhook'e (server-to-server callback).

### Inbank lizingo skaičiuoklė

`GET /api/v1/payments/inbank/calculation?amount=<gross>&period=<mėn>`

(autentišku klientui). Grąžina `monthly_payment`, `interest_rate_annual`,
`credit_cost_rate_annual`, `total_cost`, `down_payment_minimum_amount` —
"nuo X €/mėn" widget'ui. Backend proxy: Inbank API key lieka serveryje.
`payment-options` inbank opcijoje turi `payload.calculator_url`.

Montonio flow'as:

1. `checkout/confirm` su `payment_method="montonio"` → atsakymo `redirect_url` yra Montonio `paymentUrl` (`payment_provider="montonio"`, `payment_status="redirected"`).
2. FE daro `window.location.href = redirect_url` — klientas apmoka Montonio gateway'je (bank link / card).
3. Klientas grįžta į `MONTONIO_RETURN_URL` (FE route) su `?order-token=...` — **netikėti** šio statuso kaip galutinio (patikimą rezultatą rodo `GET /checkout/orders/{id}` — `payment_status`).
4. Backend'as gauna webhook'ą → `order.status="paid"`, `payment_status="succeeded"`.
5. FE success puslapyje pollinti `GET /checkout/orders/{order_id}` kol `status === "paid"` (webhook'as gali užtrukti sekundes).

Inbank flow'as (identiškas struktūrai):

1. `checkout/confirm` su `payment_method="inbank"` → `redirect_url` yra Inbank e-POS nuoroda (`payment_provider="inbank"`, `payment_status="redirected"`).
2. FE redirect'ina klientą — jis užpildo lizingo paraišką Inbank aplinkoje.
3. Klientas grįžta į `INBANK_RETURN_URL` (FE route) arba `INBANK_CANCEL_URL` atšaukimo atveju — **netikėti** naršyklės rezultato.
4. Backend'as gauna server-to-server callback'ą (`INBANK_CALLBACK_URL` → `/api/v1/payments/inbank/callback`, HMAC-SHA512 patvirtintas). `completed` → `status="paid"`; `declined`/`failed`/`expired` → `failed`; `cancelled` → `cancelled`.
5. FE poll'ina `GET /checkout/orders/{order_id}` kol `status === "paid"` — kreditavimas gali užtrukti ilgiau nei Montonio.

## 6. Užsakymai

- `GET /api/v1/checkout/orders`
- `GET /api/v1/checkout/orders/{order_id}`
- `GET /api/v1/checkout/statuses` — visų būsenų enum'ai su lokalizuotais labeliais (filter'iams/status badge'iams)

UI laukams svarbu:

- `payment_status` + `payment_status_label`, `payment_provider` + `payment_provider_label`
- `status` + `status_label` (užsakymo)
- `delivery_status` + `delivery_status_label`, `carrier_code`, `tracking_number`
- `payment_instructions` (bank transfer atvejis)

Būsenų atvaizdavimas:

- `*_label` laukai jau **lokalizuoti** pagal request kalbą (`?lang=` / `Accept-Language` / shop default) — FE juos gali rodyti tiesiogiai, nereikia savų vertimų.
- `*_status`/`*_mode` laukai yra stabilūs mašininiai kodai — juos naudokite logikai (pvz. `delivery_status === 'in_transit'`), ne UI tekstui.
- `GET /checkout/statuses` grąžina `{domain: [{code, label}]}` visoms domenams: `order_status`, `delivery_status`, `fulfillment_mode`, `supplier_reservation_status`, `payment_status`, `payment_provider` — naudinga filtrų dropdown'ams.
- Šaltinis tiesai: `checkout/statuses.py` — naujoms būsenoms label'iai pridedami ten.

`delivery_status` kodai (vieningi visiems vežėjams — DPD/Unisend raw statusai map'inami backend'e):

| Kodas | LT label'as | Pastabos |
|---|---|---|
| `pending` | Ruošiama | pradinis |
| `awaiting_stock` | Laukiama prekės iš nuotolinio sandėlio | dropship/supplier line'ai |
| `packing` | Perduota pakavimui | |
| `packed` | Supakuota, laukiama paėmimo | nustatomas ir sugeneravus lipduką |
| `picked_up` | Siunta paimta | vežėjas paėmė siuntą |
| `in_transit` | Siunta kelyje | terminalai, line-haul, kurjerio skenai |
| `at_pickup_point` | Pristatyta į paštomatą | galima atsiimti |
| `delivered` | Pristatyta | kurjeris; paštomatams label'as tampa „Atsiimta iš paštomato“ |
| `returning` | Grąžinama siuntėjui | return-to-sender srautas |
| `returned` | Siunta grąžinta siuntėjui | |
| `cancelled` / `error` | Atšaukta / Klaida | |

- `Order.delivered_at` (ISO datetime) nustatomas kai `delivery_status → delivered` — klientas realiai atsiėmė (kurjeris, paštomato atsiėmimas, parduotuvė). Return window ir sąskaita skaičiuojami nuo šio momento.
- Paštomato užsakymams (`shipping_method` su `pickup`/`locker`) `delivered` label'as API'e automatiškai rodomas kaip „Atsiimta iš paštomato“ — machine kodas lieka `delivered`.

## Sąskaitos faktūros

Pereitus `delivery_status → delivered` backend'as automatiškai išrašo sąskaitą (serija per shop'ą: `SF-<metai>-NNNN`), sugeneruoja PDF ir išsiunčia el. paštu. Kredito sąskaitos (`KRF-*`) — grąžinimams.

- `OrderOut.delivered_at` — pristatymo/atsiėmimo timestamp.
- `OrderOut.invoice_number` — išrašytos sąskaitos numeris (`""` kol neišrašyta).
- `GET /checkout/orders/{id}/invoice` — PDF atsisiuntimas (autentifikuota, tik savo užsakymas). Rodyti download mygtuką kai `invoice_number` netuščias.

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
