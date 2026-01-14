# Djengo ecommerce backend

Django + Django Ninja e‑commerce backend API su PostgreSQL. Šiuo metu įgyvendinta: auth (email OTP + JWT), katalogas (categories/brands/products), kainodara su PVM (VAT), supplier importai (Žalioji banga), ir MVP checkout (cart → checkout → orders) su vienu pristatymo metodu.

## Statusas

- Šiuo metu aktyviai vystomas checkout/shipping (carrier plugin'ai), lipdukų generavimas ir tracking.

## Greitas startas (be Docker)

1. Susikurk `.env` (iš pavyzdžio)

- Nukopijuok `.env.example` → `.env`
- Pakoreguok `SECRET_KEY` ir `DATABASE_URL`

2. Migracijos ir adminas

- `C:/Pip/django_ecommerce/.venv/Scripts/python.exe manage.py migrate`
- `C:/Pip/django_ecommerce/.venv/Scripts/python.exe manage.py createsuperuser`

3. Paleidimas

- Patikimiausia (Windows / PowerShell): `./manage.ps1 runserver`
- Arba tiesiogiai: `C:/Pip/django_ecommerce/.venv/Scripts/python.exe .\manage.py runserver`

Patikra:

- Admin: `http://127.0.0.1:8000/admin/`
- Health: `http://127.0.0.1:8000/api/v1/health`

## Auth (API)

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me` (auth required; per HttpOnly cookies)
- `PATCH /api/v1/auth/me` (auth required; per HttpOnly cookies) – atnaujina `first_name/last_name`
- `PUT /api/v1/auth/consents` (auth required; per HttpOnly cookies)
- `GET /api/v1/auth/addresses` (auth required; per HttpOnly cookies)
- `POST /api/v1/auth/addresses` (auth required; per HttpOnly cookies)
- `PATCH /api/v1/auth/addresses/{address_id}` (auth required; per HttpOnly cookies)
- `DELETE /api/v1/auth/addresses/{address_id}` (auth required; per HttpOnly cookies)

### HttpOnly Cookie auth (rekomenduojama web frontui)

Backend'as naudoja **HttpOnly cookie auth**: `access_token` + `refresh_token`.

Kai naudojamas cookie režimas:

- `POST /auth/login`, `POST /auth/register`, `POST /auth/otp/verify`:
  - grąžina `{ "status": "ok" }`,
  - papildomai nustato `Set-Cookie` su `HttpOnly` tokenais.
- `POST /auth/refresh`:
  - `refresh` body yra optional (gali būti `null`/nepateiktas),
  - jei body nepateiktas – refresh tokenas paimamas iš `refresh_token` cookie,
  - atsakymas grąžina `{ "status": "ok" }` ir atnaujina `access_token` cookie.
- `POST /auth/logout`: ištrina auth cookies.

Frontui svarbu:

- Visi request'ai turi būti su `credentials: 'include'` / `withCredentials: true`.
- Tokiu atveju frontui nereikia laikyti `refresh_token` localStorage.

Susiję `.env` raktai (cookie auth):

- `AUTH_COOKIE_ACCESS_NAME` (default `access_token`)
- `AUTH_COOKIE_REFRESH_NAME` (default `refresh_token`)
- `AUTH_COOKIE_SAMESITE` (default `lax`; prod su `api.domenas.lt` rekomenduojama `none`)
- `AUTH_COOKIE_DOMAIN` (default tuščias; prod su subdomain'ais rekomenduojama `.domenas.lt`)

### Email OTP (rekomenduojamas scenarijus)

- `POST /api/v1/auth/otp/request` (atsiunčia kodą į email)
- `POST /api/v1/auth/otp/verify` (patikrina kodą ir grąžina JWT)

Susiję `.env` raktai:

- `ALLOW_GUEST_CHECKOUT`
- `JWT_ALGORITHM`, `JWT_ACCESS_TTL_MINUTES`, `JWT_REFRESH_TTL_DAYS`
- Email siuntimui: `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_TIMEOUT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`

Pastaba (dev): jei nori tiesiog matyti OTP kodą terminale, naudok `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`.

OTP laiškas valdomas per `notifications` šabloną `auth_otp_code`.

### Consent'ai ir grupės

- Consent tipai valdomi per admin: `Accounts -> Consent types`
- Vartotojo consent būsenos (logas) per admin: `Accounts -> User consents`
- Vartotojo segmentai/grupės su prioritetu per admin: `Accounts -> Customer groups` (priskiriama vartotojui per User admin)

Prioritetas: aukštesnis `priority` laimi (skirta tam, kad vėliau nuolaidos nesusumuotųsi ir būtų aišku, kuri taisyklė svarbiausia).

### Adresai ir telefonai

- Vartotojui galima priskirti kelis telefonus ir kelis adresus (admin’e arba vėliau per checkout UI).
- `/api/v1/auth/me` grąžina `phones` ir `addresses`.

Kad frontas galėtų po registracijos užpildyti profilį ir sukurti adresą:

- `PATCH /api/v1/auth/me` body pvz.: `{ "first_name": "Jonas", "last_name": "Jonaitis" }`
- `POST /api/v1/auth/addresses` body pvz.:
  - `{ "label": "Namai", "full_name": "Jonas Jonaitis", "line1": "Gedimino pr. 1", "city": "Vilnius", "postal_code": "01103", "country_code": "LT", "phone": "+3706...", "is_default_shipping": true, "is_default_billing": true }`

Pastaba: jei `is_default_shipping=true` arba `is_default_billing=true`, backend automatiškai nuims šį flag nuo kitų userio adresų (kad nepažeistų unikalumo).

B2B scenarijui:

- `CustomerGroup.allow_additional_discounts=False` reiškia, kad vėliau nuolaidų variklis turi ignoruoti akcijas/promocijas.
- `CustomerGroup.allow_coupons=False` reiškia, kad vėliau nuolaidų variklis turi ignoruoti kuponų kodus.

Taip galima turėti B2B grupę su didmenine kainodara ir pvz. leisti tik vienkartinius kuponus (įjungiant `allow_coupons=True`), bet vis tiek ignoruoti kitas akcijas.

## Notifications (email šablonai)

- Admin'e: `Notifications -> Email templates` (kurti/redaguoti šablonus)
- Siuntimo istorija: `Notifications -> Outbound emails` (tik peržiūra)

Šablonai yra daugiakalbiai ir per-site: `EmailTemplate` turi `site` + `language_code`, o unikalumas yra `(site, key, language_code)`.

Siuntimas (pvz. užsakymo būsenos pranešimui vėliau): naudok [notifications/services.py](notifications/services.py) funkciją `send_templated_email(template_key=..., to_email=..., context=..., language_code=..., site_id=...)`.

Kalbos parinkimas:

- Jei `language_code` nepaduotas – naudojamas `LANGUAGE_CODE`.
- Jei `language_code` paduotas – šablonas parenkamas per `translation_fallback_chain(language_code)`.

## Tikslai (MVP + plėtra)

- Multi-country architektūra, su galimybe paprastai apriboti iki vienos šalies/kalbos per nustatymus.
- Klientų paskyros (email OTP) ir konfigūruojamas guest checkout.
- Paieška per Meilisearch (atskiras Docker servisas) su fallback į DB paiešką.

Greito checkout (wizard) rekomenduojamas FE flow: `docs/fast_checkout.md`.

## Konfigūracija

- Viskas turi būti valdoma per `.env` (dev/prod profiliai).
- DB: PostgreSQL.

### i18n / kalbos

Default kalba yra `lt` (konfigūruojama per `.env` `LANGUAGE_CODE`).

Kalba parenkama vieningu principu per visą API:

- Jei yra `?lang=...` query param (konfigūruojama per `LANGUAGE_QUERY_PARAM`, default `lang`) – jis turi prioritetą.
- Jei nėra `?lang=...`, kalba bandoma nustatyti iš `Accept-Language` headerio.
- Jei nei vienas netinka – naudojamas `LANGUAGE_CODE`.

Single-language mode:

- Jei `LANGUAGES` turi tik vieną kalbą, `Accept-Language` ir `?lang` praktiškai nieko nekeičia (visada bus naudojamas default).

Pastaba: `?lang` yra skirtas patogiam testavimui/preview, rekomenduojamas standartinis kelias – `Accept-Language`.

## Catalog (MVP)

- Admin'e: `Catalog -> Categories/Brands/Products`
- Kainos šaltinis: `Variant.price_eur` (EUR-only, **neto**).
- Likučių šaltinis: `InventoryItem.qty_on_hand/qty_reserved` (sandėlio lygis). `qty_available = qty_on_hand - qty_reserved`.

### Offer (InventoryItem) koncepcija (frontui)

`InventoryItem` pas mus veikia kaip **Offer**: tai yra konkretus parduodamas vienetas (variantas + sandėlis + būklė + (akcijos parametrai)).

Svarbiausi laukai:

- **`condition_grade`**: `NEW`, `RETURNED_A`, `DAMAGED_B`
- **`offer_visibility`**: `NORMAL` arba `OUTLET`
- **`offer_priority`**: didesnis laimi (returned A paprastai bus didesnis priority)
- **`offer_label`**: tekstas UI badge (pvz. "Grąžinta prekė", "Pažeista pakuotė")
- **`offer_price_override_eur`**: fiksuota sale kaina (neto)
- **`offer_discount_percent`**: sale % nuo `Variant.price_eur`

Pagrindinis principas: **krepšelio eilutė turi būti pririšta prie konkretaus offer (`offer_id`)**, jei norim garantuoti returned/outlet miksą.

### Facetai (Feature + Value)

- Facetų aprašai: `Catalog -> Features` (pvz. `composition`, `season`)
- Reikšmės: pridedamos per Feature vidų (inline) ir priskiriamos produktui per `ProductFeatureValue` (Product edit lange).

## Catalog Enrichment (taisyklių variklis facetams)

Tikslas: automatiškai priskirti produktams `FeatureValue` (facetų reikšmes) pagal produkto tekstą (pavadinimą/aprašymą/SKU), panašiai kaip Vendure „facet enrichment“.

### Duomenų modeliai

Į `catalog/models.py` pridėti modeliai:

- `EnrichmentRule`
  - Taisyklė su `priority` ir `is_active`.
  - Scope filtrai: `brand`, `category` (+ `include_descendants`), `product_group`.
  - Matcher: `contains` arba `regex` (`pattern`, `extract_group`).
  - Ištrauktos reikšmės formatavimas: `value_format` (pvz. `decimal_trim`).
  - Priskyrimo targetas: `feature` (kuriam facetui priskirti) ir `value_template`/`fixed_value`.
- `EnrichmentRun`
  - Vykdymo istorija/auditas: `status`, `dry_run`, `triggered_by`, `started_at/finished_at`, `summary`, `error`.
- `EnrichmentMatch`
  - Auditas kiekvienam match’ui: `run`, `rule`, `product`, `matched_field`, `matched_text`, `extracted_value`, `action`.

### Admin integracija

`catalog/admin.py` pridėtos admin registracijos:

- `EnrichmentRuleAdmin` – taisyklių CRUD.
- `EnrichmentRunAdmin` – run istorija (read-only).
- `EnrichmentMatchAdmin` – match/audit įrašai (read-only).

### Vykdymo logika

Vykdymo „engine“ yra `catalog/enrichment.py`:

- Pagrindinė funkcija: `apply_enrichment_rules(dry_run=..., rule_ids=None, since=None, limit=None, triggered_by=None)`.
- Ji sukuria `EnrichmentRun`, praeina per produktus ir taiko aktyvias taisykles pagal `priority`.

`dry_run` režimas:

- Sukuria `EnrichmentRun` + `EnrichmentMatch` (audit lieka).
- Neįrašo realių priskyrimų į `ProductFeatureValue` (tik simuliuoja, ką būtų priskyręs).

Pastaba: management command (CLI) yra `python manage.py enrich_catalog`.

### Paleidimas per management command

Komanda: `python manage.py enrich_catalog`

Dažniausi pavyzdžiai:

- Dry-run (tik auditas, be priskyrimų):
  - `python manage.py enrich_catalog --dry-run`
- Paleisti tik vieną taisyklę:
  - `python manage.py enrich_catalog --dry-run --rule-id 12`
  - `python manage.py enrich_catalog --dry-run --rule-id 12 --rule-id 13`
- Apdoroti tik neseniai atnaujintas prekes:
  - `python manage.py enrich_catalog --dry-run --since 2026-01-14T00:00:00`
- Apriboti apdorojamų produktų kiekį (debug):
  - `python manage.py enrich_catalog --dry-run --limit 50`
- Nurodyti kas paleido (įrašoma į `EnrichmentRun.triggered_by`):
  - `python manage.py enrich_catalog --dry-run --user-id 1`

### Variantai (Options + Variants)

- Option ašys: `Catalog -> Option types` (pvz. `size`, `color`, `cast_weight`)
- Variantas: `Catalog -> Variants` (SKU, kaina, likutis)
- Varianto reikšmės: `VariantOptionValue` (Variant edit lange)

Pastaba: jei produktas yra "paprastas", seed migracija sukuria 1 default variantą iš produkto `sku/price_eur/stock_qty` (jei produktų dar nėra, nieko nekuria).

## Multi-site (foundation)

Projektas turi pradėtą multi-site architektūros pagrindą (kol kas one-site režimu).

Kas įdiegta:

- `api.Site` modelis (Admin: `Api -> Sites`) su `code` ir `primary_domain`.
- `api.middleware.SiteMiddleware` nustato `request.site` pagal `Host` (fallback į `Site(code="default")`).
- `cms.CmsPage` yra scoped per site: unikalumas `(site, slug)`.
- `catalog.ContentBlock` yra scoped per site: unikalumas `(site, key)`.
- `GET /api/v1/cms/pages/{slug}` filtruoja pagal `request.site`.
- Product detail `content_blocks` (`GET /api/v1/catalog/products/{slug}`) parenka blokus pagal `request.site`.

Site-level assortment (katalogo matomumas per site):

- `catalog.SiteCategoryVisibility` — allow-list kategorijoms per site.
  - jei `include_descendants=true`, tada automatiškai matosi ir visos subkategorijos.
  - jei site neturi nei vienos `SiteCategoryVisibility`, katalogas elgiasi kaip iki šiol (rodo viską).
- `catalog.SiteBrandExclusion` — globalus brand exclude per site.
- `catalog.SiteCategoryBrandExclusion` — brand exclude konkrečiai kategorijai (su optional descendants).

Kaip konfigūruoti (rekomenduojamas kelias):

- Admin -> `Api -> Sites`: susikurk `Site` su `primary_domain` (pvz. `miegui.lt`).
- Admin -> `Catalog -> Categories`: pažymėk 1–kelias root kategorijas ir per bulk action `Pridėti į site visibility` su `include_descendants=true`.
- (optional) Admin -> `Catalog -> Site brand exclusions` ir/ar `Site category brand exclusions`.

Kaip testuoti per API:

- `SiteMiddleware` site’ą parenka pagal `Host` header. Lokaliai galima testuoti taip:
  - `curl -H "Host: miegui.lt" "http://127.0.0.1:8000/api/v1/catalog/categories"`
  - `curl -H "Host: miegui.lt" "http://127.0.0.1:8000/api/v1/catalog/products?country_code=LT"`
  - `curl -H "Host: miegui.lt" "http://127.0.0.1:8000/api/v1/catalog/products/facets?country_code=LT"`

Pastaba: kad testas veiktų, `ALLOWED_HOSTS` turi turėti ir tą host’ą (pvz. `miegui.lt`).

Migracijos:

- `api.0001_initial` sukuria `Site` ir default įrašą `code=default`.
- `cms.0006_cmspage_site_scope` backfill’ina `CmsPage.site` į default.
- `catalog.0021_contentblock_site_scope` backfill’ina `ContentBlock.site` į default.
- `catalog.0022_site_assortment_rules` sukuria site assortment lenteles (`SiteCategoryVisibility`, `SiteBrandExclusion`, `SiteCategoryBrandExclusion`).

Front-end integracija (CORS / CSRF / domenai):

- `ALLOWED_HOSTS` — turi turėti visus domenus, per kuriuos ateina request’ai į Django (kitaip gausi `DisallowedHost`).
- `CORS_ALLOWED_ORIGINS` — front-end origin’ai, kuriems leidžiami cross-origin request’ai.
- `CSRF_TRUSTED_ORIGINS` — reikalinga, jei naudoji session/cookie auth ir darai `POST/PUT/PATCH/DELETE` iš kito origin’o.
- `CORS_ALLOW_CREDENTIALS=true` — jei naudoji cookies (session/JWT cookies), tai turi būti įjungta.

Šiame projekte tai valdoma per env (`config/settings_base.py`):

- `CORS_ALLOWED_ORIGINS` (pvz. `http://localhost:5173,https://s-xxl.lt,https://www.s-xxl.lt`)
- `CSRF_TRUSTED_ORIGINS` (pvz. `https://s-xxl.lt,https://www.s-xxl.lt`)
- `ALLOWED_HOSTS` (pvz. `s-xxl.lt,www.s-xxl.lt,api.s-xxl.lt`)

Pastaba dėl multi-site: `api.Site.primary_domain` naudojamas request’o site resolvinimui (pagal `Host`), bet CORS/CSRF yra **security whitelist’ai** ir paprastai paliekami kaip explicit konfigūracija per env (kad neatsidarytų netyčia visiems domenams). Ateityje, jei norėsi, galima sukurti helperį kuris sugeneruoja recommended `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` iš aktyvių `Site` įrašų, bet default rekomendacija — laikyti tai per env.

Kas dar NĖRA multi-site scoped (bus daroma vėliau pilnam multi-shop):

- `checkout` (Cart/Order/PaymentIntent ir kt.)
- `promotions` (Coupon ir redemption’ai)
- `payments` (PaymentMethod/Neopay config ir kt.)
- `shipping` (ShippingMethod/Rate/DeliveryRule)
- `analytics` (events, recently-viewed)
- `notifications` (EmailTemplate)

## PVM / VAT (MVP)

MVP logika: kainos DB laikomos kaip **neto (be PVM)**, o PVM ir bruto paskaičiuojami pagal pristatymo šalį.

Admin'e:

- `Catalog -> Tax classes` (pvz. `standard`)
- `Catalog -> Tax rates` (šalis + klasė + tarifas + galiojimo datos)
- `Catalog -> Products` turi lauką `tax_class`

## API dokumentacija (Ninja / OpenAPI)

- Swagger UI: `/api/v1/docs`
- OpenAPI schema: `/api/v1/openapi.json`

Pastaba: `API_BASE_PATH` valdo prefiksą (default `api`), todėl realus kelias yra `/{API_BASE_PATH}/v1/...`.

## Catalog API (frontui)

Visi katalogo endpoint'ai priima `country_code` (pvz. `LT`) ir kainas grąžina kaip breakdown: `net`, `vat_rate`, `vat`, `gross`.

### Turinio laukai (aprašymai + SEO)

- `Product.description` ir `Category.description` laikomi kaip **Markdown**.
- Admin'e redagavimui naudojamas **WYSIWYG Markdown** (Toast UI Editor) – UI kaip rich-text, bet išsaugoma Markdown.
- SEO (tiek kategorijoms, tiek prekėms): `seo_title`, `seo_description`, `seo_keywords`.
- Kategorijos media laukai: `hero_image`/`hero_image_url` ir `menu_icon`/`menu_icon_url` (galima arba upload, arba external URL).

Jei importuojant iš XML turite HTML lauką, rekomenduojamas kelias yra jį normalizuoti į Markdown:

- helperis: `catalog.richtext.normalize_richtext_to_markdown(value, input_format="auto")` (naudoja `bleach` apvalymui + `markdownify` konversijai).

### GET `/api/v1/catalog/categories`

- Grąžina aktyvias kategorijas.

### GET `/api/v1/catalog/brands`

- Grąžina aktyvius brand'us.

### GET `/api/v1/catalog/products`

Query:

- `country_code` (privalomas) — PVZ: `LT`
- `channel` (nebūtinas, default `normal`) — `normal` arba `outlet`
- `q` (nebūtinas) — paieška pagal pavadinimą/slug
- `category_slug` (nebūtinas)
- `brand_slug` (nebūtinas)
- `page` (nebūtinas, default 1)
- `page_size` (nebūtinas, default 24)

Pavyzdys:

- `/api/v1/catalog/products?country_code=LT&page=1&page_size=24`
- `/api/v1/catalog/products?country_code=LT&channel=outlet&page=1&page_size=24`

Pastaba: `channel=outlet` grąžina tik tuos produktus, kurie turi bent vieną `OUTLET` offer su `qty_available>0`.

### GET `/api/v1/catalog/products/{slug}`

Query:

- `country_code` (privalomas)
- `channel` (nebūtinas, default `normal`) — `normal` arba `outlet`

`channel` įtakoja, kokie offer'iai (NORMAL vs OUTLET) bus parinkti ir kokia kaina bus grąžinta variantams.

Variantų kainodara frontui:

- `price` — **sale** kaina (jei yra offer su nuolaida), kitaip list
- `compare_at_price` — list kaina (tik jei yra nuolaida)
- `discount_percent` — procentas (tik jei yra nuolaida)
- `offer_id`, `offer_label`, `condition_grade`, `offer_visibility` — offer metaduomenys UI ir add-to-cart

Pavyzdys:

- `/api/v1/catalog/products/awesome-tshirt?country_code=LT`
  API:

- `GET /api/v1/pricing/quote?variant_id=123&country_code=LT&qty=2`
  - grąžina `unit_net`, `vat_rate`, `unit_vat`, `unit_gross` ir `total_*`.

## Checkout (MVP) – cart → checkout → orders

Checkout endpointai yra po `/api/v1/checkout/...`.

- **Cart** endpointai veikia ir be auth (guest cart per Django session cookie). Jei browseryje yra `access_token` cookie – krepšelis bus pririštas prie userio ir guest krepšelis gali būti sujungiamas.
- **Checkout** (`/checkout/preview`, `/checkout/confirm`) ir **orders** endpointai – **reikalauja auth** (per HttpOnly cookies).

### Cart

- `GET /api/v1/checkout/cart?country_code=LT`
- `POST /api/v1/checkout/cart/items?country_code=LT` body: `{ "variant_id": 123, "qty": 2 }`
- `POST /api/v1/checkout/cart/items?country_code=LT` body: `{ "variant_id": 123, "offer_id": 456, "qty": 1 }`
- `PATCH /api/v1/checkout/cart/items/{item_id}?country_code=LT` body: `{ "qty": 3 }` (jei `qty<=0` – item pašalinamas)
- `DELETE /api/v1/checkout/cart/items/{item_id}?country_code=LT`

`offer_id` yra nebūtinas. Jei jo nepaduodi, backend'as automatiškai parinks **best NORMAL offer** tam variantui (pagal `offer_priority` ir `qty_available`; returned A paprastai laimi).

`offer_id` reikalingas, kai norim į krepšelį dėti konkretų offer (pvz. returned A arba outlet B). Tai leidžia viename krepšelyje turėti:

- tą patį `variant_id`, bet skirtingus `offer_id` (skirtinga būklė/kaina/warehouse)

Pastabos frontui (dev):

- Jei naudojamas guest cart – visi cart request'ai turi būti su `credentials: 'include'` (kad siųstų ir priimtų session cookie).
- Naudok vienodą host'ą visur (pvz. visur `localhost`, o ne dalį requestų į `127.0.0.1`), kitaip cookie nesidalins ir krepšelis „dings“.

#### Krepšelis: 1:1 modelis ir sujungimas (guest → user)

- Prisijungęs useris turi **vieną aktyvų krepšelį** (1:1).
- Neprisijungusiam krepšelis laikomas per Django session cookie (`session_key`).
- Kai useris prisijungia tame pačiame browseryje, guest krepšelis yra **sujungiamas** į userio krepšelį:
  - jei eilutė turi `offer_id`: sujungiama pagal tą patį `offer_id`;
  - jei `offer_id` nėra: sujungiama pagal tą patį `variant_id` (kai userio eilutė irgi be offer);
  - jei varianto nėra – eilutė **pridedama**.
  - po sujungimo guest krepšelis pašalinamas.

Pastaba: po `checkout/confirm` krepšelio item'ai išvalomi (krepšelis lieka tuščias).

#### Marketing automatizacija (abandoned cart / browse) – rekomenduojamas kelias

- Jei norite automatizacijų be trečiųjų šalių priklausomybės, rekomenduojama identifikaciją laikyti „first‑party“:
  - anonimiškai sekti per `visitor_id` (cookie) ir/arba `session_key`;
  - kai vartotojas įveda email (pvz. popup) – patvirtinti per OTP ir susieti su useriu.
- Marketing consent (opt‑in) turi būti atskiras nuo prisijungimo/paskyros sukūrimo.
- Vietoje „daug istorinių krepšelių“ (kaip kai kuriose platformose) patogiau turėti atskirą snapshot/event įrašą abandoned-cart auditui (siųsta/ne, atidaryta/ne, konvertavo/ne).

### Shipping

- `GET /api/v1/checkout/shipping-methods?country_code=LT`
- `GET /api/v1/shipping/countries`

Rekomenduojama frontui:

- Šalių sąrašui naudoti `/api/v1/shipping/countries` (grąžina pilnus pavadinimus pagal kalbą).
- Tada pasirinkus šalį, užklausti `/api/v1/checkout/shipping-methods?country_code=..`.

Admin workflow:

- Šalys valdomos per admin: `Shipping -> Shipping countries` (ten pat galima suvesti vertimus per `translations`).

Endpointas grąžina ir požymius frontui:

- `carrier_code` (carrier/integracijos raktas; pvz. `dpd`, `unisend`, `omniva`, `venipak` ir t.t.)
- `requires_pickup_point` (bool) – jei `true`, frontas privalo paprašyti paštomato/pickup point pasirinkimo.

i18n pastaba (label'ams):

- Šiuo metu `ShippingMethod.name` yra DB tekstas (ne gettext), todėl frontas turėtų jį rodyti kaip yra.
- Jei reikės pilno i18n shipping metodų label'ams, rekomenduojama pridėti `ShippingMethodTranslation` arba grąžinti `name_label` per gettext pagal `carrier_code`/`code`.

Fronto validacija (rekomenduojama laikyti „privaloma“ UX lygyje):

- Checkout'e neleisti tęsti į apmokėjimą, kol nėra pasirinktas pristatymo metodas.
- Jei pasirinktas metodas turi `requires_pickup_point=true`, checkout'e privaloma:
  - parodyti paštomatų pasirinkimą,
  - neleisti tęsti, kol paštomatas nepasirinktas.

Paštomatų sąrašas nėra "core" checkout dalis – jis priklauso nuo carrier integracijos (app'o). Dabartiniai plugin'ai:

- DPD: `GET /api/v1/dpd/lockers?country_code=..&city=..&search=...&limit=...`
- Unisend: `GET /api/v1/unisend/terminals?country_code=..&city=..&search=...&limit=...`

Rekomendacija: frontas visada turi siųsti `shipping_method` (ir `pickup_point_id`, jei reikia) – backend'e gali būti palikti tik backward-compatible fallback'ai (nenaudoti kaip UX logikos).

### Primary paštomatas (user preference)

Vartotojas gali turėti vieną "pagrindinį" paštomatą (primary pickup point). Tai pagreitina checkout'ą (ypač pakartotiniams pirkimams), nes kai pasirinktas pristatymo metodas turi `requires_pickup_point=true`, backend gali automatiškai panaudoti išsaugotą `pickup_point_id`.

API:

- `GET /api/v1/auth/me` – grąžina `primary_pickup_point` (jei nustatyta).
- `PUT /api/v1/auth/pickup-point` body: `{ "shipping_method_code": "<shipping_method>", "pickup_point_id": "<pickup_point_id>" }`
  - Validuoja, kad `shipping_method_code` egzistuoja ir reikalauja pickup point.
  - Validuoja `pickup_point_id` pagal carrier integracijos DB cache (resolver pagal `ShippingMethod.carrier_code`).
  - Išsaugo kaip primary.
- `DELETE /api/v1/auth/pickup-point` – pašalina primary.

Checkout elgsena:

- Jei `checkout/preview` arba `checkout/confirm` kviečiami su `shipping_method` kuris reikalauja pickup point, bet `pickup_point_id` nepaduotas, backend bando jį paimti iš `primary_pickup_point` (tik jei sutampa `shipping_method_code`).

Svarbūs principai, kad frontui ir adminui būtų aišku:

- Užsakymas turės atskirą _pristatymo būseną_ (pvz. `label_created` → `shipped` → `delivered`) šalia mokėjimo būsenos.
- Lipdukai generuojami iš admin (Order admin action), o siuntos numeris (tracking) pririšamas prie užsakymo, kad jį matytų pirkėjas.
- Paštomato atveju: po pasirinkimo backend užpildo `Order.pickup_point_*` ir prireikus `Order.shipping_*` snapshot pagal pasirinkto pickup point adresą (resolver pagal `ShippingMethod.carrier_code`).
- Kainodara bus valdoma per admin (paprasti tarifai). Vėliau galima plėsti į taisykles pagal svorį/dimensijas.
- Tam, kad galėtume tiksliai nuspręsti ar prekės telpa į paštomatą, reikės produkto/varianto `weight` ir `dimensions` laukų.

Susiję `.env` raktai (legacy / backward-compatible fallback):

- `DEFAULT_SHIPPING_TAX_CLASS_CODE` (default `standard`)
- Kai kurie carrier'ai gali turėti laikinas fallback kainodaras per `.env` (pvz. pirmam paleidimui be DB). Rekomenduojama kainodarą visada valdyti per DB (`Shipping rates`).

### DPD (pradinis karkasas)

DPD integracija pradėta kaip atskiras „pluginas“ (app'as) `dpd/`.

Endpointai (MVP):

- `GET /api/v1/dpd/lockers?country_code=LT&city=Vilnius&search=...&limit=50`
  - Grąžina paštomatus iš lokalaus DB cache (`dpd.DpdLocker`).
  - Filtrai:
    - `country_code` (privalomas, ISO-2)
    - `city` (nebūtinas; tikslus match, `iexact`)
    - `search` (nebūtinas; ieško per `locker_id/name/city/street/postal_code`)
    - `postal_code` (nebūtinas)
    - `locker_type` (nebūtinas; filtruojama iš `raw.lockerType`)
    - `limit` (default 1000, max 1000)
  - Pastaba: jei DB cache tuščias – endpointas grąžins tuščią sąrašą; pirma paleisk sync.
- `GET /api/v1/dpd/status?tracking_number=...`
  - Proxy į DPD `/status/tracking` (kol kas grąžina `raw`).

Statusų sinchronizavimas (cron/Celery vėliau):

- `C:/Pip/django_ecommerce/.venv/Scripts/python.exe manage.py dpd_sync_statuses --limit 200`

Paštomatų (locker) sinchronizavimas (SVARBU):

- DPD paštomatų sąrašas periodiškai keičiasi (atsiranda naujų / uždaromi / koreguojami adresai), todėl **rekomenduojama daryti sync periodiškai, pvz. 1 kartą per savaitę**.
- Sync galima atlikti:
  - per komandą: `C:/Pip/django_ecommerce/.venv/Scripts/python.exe manage.py dpd_sync_lockers --country-code LT --limit 10000`
  - arba per admin: `DPD lockers` sąraše yra veiksmai **Sync LT / LV / EE / LT+LV+EE** (veikia ir be pažymėtų eilučių).

Periodikos pavyzdžiai:

- Windows Task Scheduler: kartą per savaitę paleisti komandą `...python.exe manage.py dpd_sync_lockers --country-code LT --limit 10000` (analogiškai LV/EE jei reikia).
- Linux cron (pvz. sekmadienį 03:00):
  - `0 3 * * 0 /path/to/python manage.py dpd_sync_lockers --country-code LT --limit 10000`

Susiję `.env` raktai:

- `DPD_BASE_URL` (pvz. `https://esiunta.dpd.lt/api/v1`)
- `DPD_TOKEN` (Bearer token DPD API)
- `DPD_STATUS_LANG` (pvz. `lt`)

### DPD lipdukai (A6) iš admin

Admin'e `Checkout -> Orders`:

- Order detail view: mygtukas **Generuoti DPD A6 lipduką** (sugeneruoja ir prisega PDF prie orderio).
- Order list: action **Generuoti DPD A6 lipdukus (PDF) pasirinktiems** (sugeneruoja visiems pažymėtiems ir grąžina vieną bendrą PDF atsisiuntimui).

Kad tai veiktų, reikia sukonfigūruoti siuntos kūrimui būtinus laukus.

Rekomenduojama (patogiausia) – suvesti per admin (DB):

- `DPD -> DPD config` (ten įrašai: base url, token, sender adresas, serviceAlias, payerCode)

`.env` raktai gali likti kaip fallback (pvz. pirmam paleidimui / testui), bet pagrindinė konfigūracija imama iš DB.

### Checkout preview / confirm

- `POST /api/v1/checkout/checkout/preview` body: `{ "shipping_address_id": 1, "shipping_method": "<shipping_method>" }`

Paštomatams (kai `requires_pickup_point=true`), frontas turi pridėti ir `pickup_point_id`:

- `POST /api/v1/checkout/checkout/preview` body: `{ "shipping_address_id": 1, "shipping_method": "<shipping_method>", "pickup_point_id": "<pickup_point_id>" }`

Mokesčiai (fees):

- `checkout/preview` skaičiuoja papildomus mokesčius pagal taisykles (`checkout.FeeRule`) ir grąžina:
  - `fees_total`
  - `fees[]`
- `checkout/confirm` užfiksuoja pritaikytus mokesčius DB (`checkout.OrderFee`) ir įtraukia juos į order totals.
- `fees` visada yra **+** (nuolaidos bus atskira sistema).

### Kuponai (coupon_code) ir nuolaidos (discount_total)

Checkout palaiko order-level kuponą (1 kupono kodas per checkout flow).

Pagrindinės taisyklės:

- Kuponas skaičiuojamas nuo **items** sumos (krepšelio prekių), t.y. nuo `items_total`.
- Kuponas **nemažina** `fees_total`.
- Shipping nuolaida yra atskiras flag'as: kuponas gali būti `free_shipping=true` (tuomet shipping kaina tampa 0.00, jei shipping metodas leidžiamas).
- Kupono galiojimas kanalams ir ar taikyti prekėms su nuolaida yra valdoma per settings (kaip ir aptarta).

Kupono laukai (admin'e `Promotions -> Coupons`):

- `percent_off` – procentinė nuolaida (0-100)
- `amount_off_net_eur` – fiksuota nuolaida (EUR, neto)
- `apply_on_discounted_items` – ar kuponas gali būti taikomas prekėms, kurios jau turi offer nuolaidą
- `free_shipping` – ar kuponas padaro pristatymą nemokamą
- `free_shipping_methods` – leidžiamų shipping metodų sąrašas (tuščias = visi)

Kupono limitai:

- `usage_limit_total` – bendras panaudojimų limitas visiems (global)
- `usage_limit_per_user` – panaudojimų limitas vienam vartotojui
- `times_redeemed` – panaudojimų skaitiklis

Svarbu: limitai yra skaičiuojami ir `times_redeemed` didinamas **tik tada, kai order statusas tampa `PAID`**.

Techniškai:

- per `checkout/preview` ir `checkout/confirm` mes tik validuojam limitus pagal jau apmokėtus (PAID) redemption'us;
- kai payment callback'as (arba admin action bank transfer atveju) pažymi orderį `PAID`, tada sukuriamas `CouponRedemption` ir atominiu būdu padidinamas `times_redeemed`.

API:

- `POST /api/v1/checkout/checkout/preview` priima `coupon_code` (optional)
- `POST /api/v1/checkout/checkout/confirm` priima `coupon_code` (optional)
- `checkout/preview` grąžina `discount_total`
- `GET /api/v1/checkout/orders` ir `GET /api/v1/checkout/orders/{order_id}` grąžina `discount_total`

### Order-level consent (pirkimo momentui)

Pirkimo metu fiksuojamas **order-level sutikimas** (auditas): su kokia dokumentų versija useris patvirtino.

- `GET /api/v1/checkout/consents` – grąžina aktualias versijas/URL, kurias frontas turi rodyti checkout'e.
- `POST /api/v1/checkout/checkout/confirm` – privalo turėti `consents` masyvą su bent `terms` ir `privacy`.

Confirm pavyzdys:

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "<shipping_method>", "payment_method": "klix", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

Paprastam pavedimui (be redirect):

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "<shipping_method>", "payment_method": "bank_transfer", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

Paštomatų atveju (kai reikia `pickup_point_id`):

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "<shipping_method>", "pickup_point_id": "<pickup_point_id>", "payment_method": "klix", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

Jei frontas atsiunčia pasenusias versijas (pvz. useris ilgai laikė atidarytą checkout'ą), API grąžina `409` ir frontas turi persikrauti `GET /checkout/consents`.

Susiję `.env` raktai:

- `CHECKOUT_TERMS_VERSION`, `CHECKOUT_PRIVACY_VERSION`
- `CHECKOUT_TERMS_URL`, `CHECKOUT_PRIVACY_URL`

Idempotency:

- `checkout/confirm` palaiko `Idempotency-Key` headerį (pakartojus – grąžins tą patį `order`).

Mokėjimai (MVP):

- `checkout/confirm` visada sukuria `Order` ir `PaymentIntent`.
- `payment_method=klix`:
  - `PaymentIntent.provider=klix`
  - `redirect_url` kol kas tuščias (bus prijungtas kai turėsime Klix API dokumentaciją)
- `payment_method=bank_transfer`:
  - `PaymentIntent.provider=bank_transfer`
  - `redirect_url` visada tuščias
  - `checkout/confirm` atsakyme grįžta `payment_instructions`, kurias frontas turi parodyti po užsakymo patvirtinimo

- `payment_method=neopay`:
  - `PaymentIntent.provider=neopay`
  - `redirect_url` grįžta su Neopay widget payment link (JWT HS256)
  - galutinė mokėjimo patvirtinimo būsena ateina per server-side callback

Konfigūracija (rekomenduojama per admin / DB):

- `Payments -> Payment methods`:
  - `code=bank_transfer` – čia suvedami pavedimo rekvizitai / instrukcijos (gali būti šablonas su `{order_id}`)
  - `code=klix` (ar kitas gateway) – gali būti naudojamas kaip aktyvus pasirinkimas frontui

Neopay:

- `Payments -> Neopay config`:
  - `project_id`, `project_key`
  - `client_redirect_url` (kur Neopay nukreips userį po payment)
  - `enable_bank_preselect`:
    - `false`: frontas rodo tik Neopay (banką useris pasirenka widget'e)
    - `true`: frontas gali rodyti bankų sąrašą (gaunamą iš backend) ir perduoti `neopay_bank_bic` į `checkout/confirm`

Bankų sąrašas (kai `enable_bank_preselect=true`):

- `GET /api/v1/payments/neopay/banks?country_code=LT`

Šis endpointas grąžina tik bankų sąrašą, skirtą FE bankų picker'iui (o ne visą Neopay `countries` payload). Papildomi laukai:

- `logo_url` – banko logo URL (pvz. `https://assets.neopay.lt/...svg`)
- `is_operating` – ar bankas šiuo metu veikia (jei Neopay pateikia)

Jei FE reikia pilnos `countries` informacijos (pvz. `defaultLanguage`, `languages`, `rules` tekstai), naudoti:

- `GET /api/v1/payments/neopay/countries`
- `GET /api/v1/payments/neopay/countries?country_code=LT`

Callback'ai (2 tipai):

Client redirect (browser redirect į frontą):

- Naudoja `NeopayConfig.client_redirect_url`.
- URL gali būti bet koks (pvz. `/modules/neopay/callback` arba `/order-confirmation`) – svarbu, kad sutaptų su `client_redirect_url`.
- Frontas turi persiųsti tokeną į backend'ą:
  - `POST /api/v1/payments/neopay/callback` su body `{ "token": "..." }`

Server-side callback endpoint (Neopay serveris, rekomenduojama produkcijai; reikia suvesti Neopay self-service portale):

- `POST /api/v1/payments/neopay/callback` su body `{ "token": "..." }`
- atsakymas turi būti `{ "status": "success" }` (kitu atveju Neopay kartos callback)

Pastaba: galutinis bankas užfiksuojamas iš callback (net jei preselect'inom banką, useris jį gali pakeisti widget'e). Order API grąžina `neopay_bank_bic` / `neopay_bank_name`. Gavus `success` iš callback, `Order.status` nustatomas į `paid`.

Testavimui `localhost` dažniausiai neveiks, nes Neopay serveris turi pasiekti callback URL. Rekomendacija: naudoti `ngrok`/`cloudflared` ir suvesti viešą HTTPS URL.

Local testavimas per `cloudflared`/tunnel:

- Jei `client_redirect_url` rodo į tunnel host'ą, fronto Vite dev serveris gali blokuoti host'ą.
  - Reikia įtraukti tunnel host'ą į `server.allowedHosts` (Vite config).
- Jei frontas iš tunnel origin kviečia backend API (pvz. `POST /api/v1/payments/neopay/callback`), backend'e reikia leisti CORS/CSRF:
  - `CORS_ALLOWED_ORIGINS` + `CSRF_TRUSTED_ORIGINS` turi turėti `https://<tunnel-host>`.

Sandbox vs Production (deploy checklist):

- Sandbox host'ai / Production host'ai šitam projekte yra fiksuoti kode (pakeitimai būtų daromi per deployment/config, ne per DB).
- Prieš deploy į production:
  - Pašalinti test tunnel URL iš `client_redirect_url` ir suvesti realų viešą fronto URL.
  - Patikrinti, kad Neopay bankų sąrašas (`NeopayBank`) yra atsinaujinęs (pvz. paleidus sync komandą).
  - Patikrinti, kad Neopay self-service portale server-side callback URL suvestas į realų backend (`/api/v1/payments/neopay/callback`).
  - Įsitikinti, kad `project_id`/`project_key` yra production projekto.

Fallback setting'as (jei DB dar nesukonfigūruota):

- `BANK_TRANSFER_INSTRUCTIONS` – tekstas, kurį grąžina API į `payment_instructions`.

Mokėjimo būdų sąrašas frontui:

- `GET /api/v1/checkout/payment-methods?country_code=LT`
  - Grąžina aktyvius mokėjimo būdus iš DB (`Payments -> Payment methods`).
  - Jei DB tuščia, grąžina fallback (hardcoded: `bank_transfer`, `klix`).

FE-friendly (agreguotas) mokėjimo pasirinkimų sąrašas:

- `GET /api/v1/checkout/payment-options?country_code=LT`
  - Grąžina vieną sąrašą, skirtą tiesioginiam UI renderinimui.
  - Sąrašą sudaro:
    - įprasti mokėjimo metodai (pvz. `bank_transfer`, `cod` jei sukonfigūruota)
    - Neopay bankai kaip atskiri pasirinkimai (pvz. `Swedbank`, `SEB`, ...), jei `NeopayConfig.enable_bank_preselect=true`.
      - Bankai checkout'e paduodami iš lokalaus DB (`Payments -> Neopay banks`, modelis `NeopayBank`) pagal `country_code` ir `is_enabled=true`.
      - `is_operating` laikomas informaciniu (rodymas valdomas `is_enabled`).
      - Jei DB konkrečiai šaliai dar tuščias (pvz. pirmas paleidimas), backend gali pabandyti parsisiųsti bankus iš Neopay API (bootstrap).
  - Kiekvienas įrašas turi `payload`, kurį FE gali tiesiai paduoti į `POST /checkout/preview` ir `POST /checkout/confirm`.
  - Šiuo režimu FE neturi rodyti bendro "Neopay" kaip atskiro pasirinkimo (tik bankus).
  - Paprastiems mokėjimo metodams (pvz. `bank_transfer`, `cod`, `klix`) galima admin'e įkelti logo (`Payments -> Payment methods -> image`) ir API grąžins `logo_url`.

Neopay bankų sinchronizavimas į DB (multi-country):

- Rankinis sync:
  - `python manage.py neopay_sync_banks --country-code=LT`
- Sync visoms šalims:
  - `python manage.py neopay_sync_banks`
- Rekomendacija: paleisti periodiškai (pvz. kartą per savaitę) ir prireikus rankiniu būdu (kai Neopay informuoja apie pokyčius).

### Orders

- `GET /api/v1/checkout/orders`
- `GET /api/v1/checkout/orders/{order_id}`

Frontui svarbu:

- `tracking_number` – užpildomas po lipduko sugeneravimo (admin'e)
- `carrier_code` – carrier/integracijos kodas (pvz. `dpd`, `unisend`, `omniva` ir t.t.)
- `delivery_status` – po sėkmingo lipduko sugeneravimo automatiškai nustatomas `label_created` (tiek single, tiek bulk)

Mokėjimai frontui (per `orders` endpointus):

- `payment_provider` (pvz. `bank_transfer` arba `klix`)
- `payment_status` (pvz. `pending`, `succeeded`)
- `payment_redirect_url` (kol kas dažniausiai tuščias; bus naudojamas integracijoms)
- `payment_instructions` (pildoma tik kai `payment_provider=bank_transfer`)

Mokesčiai (fees) frontui (per `orders` endpointus):

- `fees_total`
- `fees[]`

Pavyzdys: `GET /api/v1/checkout/orders/{order_id}` (sutrumpintas):

```json
{
  "id": 123,
  "status": "created",
  "status_label": "Created",
  "delivery_status": "label_created",
  "delivery_status_label": "Label created",
  "currency": "EUR",
  "country_code": "LT",
  "shipping_method": "<shipping_method>",
  "carrier_code": "<carrier_code>",
  "tracking_number": "<tracking_number>",
  "payment_provider": "bank_transfer",
  "payment_provider_label": "Bank transfer",
  "payment_status": "pending",
  "payment_status_label": "Pending",
  "items": [
    {
      "id": 1,
      "sku": "SKU-001",
      "name": "Product name",
      "qty": 1,
      "unit_price": { "currency": "EUR", "net": "10.00", "vat_rate": "0", "vat": "0.00", "gross": "10.00" },
      "line_total": { "currency": "EUR", "net": "10.00", "vat_rate": "0", "vat": "0.00", "gross": "10.00" }
    }
  ],
  "items_total": { "currency": "EUR", "net": "10.00", "vat_rate": "0", "vat": "0.00", "gross": "10.00" },
  "discount_total": { "currency": "EUR", "net": "0.00", "vat_rate": "0", "vat": "0.00", "gross": "0.00" },
  "shipping_total": { "currency": "EUR", "net": "0.00", "vat_rate": "0", "vat": "0.00", "gross": "0.00" },
  "order_total": { "currency": "EUR", "net": "10.00", "vat_rate": "0", "vat": "0.00", "gross": "10.00" },
  "created_at": "2026-01-07T12:00:00+00:00"
}
```

Admin'e (debug): `Checkout -> Carts / Orders / Payment intents`.

### Kuponai (sutrumpintai)

- Kupono stacking:
  - jei eilutė jau yra discounted (offer price < list price) arba promo-discounted (`compare_at_price` yra), kuponas taikomas tik kai `Coupon.apply_on_discounted_items=True`.
- Usage limitai:
  - limitai (`usage_limit_total`, `usage_limit_per_user`) rezervuojami order sukūrimo metu (`checkout_confirm`), sukuriant `CouponRedemption` ir padidinant `Coupon.times_redeemed`.
  - jei orderis atšaukiamas (`CANCELLED`) – rezervacija atlaisvinama.
- Admin:
  - order detalėje matosi `OrderDiscount` (coupon/promo) breakdown per inline.

## Supplier importai (Žalioji banga)

### Katalogas (products)

- Komanda: `manage.py import_zb_catalog [--dry-run] [--limit N]`
- `.env`: `ZB_PRODUCTS_FEED_URL`

### Likučiai (stocks)

- Komanda: `manage.py update_zb_stock [--dry-run] [--limit N]`
- `.env`: `ZB_STOCKS_FEED_URL`

## Toliau

- Klix (Citadelė) payment session + webhook (kai turėsime API dokumentaciją)
- Shipping rates (kainodaros taisyklės) plėtra ir shipment statusų sinchronizavimas
- Nuolaidos/kuponai ir paieška (Meilisearch)

## Recently viewed (frontui)

- Endpointas: `GET /api/v1/catalog/recently-viewed?country_code=LT&channel=normal&limit=12`
- Reikalavimas: siųsti cookies (`credentials: 'include'` / `withCredentials: true`).
- Detalės: `docs/analytics_events.md` (sekcija "Recently viewed").
