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
- `page` (optional, default 1)
- `page_size` (optional, default 20; max 100)

#### `feature` formatas

`feature` yra sąrašas porų `feature_code:feature_value`, atskirtų kableliais:

- `feature=composition:cotton,season:winter`

Reikšmė atitinka `FeatureValue.value`.

#### `option` formatas

`option` yra sąrašas porų `option_type_code:option_value_code`, atskirtų kableliais:

- `option=size:m,color:black`

Reikšmė atitinka `OptionValue.code`.

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
- `brands` — brandai, kurie egzistuoja šiame scope
- `product_groups` — product groupai, kurie egzistuoja šiame scope
- `features` — tik filterable features, kurios naudojamos šiame scope, su tik tomis value reikšmėmis, kurios realiai pasitaiko
- `option_types` — option type ašys šiame scope, su tik tomis value reikšmėmis, kurios realiai pasitaiko per variantus

Papildomai UI'ui:

- `option_types[].display_type` — rekomenduojamas atvaizdavimo tipas (`select` | `radio` | `swatch`)
- `option_types[].swatch_type` — jei `display_type=swatch`, nurodo kaip interpretuoti swatch'ą (pvz. `name`)

Šie laukai yra DB-driven (iš `OptionType.display_type` ir `OptionType.swatch_type`). Šiuo metu default visiems esamiems option tipams: `display_type=radio`.

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

## Fronto listing maršrutai (aliasai)

Šie endpointai yra patogumui, kad frontas galėtų laikyti aiškų REST maršrutą, bet filtrai ir paginacija lieka identiški kaip `GET /products`:

- Kategorija:
  - `GET /api/v1/catalog/categories/{slug}/products`
- Brand:
  - `GET /api/v1/catalog/brands/{slug}/products`
- Product group:
  - `GET /api/v1/catalog/product-groups/{code}/products`

Pastaba: alias endpointuose `slug/code` automatiškai map’inamas į `category_slug/brand_slug/group_code`.
