# Catalog i18n (Categories / Brands)

Šis dokumentas aprašo **FE kontraktą** ir **backend elgseną** katalogo i18n'ui (pirmas etapas):

- Category / Brand lokalizuoti laukai (name/description/SEO)
- lokalizuotas **SEO slug** (localized slug)
- kaip FE turi konstruoti SEO URL
- kaip API pasirenka kalbą (`language_code`) ir kaip veikia fallback
- kaip veikia `/api/v1/{lang}/...` prefiksas ir query-param suderinamumas

## Sąvokos

- **Base (default) laukai**: `Category.name`, `Category.slug`, `Brand.name`, `Brand.slug` ir kt. (kaip buvo iki i18n).
- **Translation laukai**: įrašai lentelėse `CategoryTranslation` / `BrandTranslation`, kurie perrašo base laukus konkrečiai kalbai.
- **Localized slug**: `CategoryTranslation.slug` / `BrandTranslation.slug` — slug, kurį FE naudoja URL'e konkrečiai locale.

## Duomenų modelis (realybė)

- `catalog.CategoryTranslation`:
  - `category` (FK)
  - `language_code`
  - `name`, `slug`, `description`
  - `seo_title`, `seo_description`, `seo_keywords`
  - unikalumas:
    - `(category, language_code)`
    - `(language_code, slug)`

- `catalog.BrandTranslation`:
  - `brand` (FK)
  - `language_code`
  - `name`, `slug`, `description`
  - `seo_title`, `seo_description`, `seo_keywords`
  - unikalumas:
    - `(brand, language_code)`
    - `(language_code, slug)`

Pastaba: unikalumas `(language_code, slug)` reiškia, kad tame pačiame locale **negali būti dviejų skirtingų kategorijų/brand'ų su tuo pačiu localized slug**.

## Kalbos parinkimas (language resolution)

Backend kalbą parenka per `api.i18n.get_request_language_code(...)`.

### Prioritetai

1) **Path prefiksas**: `/{API_BASE_PATH}/v1/{language_code}/...`
2) **Query param**:
   - bendras: `?lang=...` (konfigūruojama per `LANGUAGE_QUERY_PARAM`, default `lang`)
   - katalogui: `?language_code=...` (palikta dėl backward-compat ir aiškaus FE kontrakto katalogo endpointams)
3) `Accept-Language` header
4) `request.site.default_language_code` / `request.site.config.default_language_code`
5) `settings.LANGUAGE_CODE`

### Fallback grandinė (translations)

Kai reikia parinkti vertimą (categories/brands output ar detail resolve), backend naudoja `translation_fallback_chain(language_code)`.

Grandinė yra:

- prašoma kalba (jei yra)
- `settings.LANGUAGE_CODE` bazinė (pvz. `lt` iš `lt-LT`)
- visos `SUPPORTED_LANGUAGE_CODES` / `LANGUAGES` (deduplikavus)

Iš šios grandinės parenkamas **pirmas rastas** vertimas.

## API endpointai

### GET `/api/v1/catalog/categories`

- Priima `language_code` (optional)
- Grąžina kategorijų medį (parent-child per `parent_id`)
- Kiekvienai kategorijai `slug/name/description/seo_*` grąžinami iš vertimo (pagal fallback chain), jei toks rastas; kitu atveju — base laukai.

Svarbu FE'ui:

- `CategoryOut.slug` **jau yra localized** (jei vertimas egzistuoja) ir turi būti naudojamas SEO URL konstravimui.

### GET `/api/v1/catalog/categories/{slug}`

- `slug` gali būti:
  - localized slug (iš `CategoryTranslation.slug`)
  - arba base slug (iš `Category.slug`) — fallback

Resolve taisyklė:

1) bandoma rasti `CategoryTranslation` pagal `slug` ir `fallback chain`
2) jei nerasta, bandoma `Category.slug`

### GET `/api/v1/catalog/brands`

