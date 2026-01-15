# Meilisearch integration – catalog search

Šitas dokumentas aprašo kaip projekte integruotas Meilisearch produktų paieškai, kokia yra elgsena (q-only startas), kokie yra FE kontraktai ir kaip tai deploy’inti/prižiūrėti production.

Susijęs dokumentas:

- `docs/catalog_filters.md` — bendras listingų filtrų kontraktas (query param formatai).

## Tikslas ir scope

- Meilisearch naudojamas **tik kai yra `q`** (q-only režimas).
- Be `q` (browse/listing) sistema naudoja esamą DB logiką.
- Jei Meilisearch nepasiekiamas arba grąžina klaidą, endpointai automatiškai daro **fallback į DB** (eilutės filtravimas su `icontains`).

## Endpointai

Kanoniniai endpointai:

- `GET /api/v1/catalog/products`
- `GET /api/v1/catalog/products/facets`

Aliasai (naudoja tą patį kontraktą):

- `GET /api/v1/catalog/categories/{slug}/products`
- `GET /api/v1/catalog/brands/{slug}/products`
- `GET /api/v1/catalog/product-groups/{code}/products`

Query parametrų formatas (feature/option/sort/pagination ir pan.) aprašytas `docs/catalog_filters.md`.

## Kada Meili naudojamas

### `GET /catalog/products`

- Jei `q` yra `None` arba tuščias string’as → DB listing.
- Jei `q` yra ne tuščias → bandoma Meili:
  - Meili grąžina `ids` (produkto ID listą) ir tada produktai paimami iš DB pagal tuos ID.
  - DB rezultatai išrikiuojami **pagal Meili hit order**.
  - Jei Meili klaida (timeout, 5xx, neteisingas key) → DB fallback su `icontains`.

Svarbu:

- Meili šiame etape naudojamas tik “ID retrieval” (hits -> product ids). Pilnas listing’ų sort/filter gali būti perkeltas į Meili vėliau.

### `GET /catalog/products/facets`

- Jei `q` yra ne tuščias → bandoma Meili `facetDistribution`.
- Jei Meili klaida → DB fallback (kaip iki šiol).

## Site visibility (multi-site)

Meili indeksuojant produktus įrašoma per-site matomumo informacija pagal esamas assortment taisykles:

- `SiteCategoryVisibility` (allow-list su descendants)
- `SiteBrandExclusion` (brand exclude)
- `SiteCategoryBrandExclusion` (category scope brand exclude)

Paieškos metu backend paduoda `site_id` (iš `request.site`) į Meili filtrą, kad **frontend niekada negautų nevisible produktų**.

## Stock-aware semantika

- `in_stock_only=true` listingui reiškia: grąžinti tik produktus, kurie turi bent vieną in-stock offer šiame `channel`.
- Facetų dalyje `option_types` yra **stock-aware** kai naudojamas Meili:
  - `channel=normal` naudoja `option_value_ids_in_stock_normal`
  - `channel=outlet` naudoja `option_value_ids_in_stock_outlet`

Tai leidžia FE rodyti tik realiai pasirenkamas option values (pagal realų sandėlio stock’ą per channel).

## Fallback elgsena (resiliency)

Fallback aktyvuojamas, kai:

- Meili host nepasiekiamas (network, docker down)
- neteisingas API key
- Meili grąžina 4xx/5xx
- backend timeout

Fallback principai:

- `GET /products` su `q` → DB `icontains` pagal `name/slug/sku`
- `GET /products/facets` su `q` → DB facetų agregacija kaip iki šiol

Pastaba:

- Fallback yra saugus funkcionaliai, bet gali būti lėtesnis.

## Konfigūracija (.env / settings)

Reikalingi env:

- `MEILI_ENABLED` (bool) — įjungia integraciją.
- `MEILI_HOST` — pvz. `http://localhost:7700`
- `MEILI_API_KEY` — backend’o raktas (production’e laikyti kaip secret).
- `MEILI_PRODUCTS_INDEX` — pvz. `products_lt_v1` (default).

Rekomendacija:

- **Niekada** neeksponuoti Meili master key frontend’ui.
- Jei kada nors reikės FE tiesioginių Meili request’ų, naudoti atskirą “search key” (restricted) ir atskirą threat model.

## Lokalūs paleidimo žingsniai (Docker)

### Minimalus startas

- Port mapping: `-p 7700:7700`
- Master key: `MEILI_MASTER_KEY` (tą pačią reikšmę backend’e naudok kaip `MEILI_API_KEY`)
- Persistence: volume į `/meili_data`

Pavyzdinis container paleidimas:

- `docker run --name meilisearch -p 7700:7700 -e MEILI_MASTER_KEY=... -v meili_data:/meili_data getmeili/meilisearch:v1.5`

## Index lifecycle

### Reindex

Management command:

- `python manage.py reindex_meili_products --reset`

Jei keitėsi tik settings (pvz. sinonimai), paprastai užtenka paleisti be `--reset`:

- `python manage.py reindex_meili_products`

Elgsena:

- sukuria index’ą (jei nėra)
- atnaujina index settings (searchable/filterable/sortable)
- jei `--reset`, ištrina visus dokumentus prieš upload
- uploadina product docs batch’ais

### Kada reikia reindex

- pakeitus document schema (pridėjus/pašalinus field)
- pakeitus settings (filterable/sortable/searchable)
- pakeitus assortment taisykles, jei jos turi įtaką indeksuojamiems laukams ir norite, kad tai atsispindėtų Meili

## Monitoring ir troubleshooting

### Health check

- Meili: `GET {MEILI_HOST}/health`

### Useful signal

- docker loguose ieškoti:
  - `/indexes/{index}/search` (paieška)
  - `/tasks` (async task status)

### Dažnos klaidos

- `index_already_exists`: normalu, jei index jau sukurtas.
- `invalid_api_key`: neteisingas `MEILI_API_KEY`.
- `invalid_document_fields`: doc schema nesutampa su settings / payload.

## Sinonimai (admin)

Sinonimai valdomi per admin’ą (DB) ir automatiškai siunčiami į Meili index settings.

Modelis:

- `search.SearchSynonym`
  - `language_code` (pvz. `lt`)
  - `term` (pvz. `kedai`)
  - `synonyms` (JSON list, pvz. `["sneakeriai", "sportiniai"]`)
  - `is_active`

Elgsena:

- Sinonimai yra **index-level** setting’as.
- Backend parenka `language_code` pagal `settings.LANGUAGE_CODE` (imamas prefix’as iki `-`, pvz. `lt-LT` -> `lt`).
- Pakeitus sinonimus admin’e, paleisk `python manage.py reindex_meili_products` (be reset), kad settings atsinaujintų.

## Production rekomendacijos

- Deploy’inti Meili kaip atskirą servisą (container) su:
  - persistent volume (`/meili_data`)
  - private network (nepublic endpoint, jei nereikia)
  - secrets management master key
- Monitoring:
  - health endpoint
  - container restart policy
- Rollout:
  - atlikti `reindex_meili_products` kaip atskirą jobą po deploy (arba pagal poreikį)

## FE rekomendacijos (performance)

- Jei darote “search-as-you-type”, naudokite debounce (pvz. 250–350ms), kad nesprogdintumėt `/search` request’ų.
- Dažniausiai FE daro 2 request’us: `/products` + `/products/facets`. Jei reikia optimizacijos, galima:
  - cache’inti facets per `q+filters` raktą
  - arba daryti facets rečiau (pvz. tik po debounce).
