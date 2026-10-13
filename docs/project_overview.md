# Projekto architektūra (ilgalaikė)

## Tikslas

Šis projektas yra e-commerce backend platforma su aiškiu atskyrimu tarp:

- commerce core (katalogas, kainodara, checkout, užsakymai),
- integracijų (carrier/mokėjimai),
- import middleware (ingest/normalizavimas/upsert),
- storefront/admin klientų (Svelte 5).

## Strateginis kontekstas

- Trumpuoju laikotarpiu: stabilus multi-shop backend su shop-level politika.
- Ilguoju laikotarpiu: aiškios bounded context ribos, kad būtų paprasta pereiti į hibridinį modelį su išoriniu commerce core, jei to reikės.

## Aukšto lygio moduliai

- `api` - Ninja API entrypoint, shop kontekstas, i18n helperiai.
- `accounts` - autentifikacija, vartotojo profilis, adresai, consent.
- `catalog` - produktai, variantai, kategorijos, brandai, inventorius, kainų taisyklės, enrichment.
- `checkout` - krepšelis, checkout, order kūrimas, order mokesčiai.
- `shipping` - šalys, pristatymo metodai, shop policy.
- `payments` - mokėjimo metodai, provider konfigūracija, shop policy.
- `dpd`, `unisend` - carrier adapteriai.
- `cms`, `homebuilder` - turinio ir homepage komponavimo sluoksnis.
- `analytics` - eventų kaupimas.

## Multi-shop modelis

- `Shop` yra tenancy vienetas (domenas, kalba, valiuta, šalis).
- `ShopConfig` saugo shop-level SEO ir checkout tekstinius parametrus.
- `ShopEmailConfig` saugo atskiro shop SMTP konfigūraciją.
- `ShopResolverMiddleware` priskiria `request.shop` pagal `Host`.

Rekomenduojamos taisyklės:

- visoms read/write operacijoms, kurios susijusios su vartotojo matomu katalogu ar užsakymu, taikyti shop filtravimą;
- vengti globalaus fallback production aplinkoje, nebent aiškiai nustatytas `strict/fallback` režimas.

## i18n principai

- bazinė kalba paprastai `lt`;
- `Product/Category/Brand` turi vertimų modelius;
- kalba nustatoma: `?lang` -> `Accept-Language` -> `shop default` -> global default.

Svarbu:

- URL neturi locale segmento (`/api/v1/lt/...` nenaudojama);
- locale yra request lygmens signalas per header/query.

## 3 etapų įgyvendinimas

## 1 etapas - kritiniai saugos/izoliacijos darbai

- shop-aware `cms`/`homebuilder`;
- shop-aware `cart` ir `orders` skaitymas;
- griežtas domain resolving.

Priėmimo kriterijai:

- tas pats user su skirtingu domenu nemato kito shop duomenų;
- homepage turinys nepraslysta tarp shop.

## 2 etapas - `catalog` išskaidymas į bounded contexts

Rekomenduojama išskaidyti į:

- `catalog_core`: produktas/variantas/brandas/kategorija;
- `inventory`: sandėliai, likučiai, rezervacijos, offer atranka;
- `catalog_pricing`: shop price list/rules, B2B/B2C logika;
- `catalog_enrichment`: enrichment taisyklės ir run audit.

Priėmimo kriterijai:

- API išlieka stabili;
- verslo logika iškelta iš endpoint į servisus;
- mažiau kryžminių importų tarp app.

## 3 etapas - skalė ir operacinis patikimumas

- async job infrastruktūra importams/sync;
- observability ir SLO (API, import, payment callback, label generation);
- aiškus incidentų procesas (`dead letter`, retry, alerting).

Priėmimo kriterijai:

- galima atsekti kiekvieną import batch ir order state change;
- kritinių job klaidos turi aiškų retry ir ownership.

## Rekomenduojami engineering standartai

- API contract-first: OpenAPI turi būti laikoma „source of truth“ frontendui.
- Migrations discipline: kiekvienas schema pakeitimas su aiškiu rollback planu.
- Test pyramid:
  - unit: domain servisuose,
  - integration: API + DB critical flows,
  - smoke: auth/catalog/checkout critical endpoints.

## Rizikos ir kaip jas sumažinti

- Rizika: `catalog` tampa per platus ir trapus.
  - Sprendimas: etapinis išskaidymas per service layer prieš fizinį app split.
- Rizika: cross-shop duomenų nutekėjimas.
  - Sprendimas: privalomas shop context guard visose read/write vietose.
- Rizika: frontend ir backend kontraktų drift.
  - Sprendimas: OpenAPI validation CI ir bendras contract changelog.
