# Djengo ecommerce backend

Django + Django Ninja e‑commerce backend API su PostgreSQL. Šiuo metu įgyvendinta: auth (email OTP + JWT), katalogas (categories/brands/products), kainodara su PVM (VAT), supplier importai (Žalioji banga), ir MVP checkout (cart → checkout → orders) su vienu pristatymo metodu.

## Statusas

- Šiuo metu aktyviai vystomas checkout/shipping (DPD + Unisend/LPExpress), lipdukų generavimas ir tracking.

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
- `GET /api/v1/auth/me` (JWT required)
- `PATCH /api/v1/auth/me` (JWT required) – atnaujina `first_name/last_name`
- `PUT /api/v1/auth/consents` (JWT required)
- `GET /api/v1/auth/addresses` (JWT required)
- `POST /api/v1/auth/addresses` (JWT required)
- `PATCH /api/v1/auth/addresses/{address_id}` (JWT required)
- `DELETE /api/v1/auth/addresses/{address_id}` (JWT required)

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

Kodas (pvz. užsakymo būsenos pranešimui vėliau): naudok [notifications/services.py](notifications/services.py) funkciją `send_templated_email(template_key=..., to_email=..., context=...)`.

## Tikslai (MVP + plėtra)

- Multi-country architektūra, su galimybe paprastai apriboti iki vienos šalies/kalbos per nustatymus.
- Klientų paskyros (email OTP) ir konfigūruojamas guest checkout.
- Paieška per Meilisearch (atskiras Docker servisas) su fallback į DB paiešką.

## Konfigūracija

- Viskas turi būti valdoma per `.env` (dev/prod profiliai).
- DB: PostgreSQL.

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

### Variantai (Options + Variants)

- Option ašys: `Catalog -> Option types` (pvz. `size`, `color`, `cast_weight`)
- Variantas: `Catalog -> Variants` (SKU, kaina, likutis)
- Varianto reikšmės: `VariantOptionValue` (Variant edit lange)

Pastaba: jei produktas yra "paprastas", seed migracija sukuria 1 default variantą iš produkto `sku/price_eur/stock_qty` (jei produktų dar nėra, nieko nekuria).

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

- **Cart** endpointai veikia ir be JWT (guest cart per Django session cookie). Jei siunti `Authorization: Bearer ...` – krepšelis bus pririštas prie userio.
- **Checkout** (`/checkout/preview`, `/checkout/confirm`) ir **orders** endpointai – **reikalauja JWT**.

### Cart

- `GET /api/v1/checkout/cart?country_code=LT`
- `POST /api/v1/checkout/cart/items?country_code=LT` body: `{ "variant_id": 123, "qty": 2 }`
- `POST /api/v1/checkout/cart/items?country_code=LT` body: `{ "variant_id": 123, "offer_id": 456, "qty": 1 }`
- `PATCH /api/v1/checkout/cart/items/{item_id}?country_code=LT` body: `{ "qty": 3 }` (jei `qty<=0` – item pašalinamas)
- `DELETE /api/v1/checkout/cart/items/{item_id}?country_code=LT`

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

Endpointas grąžina ir požymius frontui:

- `carrier_code` (pvz. `dpd`, `lpexpress`)
- `requires_pickup_point` (bool) – jei `true`, frontas privalo paprašyti paštomato/pickup point pasirinkimo.

Fronto validacija (rekomenduojama laikyti „privaloma“ UX lygyje):

- Checkout'e neleisti tęsti į apmokėjimą, kol nėra pasirinktas pristatymo metodas.
- Jei pasirinktas metodas turi `requires_pickup_point=true`, checkout'e privaloma:
  - parodyti paštomatų pasirinkimą,
  - neleisti tęsti, kol paštomatas nepasirinktas.
- Paštomatų sąrašui naudoti `GET /api/v1/dpd/lockers?country_code=..&city=..` (žr. žemiau).

Papildomai (Unisend / LPExpress):