Analogija kaip `categories`:

- `BrandOut.slug` grąžinamas localized, jei yra vertimas
- `name/description/seo_*` taip pat per vertimą

### GET `/api/v1/catalog/brands/{slug}`

Analogija kaip `category_detail`:

- `slug` gali būti localized arba base
- resolve pirma per `BrandTranslation.slug`, po to per `Brand.slug`

## Catalog i18n (2 etapas): Product Groups / Features / Options

Ši dalis aprašo FE kontraktą ir backend elgseną i18n'ui šiems katalogo objektams:

- `ProductGroup` (produktų grupės)
- `Feature` / `FeatureValue` (charakteristikos)
- `OptionType` / `OptionValue` (variantų opcijos)

### Duomenų modelis (realybė)

- `catalog.ProductGroupTranslation`:
  - `product_group` (FK)
  - `language_code`
  - `name`, `slug`, `description`
  - unikalumas:
    - `(product_group, language_code)`
    - `(language_code, slug)`

- `catalog.FeatureTranslation`:
  - `feature` (FK)
  - `language_code`
  - `name`
  - unikalumas: `(feature, language_code)`

- `catalog.FeatureValueTranslation`:
  - `feature_value` (FK)
  - `language_code`
  - `value`
  - unikalumas: `(feature_value, language_code)`

- `catalog.OptionTypeTranslation`:
  - `option_type` (FK)
  - `language_code`
  - `name`
  - unikalumas: `(option_type, language_code)`

- `catalog.OptionValueTranslation`:
  - `option_value` (FK)
  - `language_code`
  - `label`
  - unikalumas: `(option_value, language_code)`

### API endpointai

#### GET `/api/v1/catalog/product-groups`

- Priima `language_code` (optional)
- Grąžina `slug/name/description` lokalizuotai (per fallback chain), jei yra vertimas; kitu atveju — base laukai.

Svarbu FE'ui:

- `ProductGroupOut.slug` yra **localized** (jei vertimas egzistuoja) ir turi būti naudojamas SEO URL (jei FE turi product-group landing page).

#### GET `/api/v1/catalog/product-groups/{slug}`

- `slug` gali būti:
  - localized slug (iš `ProductGroupTranslation.slug`)
  - arba base slug (iš `ProductGroup.slug`) — fallback

Resolve taisyklė:

1) bandoma rasti `ProductGroupTranslation` pagal `slug` ir `fallback chain`
2) jei nerasta, bandoma `ProductGroup.slug`

#### GET `/api/v1/catalog/features`

- Priima `language_code` (optional)
- Grąžina feature list/facet'ams tinkamą output.
- Lokalizuoja:
  - `Feature.name`
  - `FeatureValue.value`

Pastaba FE'ui:

- Feature/facet'ai neturi localized slug šiame etape; identifikavimui naudokite `id` / `code`.

#### GET `/api/v1/catalog/option-types`

- Priima `language_code` (optional)
- Lokalizuoja:
  - `OptionType.name`
  - `OptionValue.label`

Pastaba FE'ui:

- Variantų pasirinkimo UI (pvz. dydžiai, spalvos) turi rodyti localized `OptionType.name` ir `OptionValue.label` pagal pasirinktą locale.

## Product (PDP) i18n

Ši dalis aprašo FE kontraktą produkto puslapiui.

### Duomenų modelis (realybė)

- `catalog.ProductTranslation`:
  - `product` (FK)
  - `language_code`
  - `name`, `slug`, `description`
  - `seo_title`, `seo_description`, `seo_keywords`
  - unikalumas:
    - `(product, language_code)`
    - `(language_code, slug)`

### API endpointai

#### GET `/api/v1/catalog/products/{slug}`

- Priima `language_code` (optional) (taip pat veikia `/api/v1/{lang}/...` prefiksas)
- `slug` gali būti:
  - localized slug (iš `ProductTranslation.slug`)
  - arba base slug (iš `Product.slug`) — fallback

