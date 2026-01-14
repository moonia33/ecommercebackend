# Multi-site plan

Šis dokumentas aprašo pilną perėjimo į multi-site (multi-shop) architektūrą planą šiam Django e-commerce projektui.

Tikslai:

- Dabartinis režimas: **one-site** (nekeičiam elgsenos).
- Artimiausias tikslas: saugiai įgalinti kelis domenus su `request.site` ir site-lvl assortiment (jau padaryta).
- Ilgalaikis tikslas: keli shop’ai su site-level konfigūracija, atsiskaitymais, komunikacija, ir aiškiu duomenų scoping.

## Esami sprendimai (jau įdiegta)

- `api.Site` modelis (su default įrašu) ir `api.middleware.SiteMiddleware`.
- Site-scoped turinys:
  - `cms.CmsPage` unikalumas `(site, slug)`.
  - `catalog.ContentBlock` unikalumas `(site, key)`.
- Site-level assortment:
  - `catalog.SiteCategoryVisibility` (allow-list su descendants)
  - `catalog.SiteBrandExclusion` (global exclude)
  - `catalog.SiteCategoryBrandExclusion` (exclude kategorijos scope)
- Catalog API filtruoja pagal `request.site` (kai sukonfigūruota).

## Pagrindiniai principai / invariantai

- **SKU globalus**: tas pats SKU negali konfliktuoti tarp site’ų.
- **Produktų katalogas globalus**, o matomumas valdomas per site (assortment rules).
- **Slug strategija**: slug’ai yra unikalūs per `language_code` ir dalinami tarp site’ų, kurie naudoja tą pačią kalbą.
- **Sandėliai globalūs**: inventorius rezervuojamas/nurašomas globaliai.
- **Krepšelis ir užsakymas per site**: order/carts visada turi aiškų site kontekstą.

## Users: global vs per-site (rekomendacija)

### Variantai

1) **Globalūs users (vienas account per visus site)**

- Pliusai:
  - geresnė UX (vienas loginas visur)
  - paprasčiau lojalumo/istorijos sujungimas
  - lengviau dalintis “profile” (adresai, pageidavimai)
- Minusai:
  - sudėtingiau su privatumu, jei site’ai priklauso skirtingiems brand’ams
  - sudėtingiau su atskirais “terms/privacy consent” per site
  - jei loginas per cookies, cross-subdomain/cross-domain auth tampa jautrus.

2) **Users per-site (atskiri account’ai per shop)**

- Pliusai:
  - aiškus atskyrimas (privacy, marketing consents, B2B/B2C skirtybės)
  - paprasčiau su auth cookies (vienas domenas = vienas auth scope)
- Minusai:
  - blogesnė UX (registracija kelis kartus)
  - sunkiau su bendru order history.

### Praktinė rekomendacija šiam projektui

- MVP/vidutinis horizontas: laikyti **global user** modelį (kaip dabar), bet:
  - pridėti `site_id` į *site-specific* objektus (cart/order/consents/recently-viewed)
  - konsentus (terms/privacy) daryti per site
  - nepersudėtinginti “SSO across domains” dabar: jei site’ai skirtinguose TLD, laikyti loginą per token (Authorization header) arba per “per-site” cookies.

Tokiu būdu turėsi galimybę vėliau pereiti į users-per-site, jei reikės, bet neprarasi dabartinio paprastumo.

## Perėjimo etapai

### Etapas 0 — Foundation (DONE)

- `Site` + middleware + CMS/ContentBlock scoping
- Site-level assortment + filtravimas

### Etapas 1 — Site-level config (p6)

Tikslas: perkelti į DB per-site nustatymus, kurie realiai skiriasi tarp shop’ų.

Siūlomas modelis: `api.SiteConfig` (OneToOne su `Site`), su fallback į global settings.

Minimalus MVP laukai:

- Email:
  - `default_from_email`
  - `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_use_tls`
- Checkout/legal:
  - `terms_url`, `privacy_url`, `terms_version`, `privacy_version`
- Payments:
  - `neopay_project_id`, `neopay_project_key`
  - `neopay_client_redirect_url`
  - `neopay_enable_bank_preselect`

Integracijos taškai:

- `notifications` siuntimas turi imti “from” ir SMTP per site.
- `checkout` turi atiduoti terms/privacy per site.
- `payments` service turi rinktis raktus per site.

Migrations:

- sukurti `SiteConfig` su default reikšmėmis iš env.
- backfill: visiems existing site sukurti config.

### Pastaba dėl kainodaros

Kainodara per site (skirtingi price list’ai / skirtingos bazinės kainos per shop) yra atskira, didelė tema.

- Šiame etape (p6) **kainodaros nekeičiam**.
- Jei ateityje reikės, rekomendacija: kurti atskirą `pricelist` app (price lists + validity windows + currency), kurį vėliau integruoti į catalog/pricing skaičiavimus.

### Etapas 2 — Checkout scoping (ateitis)

Modeliai:

- `checkout.Cart`:
  - pridėti `site` FK
  - pririšti visus cart line items prie cart (kas ir taip)
- `checkout.Order`:
  - pridėti `site` FK
  - užfiksuoti order-level snapshot: country_code, language_code, channel

API:

- visiems checkout endpointams naudoti `request.site`.

Migrations:

- backfill existing carts/orders į default site.

### Etapas 3 — Promotions scoping

- Coupon redemptions per site (bent per order.site)
- Jei kuponai skiriasi per shop: `promotions.Coupon.site` arba M2M per sites.

### Etapas 4 — Shipping & payments scoping

- Shipping methods/rules dažniausiai per site arba per country+site.
- Payments methods / configs per site.

### Etapas 5 — Notifications scoping

- `notifications.EmailTemplate` unikalumas išplėsti iki `(site, key, language_code)`.
- `OutboundEmail` gali turėti `site` FK auditui.

### Etapas 6 — Analytics scoping

- `analytics.AnalyticsEvent` pridėti `site`.
- `RecentlyViewedProduct` pridėti `site` (kad history būtų per shop).

### Etapas 7 — Search (Upstash) (p7)

- Vienas indeksas, bet kiekvienam product įrašui įrašyti meta:
  - `site_ids` (arba “allowed_site_ids” pagal assortment)
  - `category_ids`, `brand_id`, `is_active`
- Query metu:
  - server-side filtruoti pagal `request.site` + assortment

## Rollout strategija (saugumas)

- Default elgsena: jei site neturi jokių assortment taisyklių -> rodyti viską.
- Feature flags:
  - galima įdėti `SITE_ASSORTMENT_ENABLED` (default true), kad būtų galima išjungti.
- Backward compatibility:
  - slug’ai ir endpointai nekeičia contract’ų.

## Testavimas

- Per-site testai su `Host` header.
- Minimalūs regression testai:
  - be taisyklių katalogas identiškas kaip anksčiau
  - su taisyklėmis katalogas apribotas
  - product_detail grąžina 404 jei nevisible.