- Paštomatų sąrašui naudoti `GET /api/v1/unisend/terminals?country_code=LT&city=Vilnius&search=...&limit=50`
- Metodai:
  - `lpexpress` (paštomatas / terminal) – `requires_pickup_point=true`
  - `lpexpress_courier` (kurjeris) – `requires_pickup_point=false`

Pastaba: šiuo metu `checkout/preview` ir `checkout/confirm` turi default `shipping_method="lpexpress"`, todėl jei frontas jo visai nesiųs – backend'as laikys, kad pasirinktas `lpexpress`. Jei norite „kietos“ validacijos backende (400, kai nepriduotas pristatymas / nepriduotas paštomatas) – reikia praplėsti checkout payload'ą pickup point ID ir įjungti server-side patikras.

Planuojama (sekantis etapas): **DPD** su 2 pagrindinėm kryptim:

- `dpd_locker` (paštomatai / pickup)
- `dpd_courier` (kurjeris)

Svarbūs principai, kad frontui ir adminui būtų aišku:

- Užsakymas turės atskirą _pristatymo būseną_ (pvz. `label_created` → `shipped` → `delivered`) šalia mokėjimo būsenos.
- Lipdukai generuojami iš admin (Order admin action), o siuntos numeris (tracking) pririšamas prie užsakymo, kad jį matytų pirkėjas.
- Paštomato atveju: po pasirinkimo backend užpildo `Order.shipping_*` snapshot pagal DPD paštomato adresą.
- Kainodara bus valdoma per admin (paprasti tarifai). Vėliau galima plėsti į taisykles pagal svorį/dimensijas.
- Tam, kad galėtume tiksliai nuspręsti ar prekės telpa į paštomatą, reikės produkto/varianto `weight` ir `dimensions` laukų.

Susiję `.env` raktai:

- `LPEXPRESS_SHIPPING_NET_EUR` (default `0.00`)
- `DEFAULT_SHIPPING_TAX_CLASS_CODE` (default `standard`)

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
    - `limit` (default 50, max 1000)
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
- `DPD_TOKEN` (Bearer)
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

- `POST /api/v1/checkout/checkout/preview` body: `{ "shipping_address_id": 1, "shipping_method": "lpexpress" }`

Paštomatams (kai `requires_pickup_point=true`), frontas turi pridėti ir `pickup_point_id`:

- `POST /api/v1/checkout/checkout/preview` body: `{ "shipping_address_id": 1, "shipping_method": "dpd_locker", "pickup_point_id": "LT90001" }`

Mokesčiai (fees):

- `checkout/preview` skaičiuoja papildomus mokesčius pagal taisykles (`checkout.FeeRule`) ir grąžina:
  - `fees_total`
  - `fees[]`
- `checkout/confirm` užfiksuoja pritaikytus mokesčius DB (`checkout.OrderFee`) ir įtraukia juos į order totals.
- `fees` visada yra **+** (nuolaidos bus atskira sistema).

### Order-level consent (pirkimo momentui)

Pirkimo metu fiksuojamas **order-level sutikimas** (auditas): su kokia dokumentų versija useris patvirtino.

- `GET /api/v1/checkout/consents` – grąžina aktualias versijas/URL, kurias frontas turi rodyti checkout'e.
- `POST /api/v1/checkout/checkout/confirm` – privalo turėti `consents` masyvą su bent `terms` ir `privacy`.

Confirm pavyzdys:

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "lpexpress", "payment_method": "klix", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

Paprastam pavedimui (be redirect):

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "lpexpress", "payment_method": "bank_transfer", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

Paštomatų atveju (pvz. DPD):

