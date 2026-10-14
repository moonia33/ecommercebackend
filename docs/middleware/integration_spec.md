# Middleware integracijos specifikacija (Django target)

> **Deprecated (2026-10-15).** Šis spec'as aprašo seną būseną su `external_presta_id`
> ir be importų orchestration. Galiojantis kontraktas:
> [`docs/normalizer_integration.md`](../normalizer_integration.md).
> Importų pipeline realizuotas `catalog_import` app (`ImportRun`/`ImportItem`,
> ops API `/ops/imports/*`, `import_catalog`/`import_stock` komandos).

## Tikslas ir ribos

Šis dokumentas aprašo kontraktą tarp importų middleware ir esamos Django e-commerce platformos.

- Middleware atsakomybė:
  - šaltinių ingest (`file/api/html/scrape`)
  - normalizacija į vieningą schemą
  - validacija, deduplikacija, audit trail
  - deterministic upsert kvietimai į platformą
- Platformos atsakomybė:
  - saugoti canonical katalogą
  - kainų, likučių, publikavimo ir API atvaizdavimo logika

Dokumentas orientuotas į dabartinę projekto būseną (branch `bz`) ir dabartinius modelius.

## 1) Tikslinių modelių schema ir privalomi laukai

## 1.1 Core katalogas (MVP import target)

- `Category`:
  - required: `name`, `slug`
  - optional/import-critical: `external_presta_id`, `parent`, `description`, `is_active`
  - optional/media-seo: `hero_image_url`, `menu_icon_url`, `seo_title`, `seo_description`, `seo_keywords`
- `Brand`:
  - required: `name`, `slug`
  - optional/import-critical: `external_presta_id`, `is_active`
- `Product`:
  - required: `sku`, `name`, `slug`
  - strongly recommended: `tax_class`
  - optional/import-critical: `external_presta_id`, `brand`, `category`, `description`, `is_active`
  - optional/seo: `seo_title`, `seo_description`, `seo_keywords`
- `Variant`:
  - required: `product`, `sku`, `price_eur`
  - optional/import-critical: `external_presta_combination_id`, `barcode`, `cost_eur`, `is_active`
  - optional/logistics: `weight_g`, `length_cm`, `width_cm`, `height_cm`
- `Warehouse`:
  - required: `code`, `name`, `country_code`
  - optional: `city`, `dispatch_days_min`, `dispatch_days_max`, `is_active`
- `InventoryItem`:
  - required: `variant`, `warehouse`
  - required/ops: `qty_on_hand` (>=0), `qty_reserved` (>=0, <= `qty_on_hand`)
  - optional/pricing-offer: `cost_eur`, `offer_price_override_eur`, `offer_discount_percent`, `offer_visibility`, `offer_priority`, `offer_label`

## 1.2 Vertimai ir lokalizacija

Esamoje bazėje yra:

- `CategoryTranslation`
- `BrandTranslation`
- `ProductTranslation`

Middleware turi dirbti taip:

- bazinė kalba į core (`Category`, `Brand`, `Product`)
- kitos kalbos į atitinkamus `*Translation` modelius
- fallback grandinė API lygyje lieka tokia pati (žr. žemiau)

Kalbos fallback API lygyje:

1. `?lang=...`
2. `Accept-Language`
3. `Shop.default_language_code`
4. global `LANGUAGE_CODE`

## 2) Unikalumo taisyklės produktui ir supplier

## 2.1 Produktas ir variantas

- `Product.sku` yra globaliai unikalus.
- `Variant.sku` yra globaliai unikalus.
- `Product.external_presta_id` yra unikalus (kai naudojamas).
- `Variant.external_presta_combination_id` yra unikalus (kai naudojamas).

Upsert tvarka (rekomenduojama ir suderinta su esamais importais):

1. ieškoti pagal `external_source_id` (pvz. `external_presta_id`)
2. jei nerasta, ieškoti pagal `sku`
3. jei rasta pagal `sku` ir trūksta external id, backfill'inti external id

## 2.2 Supplier ir warehouse

Dabartiniame modelyje nėra atskiro `Supplier` modelio kataloge.

- rekomenduojamas kontraktas middleware pusėje:
  - turėti `source_supplier_id`
  - turėti deterministic map į `Warehouse.code`
- platformos pusėje unikalumas likučiui:
  - `InventoryItem` unikalus per (`variant`, `warehouse`)

Tai reiškia:

- vienas variantas gali turėti kelis offer/likučių įrašus per skirtingus sandėlius
- middleware neturi kurti dublių tam pačiam (`variant`, `warehouse`)

## 3) Kategorijos, atributai, feature reikšmės, lokalizacijos

## 3.1 Kategorijos

- kategorijos yra medis per `MPTT` (`Category.parent` + mptt techniniai laukai)
- importuojant iš šaltinio:
  - pirma importuoti viršūnes, tada vaikus (pagal `level_depth`)
  - root tipo technines kategorijas (pvz. Presta `1`, `2`) galima atmesti middleware lygyje

## 3.2 Atributai ir feature

- feature:
  - `Feature` (unikalus `code`)
  - `FeatureValue` (unikalus per `feature + value`)
  - `ProductFeatureValue` (unikalus per `product + feature_value`)
  - vertimai: `FeatureTranslation`, `FeatureValueTranslation`
- variant option:
  - `OptionType` (unikalus `code`)
  - `OptionValue` (unikalus per `option_type + code`)
  - `VariantOptionValue` (unikalus per `variant + option_type`)
  - vertimai: `OptionTypeTranslation`, `OptionValueTranslation`

Middleware taisyklė:

