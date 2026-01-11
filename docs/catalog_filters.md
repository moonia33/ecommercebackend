# Catalog listing filters – contract

Šitas dokumentas aprašo produktų listingų filtravimo kontraktą (query param formatus ir susijusius endpointus), kad frontas galėtų nuosekliai generuoti filtrų UI ir daryti listingus per skirtingus maršrutus.

## Bazinis principas

Kanoninis produktų listing endpointas:

- `GET /api/v1/catalog/products`

Kiti listingai (pagal fronto maršrutus) yra tik „aliasai“ ir naudoja tą patį filtrų kontraktą:

- `GET /api/v1/catalog/categories/{slug}/products`
- `GET /api/v1/catalog/brands/{slug}/products`
- `GET /api/v1/catalog/product-groups/{code}/products`

Visi listingai palaiko:

- `country_code` (ISO-2, pvz. `LT`)
- `channel` (`normal` arba `outlet`)
- filtrus (žr. žemiau)
- paginaciją (`page`, `page_size`) per Ninja `PageNumberPagination`

## Produktų listingas

### GET `/api/v1/catalog/products`

Query:

- `country_code` (default `LT`) — PVZ: `LT`
- `channel` (default `normal`) — `normal` arba `outlet`
- `q` (optional) — paieška pagal `name/slug/sku`
- `category_slug` (optional) — filtruojama pagal kategoriją ir visus jos dukterinius (descendants)
- `brand_slug` (optional)
- `group_code` (optional) — `ProductGroup.code`
- `feature` (optional) — „pair list“ formatas (žr. žemiau)
- `option` (optional) — „pair list“ formatas (žr. žemiau)
- `sort` (optional) — rikiavimas (žr. žemiau)
- `in_stock_only` (optional, default false) — jei `true`, grąžina tik prekes, kurios turi bent vieną offer su `qty_available>0` šiame `channel`
- `page` (optional, default 1)
- `page_size` (optional, default 20; max 100)

#### `sort` formatas

Palaikomos reikšmės:

- `price` / `-price` — pagal kainą
- `created` / `-created` (alias: `created_at` / `-created_at`) — pagal `Product.created_at`
- `discounted` / `-discounted` — pagal tai, ar produktas turi aktyvų offer su mažesne kaina nei list price
- `best_selling` / `-best_selling` — pagal parduotą kiekį (sum(qty)) per `OrderLine`, skaičiuojant tik `Order.status=PAID`

Pastabos:

- `price` rikiavimas skaičiuojamas pagal DB reprezentacinę kainą (`min(offer_price)` arba `min(variant_price)`), nes promo pritaikymas vyksta Python sluoksnyje vėliau.
- `best_selling` yra agregacija per užsakymų eilutes; dideliuose kataloguose gali būti brangesnis už kitus sortus.

#### Stock elgsena (in-stock first)

Nepriklausomai nuo `sort`, listingas visada rikiuoja taip:

- pirma prekės, kurios turi bent vieną offer su `qty_available>0` pagal pasirinktą `channel`
- po to likusios (out-of-stock)

Jei norite out-of-stock visai nerodyti, naudokite `in_stock_only=true`.

#### `feature` formatas

`feature` yra sąrašas porų `feature_code:feature_value`, atskirtų kableliais:

- `feature=composition:cotton,season:winter`

Reikšmė atitinka `FeatureValue.value`.

#### `option` formatas

`option` yra sąrašas porų `option_type_code:option_value_code`, atskirtų kableliais:

- `option=size:m,color:black`

Reikšmė atitinka `OptionValue.code`.

