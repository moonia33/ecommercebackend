# Normalizerio integracijos instrukcija

Statusas: **galiojanti** (2026-10-15). Pakeičia `docs/middleware/integration_spec.md`.

Šis dokumentas aprašo pilną kontraktą tarp normalizerio (atskiras servisas, kuris
iš tiekėjų duomenų gamina normalizuotą katalogą) ir šios Django e-commerce
platformos.

## 1. Atsakomybių ribos

**Normalizeris atsako už:**
- šaltinių ingest'ą (XML/CSV/API/scrape) ir normalizavimą į žemiau aprašytą formatą
- vertimus: visi tekstai ateina jau išversti visomis kalbomis (`lt` privaloma)
- kategorijų mapinimą: faile siunčiamas **shop'e egzistuojantis** `Category.slug`
- kainas **be PVM** (`price_net`, `cost_net`)
- `code` / `product_group_code` / `ean` identifikatorių vientisumą

**Platforma atsako už:**
- staging + apply pipeline (`ImportRun`/`ImportItem` audit'as)
- per-item tranzakcijas (viena bloga eilutė nenumuša importo)
- upsert pagal `code` (SKU), konfliktų aptikimą, dry-run
- PVM, B2B kainų taisykles, publikavimą, API atvaizdavimą

## 2. Pristatymo kanalai

Vienas formatas, du kanalai:

### 2.1 Failas per CLI

```powershell
.venv\Scripts\python.exe manage.py import_catalog <failas.json> [--source <id>] [--dry-run] [--no-apply]
.venv\Scripts\python.exe manage.py import_stock <failas.json> [--warehouse <code>] [--dry-run] [--no-apply]
```

### 2.2 Ops API (rekomenduota normalizeriui)

Base: `POST /api/v1/ops/imports/...` — reikalauja JWT (staff user), `Authorization: Bearer <token>` arba auth cookie.

| Metodas | Paskirtis |
|---|---|
| `POST /ops/imports/catalog` | Katalogo importas (JSON body = visas dokumentas) |
| `POST /ops/imports/stock` | Likučių importas |
| `GET /ops/imports/runs?kind=&status=&limit=` | Run'ų sąrašas |
| `GET /ops/imports/runs/{id}` | Vieno run'o būsena + skaičiai |
| `GET /ops/imports/runs/{id}/items?status=error` | Eilučių detalės (klaidos) |
| `POST /ops/imports/runs/{id}/apply?dry_run=` | Rankinis apply stage'iui |

Query parametrai `POST` endpoint'ams:
- `dry_run=1` — pilna validacija + realus apply su rollback. **Niekas neįrašoma.** Naudoti prieš kiekvieną naują formatą/tiekėją.
- `apply_async=1` — apply Celery task'e (dideliems failams; be Redis fallback'ina į sinchroninį apply).

Atsakymas — `ImportRun`:

```json
{
  "id": 42, "kind": "catalog", "source": "normalizer",
  "status": "done_with_errors",
  "total_items": 500, "ok_items": 497, "failed_items": 3, "skipped_items": 0,
  "error_message": "", "created_at": "...", "finished_at": "..."
}
```

Klaidos visada per `GET /ops/imports/runs/{id}/items?status=error` — ten `row_no`, `external_code`, `error` tekstas.

**Idempotencija:** tas pats failas (sha256) du kartus = tas pats `ImportRun` (ne dubliuojamas apply). Pakartotinis upsert yra saugus — tie patys kodai atnaujina tuos pačius įrašus.

## 3. Katalogo dokumento formatas (v1)

```json
{
  "format_version": 1,
  "source": "normalizer",
  "mode": "upsert",
  "items": [ ... ]
}
```

| Envelope laukas | Reikšmė |
|---|---|
| `format_version` | Visada `1`. Kita versija → run klaida. |
| `source` | Tiekėjo/šaltinio identifikatorius, pvz. `"supplier-x"`. Tas pats `code` iš kito `source` su konfliktuojančiais duomenimis → item ERROR (neperrašo). |
| `mode` | `"upsert"` (default; nieko netrina) arba `"full_sync"` (deaktyvuoja prekes to paties `source`, kurių nėra faile). `full_sync` naudoti tik atsakingai — tai "šis failas = pilnas tiekėjo katalogas". |

