# Django Ecommerce Platform

Ilgalaikei plėtrai paruošta e-commerce platforma su multi-shop, daugiašaliu tiekimu, B2B/B2C kainodara ir API-first integracija su Svelte 5 frontend/admin UI.

## Dokumentacijos žemėlapis

- Projekto architektūra: `docs/project_overview.md`
- Frontend (Svelte 5) API instrukcija: `docs/frontend_svelte5_api_guide.md`
- Admin UI (Svelte 5) instrukcija: `docs/svelte5_admin_ui_guide.md`
- Middleware importų kontraktas: `docs/middleware/integration_spec.md`
- Middleware job lifecycle: `docs/middleware/import_job_lifecycle.md`
- FE API v2 trumpa santrauka: `docs/api_v2_frontend_guidelines.md`

## GitHub naudojimas (komandai)

- Branch modelis:
  - `main` - stabili bazė.
  - feature šakos - konkretūs darbai (`feature/*`, `fix/*`, `docs/*`).
- Pull request standartas:
  - aiškus `Summary`, `Scope`, `Test plan`.
  - migracijos ir breaking change pažymėjimas PR aprašyme.
- Prieš PR:
  - paleisti migracijas lokaliai;
  - patikrinti `manage.py check`;
  - patikrinti API docs (`/api/v1/docs`) ar schema korektiška.

Rekomenduojamas PR šablonas:

- `## Summary`
- `## Why`
- `## DB/Migrations`
- `## API impact`
- `## Test plan`
- `## Rollback plan`

## Greitas startas (Windows / PowerShell)

1. Aplinka:

- `python -m venv .venv`
- `.venv/Scripts/activate`
- `pip install -r requirements.txt`

2. Konfigūracija:

- nukopijuoti `.env.example` į `.env`
- užpildyti bent `SECRET_KEY`, `DATABASE_URL`

3. DB:

- `.venv/Scripts/python.exe manage.py migrate`
- `.venv/Scripts/python.exe manage.py createsuperuser`

4. Paleidimas:

- `.venv/Scripts/python.exe manage.py runserver`

Patikra:

- Admin: `http://127.0.0.1:8000/admin/`
- Health: `http://127.0.0.1:8000/api/v1/health`
- API docs: `http://127.0.0.1:8000/api/v1/docs`

## Techninis branduolys

- Framework: Django 5 + Django Ninja
- DB: PostgreSQL
- Auth: HttpOnly cookie (`access_token`, `refresh_token`) + OTP flow
- Shop tenancy: domain-based (`Host` -> `request.shop`)
- Katalogas: `Product`/`Variant` + `InventoryItem` offer modelis
- Checkout: cart -> preview -> confirm -> order/payment intent
- Carrier integracijos: DPD, Unisend
- Mokėjimai: bank transfer / Klix / Neopay (pagal konfigūraciją)

## Multi-shop ir i18n bazė

- Shop kontekstas sprendžiamas pagal domeną (`ShopResolverMiddleware`).
- Kalbos pasirinkimo fallback:
  1. `?lang=...`
  2. `Accept-Language`
  3. `Shop.default_language_code`
  4. global `LANGUAGE_CODE`
- Feature flag:
  - `SHOP_CONTEXT_ENABLED=true` rekomenduojama visose ne-legacy aplinkose.
  - `SHOP_CONTEXT_STRICT=true` įjungia griežtą režimą: jei host neatitinka jokio shop domeno, API nebefallbackina į default shop.

## 3 etapų ilgalaikis planas (LTS)

### Etapas 1: kritinis multisite izolavimas

Tikslas: panaikinti cross-shop duomenų nutekėjimo riziką.

- `cms` ir `homebuilder` įvesti `shop` dimensiją ir filtravimą.
- `cart` padaryti shop-aware.
- `orders` skaitymą ir confirm idempotency filtruoti per shop.
- domain mapping griežtinimas (exact match, ne `icontains`).

Rezultatas: funkcinė multisite izoliacija pagal domeną.

### Etapas 2: bounded context refaktorizacija

Tikslas: mažinti `catalog` monolitą, didinti aiškumą.

- išskaidymas į aiškius modulius/apps:
  - `catalog_core`
  - `inventory`
  - `catalog_content`
  - `catalog_enrichment`
- API ir admin sluoksnių atskyrimas per use-case servisus.

Rezultatas: lengvesnis onbordingas, mažesnė regresijų rizika.

### Etapas 3: operacinis hardening ir scale

Tikslas: enterprise lygio stabilumas.

- observability: import/job/order/payment/shipping metrikos.
- aiškus policy režimas (`strict` vs `fallback`) mokėjimams/pristatymams.
- asinchroniniai ingest/sync job'ai (media, carrier statusai, bankai).

Rezultatas: nuspėjamas release ciklas ir mažesnė eksploatavimo kaina.

## Frontend ir admin UI nuorodos

- Svelte 5 storefront instrukcija: `docs/frontend_svelte5_api_guide.md`
- Svelte 5 admin panelės instrukcija: `docs/svelte5_admin_ui_guide.md`

## Bootstrap importai

- `python manage.py import_prestashop_categories --source "docs/prestashop_category_for_import.json" --replace-existing`
- `python manage.py import_prestashop_brands --source "docs/ps_manufacturer.json" --replace-existing`
- `python manage.py import_prestashop_products --source-dir "docs/prestashop_migration"`

## Middleware dokumentacija

- kontraktas: `docs/middleware/integration_spec.md`
- lifecycle: `docs/middleware/import_job_lifecycle.md`