- `POST /api/v1/checkout/checkout/confirm` body:
  - `{ "shipping_address_id": 1, "shipping_method": "dpd_locker", "pickup_point_id": "LT90001", "payment_method": "klix", "consents": [{"kind":"terms","document_version":"v1"},{"kind":"privacy","document_version":"v1"}] }`

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
  - `widget_host` (default: `https://psd2.neopay.lt/widget.html?`)
  - `client_redirect_url` (kur Neopay nukreips userį po payment)
  - `banks_api_base_url` (Banks API bazinis URL)
  - `enable_bank_preselect`:
    - `false`: frontas rodo tik Neopay (banką useris pasirenka widget'e)
    - `true`: frontas gali rodyti bankų sąrašą (gaunamą iš backend) ir perduoti `neopay_bank_bic` į `checkout/confirm`

  - `force_bank_bic` / `force_bank_name` (nebūtina):
    - Jei nustatyta – backend'as visais atvejais įdės `bank` į Neopay JWT (override) ir `GET /payments/neopay/banks` grąžins tik šitą banką.
    - Naudinga sandbox'e, kai projektui leidžiamas tik vienas testinis bankas (pvz. `TESTLT123`).

Bankų sąrašas (kai `enable_bank_preselect=true`):

- `GET /api/v1/payments/neopay/banks?country_code=LT`

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

- Sandbox host'ai (pvz.):
  - `widget_host=https://psd2.sandboxnpay.online/widget.html`
  - `banks_api_base_url=https://psd2.sandboxnpay.online/api`
- Production host'ai:
  - `widget_host=https://psd2.neopay.lt/widget.html?`
  - `banks_api_base_url=https://psd2.neopay.lt/api`
- Prieš deploy į production:
  - Pašalinti test tunnel URL iš `client_redirect_url` ir suvesti realų viešą fronto URL.
  - Išjungti `force_bank_bic` (palikti tuščią), kad būtų rodomi realūs bankai.
  - Patikrinti, kad Neopay self-service portale server-side callback URL suvestas į realų backend (`/api/v1/payments/neopay/callback`).
  - Įsitikinti, kad `project_id`/`project_key` yra production projekto.

Fallback setting'as (jei DB dar nesukonfigūruota):

- `BANK_TRANSFER_INSTRUCTIONS` – tekstas, kurį grąžina API į `payment_instructions`.

Mokėjimo būdų sąrašas frontui:

- `GET /api/v1/checkout/payment-methods?country_code=LT`
  - Grąžina aktyvius mokėjimo būdus iš DB (`Payments -> Payment methods`).
  - Jei DB tuščia, grąžina fallback (hardcoded: `bank_transfer`, `klix`).

### Orders

- `GET /api/v1/checkout/orders`
- `GET /api/v1/checkout/orders/{order_id}`

Frontui svarbu:

- `tracking_number` – užpildomas po lipduko sugeneravimo (admin'e)
- `carrier_code` – pvz. `dpd` arba `lpexpress`
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
  "delivery_status": "label_created",
  "currency": "EUR",
  "country_code": "LT",
  "shipping_method": "lpexpress",
  "carrier_code": "lpexpress",
  "tracking_number": "LP123456789LT",
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
  "shipping_total": { "currency": "EUR", "net": "0.00", "vat_rate": "0", "vat": "0.00", "gross": "0.00" },
  "order_total": { "currency": "EUR", "net": "10.00", "vat_rate": "0", "vat": "0.00", "gross": "10.00" },
  "created_at": "2026-01-07T12:00:00+00:00"
}
```

Admin'e (debug): `Checkout -> Carts / Orders / Payment intents`.

## Supplier importai (Žalioji banga)

### Katalogas (products)

- Komanda: `manage.py import_zb_catalog [--dry-run] [--limit N]`
- `.env`: `ZB_PRODUCTS_FEED_URL`

### Likučiai (stocks)

- Komanda: `manage.py update_zb_stock [--dry-run] [--limit N]`
- `.env`: `ZB_STOCKS_FEED_URL`

## Toliau

- Klix (Citadelė) payment session + webhook (kai turėsime API dokumentaciją)
- LPExpress/Unisend rates (kainodaros taisyklės) plėtra ir shipment statusų sinchronizavimas
- Nuolaidos/kuponai ir paieška (Meilisearch)