### 3.1 Item (prekė)

```json
{
  "code": "ABC-123",
  "product_group_code": "GR-77",
  "ean": "4770000000000",
  "brand_code": "acme",
  "category": "tools-drills",
  "tax_class": "standard",
  "is_active": true,
  "price_net": "19.99",
  "cost_net": "10.00",
  "weight_g": 1200,
  "length_cm": 25, "width_cm": 10, "height_cm": 8,
  "translations": {
    "lt": {"name": "Gręžtuvas ABC", "slug": "greztuvas-abc", "description": "...", "seo_title": "", "seo_description": "", "seo_keywords": ""},
    "en": {"name": "Drill ABC", "slug": "drill-abc", "description": "..."},
    "pl": {"name": "Wiertarka ABC"}
  },
  "features": [
    {
      "code": "color",
      "value": "red",
      "name_translations": {"en": "Color", "pl": "Kolor"},
      "value_translations": {"en": "Red", "pl": "Czerwony"}
    }
  ],
  "images": [{"url": "https://cdn.example/img1.jpg", "alt": "", "sort_order": 0}],
  "stock": {"main": 10},
  "variants": [ ... ]
}
```

#### Item laukai

| Laukas | Tipas | Privalomas | Semantika |
|---|---|---|---|
| `code` | string | **taip** | Prekės SKU (`Product.sku`, unikalus). Pagrindinis matching raktas. |
| `product_group_code` | string | ne | Prekės šeimos kodas — bendras prekei ir visoms jos variantams. `Product.product_group_code`. |
| `ean` | string | ne | Prekės lygio EAN → default varianto `barcode` (unikalus; tuščias = NULL). |
| `brand_code` | string | ne | `Brand.slug` arba `Brand.import_code`. Nežinomas → auto-create (slug=pavadinimas). |
| `category` | string | ne | **Shop `Category.slug`** (mapinimas normalizeryje). Nežinomas → item ERROR. |
| `tax_class` | string | ne | `TaxClass.code`, default `"standard"`. Nežinomas → item ERROR. |
| `is_active` | bool | ne | Publikavimas. Default `true`. |
| `price_net` | decimal string | **taip** | Kaina **be PVM** → `Variant.price_eur` (default variantui arba bazinė, jei `variants` tuščias). |
| `cost_net` | decimal string | ne | Savikaina be PVM → `Variant.cost_eur`. |
| `weight_g`, `length_cm`, `width_cm`, `height_cm` | int | ne | Logistika (default variante). |
| `translations` | object | **taip** | `translations.lt` **privaloma** (`name` negali būti tuščias). `lt` → baziniai `Product` laukai; kitos kalbos → `ProductTranslation`. `slug` siunčiamas kaip hint'as — platforma užtikrina unikalumą (suffix `-2`, `-3`...). |
| `features` | array | ne | Spec. charakteristikos (filtrams). `Feature`/`FeatureValue` auto-create pagal `code`/`value`. **Failas yra source of truth**: prekės feature'ai, kurių nėra faile, ištrinami. |
| `images` | array | ne | `url` privalomas; saugoma kaip external `image_url` (renditions generuojami tik upload'ams). |
| `stock` | object | ne | `{"<warehouse_code>": <qty>}` → `qty_on_hand` default variantui. Nežinomas warehouse → item ERROR. `qty >= 0`. |
| `variants` | array | ne | Žr. žemiau. Tuščias/nėra → automatinis default variantas (`code = item.code`, paveldi `price_net`/`ean`/`stock`). |

### 3.2 Variantas

```json
{
  "code": "ABC-123-M",
  "product_group_code": "GR-77",
  "ean": "4770000000001",
  "options": {"size": "m", "color": "red"},
  "price_net": "21.99",
  "cost_net": "11.00",
  "is_active": true,
  "weight_g": 1250,
  "stock": {"main": 5, "supplier-x": 40}
}
```

| Laukas | Semantika |
|---|---|
| `code` | Varianto SKU (`Variant.sku`, globaliai unikalus). Konvencija: `<product_code>-<dydis>`, bet neprivaloma. |
| `product_group_code` | Tas pats šeimos kodas kaip prekės (pvz. `GR-77`). Jei tuščias — paveldi `item.product_group_code`. |
| `ean` | `Variant.barcode` — unikalus, kai pateiktas. Tas pats EAN kitam variantui → item ERROR. |
| `options` | `{"<option_type_code>": "<option_value_code>"}`, pvz. `{"size": "m"}`. `OptionType`/`OptionValue` auto-create. Tai maitina variantų checkbox/select UI front'e. |
| `price_net` | Jei nenurodyta — paveldi prekės `price_net`. Variantų kainų skirtumai → listing'e rodoma `price`–`price_to` (nuo–iki). |
| `stock` | Tas pats kaip prekės lygyje, bet rašoma į **šio** varianto `InventoryItem`. Tas pats variantas gali turėti likučius keliuose warehouse'uose (skirtingi tiekėjai). |

**Svarbu:** variantas visada priklauso savo prekei. Tas pats `variant.code`, atėjęs kitos prekės viduje → item ERROR (konfliktas).

## 4. Likučių dokumentas

Atskiri, dažni atnaujinimai — tik `qty_on_hand`, niekada neliečia `qty_reserved`:

```json
{
  "format_version": 1,
  "source": "normalizer",
  "warehouse_code": "main",
  "items": [
    {"code": "ABC-123-M", "qty": 5},
    {"code": "ABC-123-L", "qty": 0}
  ]
}
```

- `code` = `Variant.sku` (ne prekės kodas — default varianto sku = prekės `code`).
- `qty >= 0`, kitapus → item ERROR.
- Nežinomas SKU arba warehouse → item ERROR.

## 5. Klaidų ir konfliktų modelis

| Situacija | Elgsena |
|---|---|
| Blogas JSON / `format_version` | Run `failed`, `error_message` |
| Item neatitinka schemos | Item `error`, run tęsia |
| Nežinomas `category`/`tax_class`/`warehouse` | Item `error` su tiksliu laukų pavadinimu |
| `ean` užimtas kito varianto | Item `error` `"ean '…' already used by variant '…'"` |
| `code` priklauso kitam `source` | Item `error` — konfliktas peržiūrai, netylusis perrašymas |
| Visa kita OK | Item `ok`, `product_id`/`variant_id` užfiksuojami |

Run būsenos: `queued → applying → done` / `done_with_errors` / `failed`.
Item būsenos: `ok` / `error` / `skipped` (dry-run metu).

## 6. Praktinės taisyklės normalizeriui

1. **Visada `dry_run=1` prieš pirmą realų importą** naujam šaltiniui — pamatysi visas klaidas be write'ų.
2. `source` — stabilus, vienintelis per tiekėją (`"supplier-x"`). Tai tampa duomenų nuosavybės raktu.
3. `code` turi būti stabilus tarp run'ų — tai upsert raktas. Kodai keičiantis = naujos prekės + senos lieka (upsert režime).
4. `price_net` — string su tašku (`"19.99"`), be PVM, EUR.
5. `product_group_code`: bazinė prekė + visi jos variantai turi tą patį kodą (pvz. `xxxx`, variantai `xxxx-M`/`xxxx-L` su `product_group_code: "xxxx"`). Front'e tai naudojama vienai kortelei su min–max kaina.
6. Feature/option kodai — stabilūs slug'ai (`"size"`, `"color"`, `"m"`, `"red"`), ne laisvi tekstai. Vertimus siųsti per `name_translations`/`value_translations`.
7. `full_sync` režimą naudoti tik kai failas tikrai yra **pilnas** to `source` katalogas.
8. Dideli failai (>~10k prekių) — skaldyti į kelis dokumentus arba `apply_async=1`.

## 7. Minimalus end-to-end pavyzdys

```bash
curl -X POST "$BASE/api/v1/ops/imports/catalog?dry_run=1" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @catalog.json
# → run id, patikrinti items?status=error

curl -X POST "$BASE/api/v1/ops/imports/catalog" -H "..." -d @catalog.json
# → ok_items/failed_items

curl -X POST "$BASE/api/v1/ops/imports/stock" -H "..." -d @stock.json
```