Grąžinami lokalizuoti laukai (per fallback chain):

- `ProductDetailOut.slug`
- `ProductDetailOut.name`
- `ProductDetailOut.description`
- `ProductDetailOut.seo_title`
- `ProductDetailOut.seo_description`
- `ProductDetailOut.seo_keywords`

Taip pat PDP response'e lokalizuojami (kai taikoma):

- `features[].name` (FeatureTranslation)
- `features[].value` (FeatureValueTranslation)
- `variants[].option_type_name` (OptionTypeTranslation)
- `variants[].option_value_label` (OptionValueTranslation)

Svarbu FE'ui:

- FE turi laikyti `product.slug` kaip **canonical localized slug** ir naudoti jį URL generavimui.
- Jei vartotojas ateina su senu base slug URL, endpoint'as vis tiek suras produktą, bet atsakyme grąžins localized `slug` (jei toks yra), todėl FE gali (rekomenduojama) padaryti 301/replace į canonical URL.

### SEO URL konstravimas (FE) produkto puslapiui

Šiame etape backend grąžina tik localized `product.slug`.

Rekomenduojamas FE kelias:

- Product URL: `/p/{slug}` (arba pagal jūsų FE route'ą)

Jei jūsų FE naudoja kitą struktūrą (pvz. `/product/{slug}`), naudokite localized `slug` iš API.

## Rekomenduojamas FE pattern'as: canonical locale + redirect

Kad SEO URL būtų stabilūs:

1) FE turi pasirinkti aktyvų locale (`lt/lv/et/pl`) ir visur jį perduoti:
   - arba per `/api/v1/{lang}/...`
   - arba per `?language_code={lang}` (katalogui)
2) Detail puslapiuose (category/brand/product-group/product) FE turėtų:
   - call'inti su tuo slug, kurį turi URL'e
   - jei response'e `slug` nesutampa su URL slug — performinti redirect/replace į canonical localized slug

## SEO URL konstravimas (FE)

Backend duoda tik `slug` (localized) ir (jei reikia) `category_path_template` / `brand_path_template` per `SiteConfig` (šiuo metu template'ai yra DB laukai).

Rekomenduojamas FE kelias:

- Category URL: `/{category_path_template}` su `{slug}` pakeitimu.
  - default: `/c/{slug}`
- Brand URL: `/{brand_path_template}` su `{slug}` pakeitimu.
  - default: `/b/{slug}`

### Pavyzdžiai

Jei categories endpoint grąžino:

```json
{ "id": 10, "slug": "lovos", "name": "Lovos" }
```

tada FE category URL:

- `/c/lovos`

Jei kitoje kalboje grąžino:

```json
{ "id": 10, "slug": "beds", "name": "Beds" }
```

tada URL:

- `/c/beds`

## API route su language prefiksu

API palaiko **canonical i18n API URL** su prefiksu:

- `/api/v1/lt/catalog/categories`
- `/api/v1/en/catalog/brands`

Tai nekeičia endpointų kontrakto, tik įtakoja language resolution (path param turi prioritetą).

FE gali naudoti:

- arba `/api/v1/{lang}/...` (canonical)
- arba `/api/v1/...` + query param (`language_code` katalogui / `lang` bendrai)

## Backward compatibility

- Jei FE nenaudoja `language_code` ir nenaudoja `/api/v1/{lang}/...`, sistema vis tiek veiks per `Accept-Language` ir site default.
- Jei FE dar turi senus URL su base slug — detail endpointai vis dar randa objektą (fallback į base slug).

## Rekomendacijos admin turiniui

- Kiekvienam site rekomenduojama turėti aiškų `default_language_code` (per `SiteConfig`), kad be query param būtų stabilus fallback.
- Vertimuose (`CategoryTranslation.slug`, `BrandTranslation.slug`) laikyti SEO-friendly, locale-specific slugs.