Pastaba: `option_value_code` nėra garantuotai CSS spalva (pvz. `black` gali būti slug'as). UI turi remtis `OptionType.display_type`/`swatch_type` (žr. žemiau), o ne bandyti interpretuoti kodą.

### Pavyzdžiai

- Visos prekės:
  - `/api/v1/catalog/products?country_code=LT&page=1&page_size=24`

- Visos prekės + filtrai:
  - `/api/v1/catalog/products?country_code=LT&brand_slug=nike&feature=season:winter&option=size:m`

- Kategorija `{slug}` (įskaitant dukterines kategorijas):
  - `/api/v1/catalog/products?country_code=LT&category_slug=shoes`

- Brand `{slug}`:
  - `/api/v1/catalog/products?country_code=LT&brand_slug=nike`

- Product Group `{code}`:
  - `/api/v1/catalog/products?country_code=LT&group_code=air-max`

## Facets (filtrų UI generavimui)

### GET `/api/v1/catalog/products/facets`

Grąžina „galimus filtrus“ pagal pasirinktą scope (t.y. pagal tai, ką šiuo metu rodo listingas).

Priima tuos pačius query parametrus kaip ir produktų listingas:

- `country_code`, `channel`, `q`, `category_slug`, `brand_slug`, `group_code`, `feature`, `option`

Atsakymas (`CatalogFacetsOut`) apima:

- `categories` — jei pasirinkta `category_slug`, grąžina tik tos kategorijos dukterines kategorijas, kurios turi produktų šiame scope; jei kategorija nepasirinkta, grąžina tik top-level kategorijas (parent null), kurios turi produktų.

Svarbu: „turi produktų“ reiškia, kad dukterinė kategorija įtraukiama net jei produktai yra ne tiesiogiai joje, o jos gilesniuose descendants (grandchildren ir t.t.).
- `brands` — brandai, kurie egzistuoja šiame scope
- `product_groups` — product groupai, kurie egzistuoja šiame scope
- `features` — tik filterable features, kurios naudojamos šiame scope, su tik tomis value reikšmėmis, kurios realiai pasitaiko
- `option_types` — option type ašys šiame scope, su tik tomis value reikšmėmis, kurios realiai pasitaiko per variantus

Papildomai UI'ui:

- `option_types[].display_type` — rekomenduojamas atvaizdavimo tipas (`select` | `radio` | `swatch`)
- `option_types[].swatch_type` — jei `display_type=swatch`, nurodo kaip interpretuoti swatch'ą (pvz. `name`)

Šie laukai yra DB-driven (iš `OptionType.display_type` ir `OptionType.swatch_type`). Šiuo metu default visiems esamiems option tipams: `display_type=radio`.

### OptionType UI metaduomenys (DB-driven)

`OptionType` turi UI metaduomenis, kurie grąžinami tiek per `GET /catalog/option-types`, tiek per `GET /catalog/products/facets`:

- `display_type`:
  - `select` — dropdown
  - `radio` — radio buttons (tinka size/material ir pan.)
  - `swatch` — swatch/grid (dažniausiai spalvoms)
- `swatch_type` (naudojama tik kai `display_type=swatch`):
  - `hex` — `OptionValue.code` arba atskiras laukas turi būti hex (jei tokį naudosim ateityje)
  - `name` — rodyti `OptionValue.label` kaip tekstinį swatch (pvz. „Juoda“)
  - `image` — swatch paveikslėliai (jei tokį lauką pridėsim ateityje)

Rekomendacija:

- spalvoms: `display_type=swatch`, `swatch_type=name` (kol neturim hex/image)
- dydžiams: `display_type=radio`

### Admin (kur pildyti)

Admin'e:

- `Catalog -> Option types`:
  - nustatyk `display_type` ir (jei reikia) `swatch_type`
  - `Option values` (inline) yra filtro reikšmės (`code` + `label`)

Pavyzdys:

- `/api/v1/catalog/products/facets?country_code=LT&category_slug=shoes&brand_slug=nike`

## Lookup endpointai (bendram UI)

Šie endpointai skirti užsipildyti „globalius“ sąrašus (pvz. filtrų konfigūrai, admin UI, cache’ui).

- `GET /api/v1/catalog/categories`
- `GET /api/v1/catalog/brands`
- `GET /api/v1/catalog/product-groups`
- `GET /api/v1/catalog/features`
- `GET /api/v1/catalog/option-types`

Pastaba: `option-types` taip pat grąžina `display_type` ir `swatch_type`, kad frontas galėtų teisingai atvaizduoti filtrus (pvz. spalvas).

## Notify me (back-in-stock)

### POST `/api/v1/catalog/back-in-stock/subscribe`

Skirta registruoti vartotojo email, kad būtų galima pranešti, kai prekė/variantas vėl atsiras sandėlyje.

Body:

- `email` (required)
- `product_id` (optional)
- `variant_id` (optional)
- `channel` (optional, default `normal`) — `normal` arba `outlet`

Pastaba: privaloma paduoti bent vieną iš `product_id` arba `variant_id`.

Response:

- `{"status": "ok"}`

Elgsena:

- Prenumerata yra idempotentinė (pakartotinis subscribe su tais pačiais laukais nekuria dublikatų).
- Jei prenumerata buvo išjungta (pvz. jau išsiųsta) ir vartotojas subscribina dar kartą, prenumerata vėl aktyvuojama.
- Pranešimas siunčiamas automatiškai, kai konkretaus `variant` (arba bet kurio `product` varianto) `qty_available` pereina iš `0` į `>0`.
- `channel` yra svarbus:
  - `channel=normal` siunčia, kai atsiranda `InventoryItem.offer_visibility=NORMAL`
  - `channel=outlet` siunčia, kai atsiranda `InventoryItem.offer_visibility=OUTLET`

Pavyzdžiai:

- Prenumeruoti visą produktą (bet kuris variantas):
  - `POST /api/v1/catalog/back-in-stock/subscribe`
  - body: `{"email":"a@b.com","product_id":79,"channel":"normal"}`
- Prenumeruoti konkretų variantą:
  - body: `{"email":"a@b.com","variant_id":80,"channel":"normal"}`

Pastaba: email turinys siunčiamas pagal `notifications.EmailTemplate` su key `catalog_back_in_stock`.

## Fronto listing maršrutai (aliasai)

Šie endpointai yra patogumui, kad frontas galėtų laikyti aiškų REST maršrutą, bet filtrai ir paginacija lieka identiški kaip `GET /products`:

- Kategorija:
  - `GET /api/v1/catalog/categories/{slug}/products`
- Brand:
  - `GET /api/v1/catalog/brands/{slug}/products`
- Product group:
  - `GET /api/v1/catalog/product-groups/{code}/products`

Pastaba: alias endpointuose `slug/code` automatiškai map’inamas į `category_slug/brand_slug/group_code`.

## Product detail papildomi laukai

### GET `/api/v1/catalog/products/{slug}`

Product detail atsakyme papildomai grąžinamas `features[]`:

- `feature_id`
- `feature_code`
- `feature_name`
- `value_id`
- `value`

Pavyzdys:

```json
{
  "features": [
    {
      "feature_id": 1,
      "feature_code": "composition",
      "feature_name": "Sudėtis",
      "value_id": 10,
      "value": "cotton"
    }
  ]
}
```

## Kainodara: offer nuolaidos ir cart perskirstymas (returned stock first)

### Terminai

- **List price**: bazinė varianto kaina (`Variant.price_eur`).
- **Offer price**: sandėlio/offer lygmens kaina, kuri gali būti pakeista per `InventoryItem.offer_price_override_eur` arba `InventoryItem.offer_discount_percent`.
- **Promo**: papildoma promo variklio nuolaida, kuri taikoma tik jei leidžiama (žr. `allow_additional_promotions`) ir jei offer nėra jau „discounted“ (nebent leidžiama).

### Product detail: kaip rodyti nuolaidą

`GET /api/v1/catalog/products/{slug}` (laukas `variants[]`):

- `price` — galutinė kaina (offer + promo).
- `compare_at_price` — list price (rodoma tik jei yra reali nuolaida).
- `discount_percent` — procentinė nuolaida nuo list price iki `price` (rounded int, arba `null` jei nėra).
- `offer_id` — kuris sandėlio offer šiuo metu laikomas „best offer“ (pagal prioritetą ir kainą).

Frontend rekomendacija:

- UI nuolaidos badge/strikethrough turi remtis `compare_at_price` ir `discount_percent` iš API.
- UI neturi bandyti pati perskaičiuoti nuolaidos pagal admin laukus; backend apskaičiuoja galutinę kainą.

### Cart: kodėl kaina „nesikeičia“ jei FE neatsinaujina iš API

Cart API grąžina kainas jau apskaičiuotas (offer + promo). Jei FE po `add`/`update` tik lokaliai pasikeičia qty (neperkraunant cart state iš response), UI gali likti su sena kaina.

Taisyklė:

- Po **kiekvieno** `POST /api/v1/checkout/cart/items` ir `PATCH /api/v1/checkout/cart/items/{id}` FE turi atnaujinti visą cart state pagal response (arba bent jau atnaujinti konkretų item’ą su `unit_price`, `line_total`, `compare_at_price`, `discount_percent`).

### Cart: automatinis qty „split“ per offer’ius

Kai FE kviečia `POST /api/v1/checkout/cart/items` su `variant_id` ir **be** `offer_id`, backend elgiasi taip:

- suranda visus `InventoryItem` (offers) su `qty_available>0` ir `offer_visibility=NORMAL`
- surikiuoja juos pagal:
  - `offer_priority` (desc)
  - efektyvią offer kainą (asc)
  - `id` (asc)
- **pirmiausia** alokuoja qty į pirmą offer (pvz. grąžinimų sandėlį)
- jei qty didesnis nei to offer likutis, likusi dalis alokuojama į kitus offer’ius
- jei visų offer’ių neužtenka, likutis dedamas kaip cart item su `offer_id=null` (generic/supplier fulfillment)

Tai reiškia:

- vienas user veiksmas „įdėti qty=10“ gali sukurti **kelias** cart eilutes.
- kainos gali skirtis tarp eilučių (pvz. returned offer su 50% nuolaida ir likusi dalis už pilną kainą).

Frontend UI rekomendacijos:

- rodyti cart eilutes kaip grąžina API (kiekviena eilutė turi savo `unit_price`/`line_total`).
- jei norite UI rodyti „vieną produktą“, galima grupuoti pagal `variant_id`, bet tada reikia:
  - sumuoti `qty`
  - sumuoti `line_total`
  - atskirai atvaizduoti sub-eilutes arba „nuo X €/vnt“ logiką (nes blended unit price gali klaidinti)

### Jei FE nori įdėti į konkretų offer (force)

Galima paduoti `offer_id` į `POST /api/v1/checkout/cart/items`.

- Tokiu atveju backend kuria / didina **vieną** cart eilutę tam konkrečiam offer.
- Tai naudinga, jei FE leidžia vartotojui pasirinkti konkrečią būklę/sandėlį (pvz. returned A vs NEW).