- normalizuoti feature/option kodus į stabilų formatą (`slug`/`code`)
- užtikrinti deterministic mapping prieš upsert

## 3.3 Lokalizacijos

- Visi `*Translation.language_code` validuojami per bendrą palaikomų kalbų sąrašą.
- Numatyti kodai: `lt`, `pl`, `en`; papildomi (pvz. `de`) įjungiami per `SUPPORTED_LANGUAGE_CODES`.
- Shop aktyvios kalbos valdomos per `ShopLanguage` (kanoninis šaltinis); `Shop.default_language_code` yra sinchronizuojamas default indikatorius.
- Po `SUPPORTED_LANGUAGE_CODES` pokyčių backend migracijos išvalo nebeleidžiamų kalbų įrašus.
- API fallback lieka vienodas visiems moduliam: `?lang` -> `Accept-Language` -> `Shop.default_language_code` -> global `LANGUAGE_CODE`.

## 4) Kainų, likučių ir publikavimo būsenų logika

## 4.1 Kainos

- bazinė produkto pardavimo kaina: `Variant.price_eur` (neto)
- optional cost: `Variant.cost_eur` ir/ar `InventoryItem.cost_eur`
- akcijinė offer logika per `InventoryItem`:
  - `offer_price_override_eur` (fiksuota)
  - arba `offer_discount_percent` (0..100)
  - `never_discount` gali blokuoti papildomas akcijas

Shop/B2B sluoksnis:

- `ShopPriceList` + `ShopPriceRule` leidžia per-shop/per-channel (`retail|b2b`) taisykles
- middleware turi paduoti bazines kainas; shop kainodara gali būti taikoma platformoje

## 4.2 Likučiai

- canonical likutis:
  - `qty_available = qty_on_hand - qty_reserved`
- rezervacijos (`qty_reserved`) keičiasi checkout metu; middleware neturi jų perrašinėti „aklai“
- middleware turėtų atnaujinti:
  - `qty_on_hand`
  - optional `cost_eur`
  - bet ne reset'inti `qty_reserved`

## 4.3 Publikavimas

- produktų/variantų/kategorijų/brand publish flag:
  - `is_active`
- offer kanalizavimas:
  - `InventoryItem.offer_visibility = NORMAL | OUTLET`

Middleware rekomendacija:

- skirti `source_active` ir `publish_active`
- platformai paduoti galutinį `is_active` pagal aiškias verslo taisykles

## 5) Staging, import jobs, audit modelis

Trumpas atsakymas: reikia kurti middleware pusėje.

Dabartinė platforma turi vienkartinių import commandų pavyzdžius, bet neturi pilno importų orchestration domeno:

- nėra bendro staging sluoksnio
- nėra standardizuoto `import_job` gyvavimo ciklo visiems tiekėjams
- nėra vieningo dead-letter/retry/audit režimo importų domenui

Todėl rekomenduojama middleware minimali schema:

- `source_batch`
- `staging_record`
- `normalize_result`
- `import_job`
- `import_job_item`
- `import_audit_event`
- `dead_letter_item`

Detalus lifecycle aprašymas pateiktas atskirame faile:

- `docs/middleware/import_job_lifecycle.md`

## 6) Išorinės nuotraukų nuorodos pereinamuoju laikotarpiu

Dabartinis modelis palaiko pereinamą variantą:

- `ProductImage.image_url` (external source URL)
- kartu gali būti lokaliai sugeneruoti failai (`image`, `image_avif`, `image_webp`, `listing_avif`, `listing_webp`)

Pereinamojo laikotarpio strategija:

1. Middleware paduoda `image_url` + metaduomenis (`alt_text`, `sort_order`).
2. Platforma rodo external URL, kol nėra local ingest.
3. Asinchroninis ingest job atsisiunčia failą į local/S3.
4. Po ingest atnaujinami local laukai, external URL paliekamas traceability.

Privalomos praktikos:

- dedupe pagal canonicalized URL + content hash
- saugoti `source_image_url` audit trail
- neperrašyti rankinių redagavimų admin'e be explicit flag

## Siūlomas middleware payload kontraktas (MVP)

Vienam produkto įrašui:

- identity:
  - `source_system` (required)
  - `external_product_id` (required)
  - `external_variant_id` (optional)
  - `sku` (required)
- content:
  - `name` (required)
  - `slug` (required or generated deterministic)
  - `description_markdown` (recommended)
  - `seo_title`, `seo_description`, `seo_keywords` (optional)
- relations:
  - `brand_external_id` (optional)
  - `category_external_id` (optional but recommended)
  - `tax_class_code` (required by business, fallback `standard`)
- pricing:
  - `price_eur_net` (required)
  - `cost_eur_net` (optional)
- stock:
  - `warehouse_code` (required)
  - `qty_on_hand` (required)
- status:
  - `is_active` (required)
  - `offer_visibility` (optional, default `NORMAL`)
- media:
  - `images[]` su bent `url` arba failo reference

## Rollout etapai

1. Įdiegti middleware staging + job lifecycle (be write į platformą).
2. Įjungti dry-run normalization su pilna validacija.
3. Įjungti deterministic upsert į platformą mažais batch.
4. Įjungti audit dashboards/KPI.
5. Įjungti image ingest pipeline.
6. Stabilizavus, pereiti į periodinius/scheduled importus.

## Pagrindiniai KPI middleware kokybei

- `import_success_rate`
- `idempotent_replay_diff_rate` (turėtų artėti prie 0)
- `mapping_miss_rate` (category/brand/tax/warehouse)
- `price_validation_error_rate`
- `stock_conflict_rate`
- `image_ingest_success_rate`
