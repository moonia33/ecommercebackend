# Delivery ETA (pristatymo data / intervalas)

## Tikslas

Frontend turi galėti atvaizduoti pristatymo datą/intervalą:

- Product detail puslapyje
- Krepšelyje (cart)
- Užsakyme (order) ir order confirmation

Pvz. UI formatas:

- `2025 sausio mėn. 15–16 d.`

Backend neturėtų grąžinti lokalizuoto stringo — grąžiname normalizuotas datas (`min_date`, `max_date`) ir papildomą metainformaciją.

## Pagrindiniai principai

- ETA yra „fulfillment“ (sandėlio/tiekėjo) atsakomybė.
- Skaičiavimas turi palaikyti:
  - Skirtingus tiekėjus (dropship) su nestandartiniais pardavimo/pristatymo langais
  - Sezoniškumą (taisyklių galiojimo intervalai)
  - Darbo dienas, savaitgalius, šventines dienas
- Užsakymo metu ETA turi būti fiksuojamas (snapshot), kad vėliau nepasikeistų dėl taisyklių pakeitimų.

Pastaba: MVP realizacijoje ETA snapshot yra saugomas `Order` lygiu (agreguotas per visas eilutes, vėliausias intervalas).

## Duomenų modelis (siūlomas)

### 1) Business calendar

#### `Holiday`

Minimalus modelis šventėms:

- `date` (Date)
- `country_code` (ISO2, pvz. LT)
- `source`: string

### Cart / Checkout / Orders (frontend)

Frontend UI'ui rekomenduojama naudoti du lygius:

- **Per-item ETA**: kiekvienai eilutei `delivery_window`.
- **Agreguotas ETA** visam krepšeliui / užsakymui (ką rodyti „Pristatymas nuo-iki“ viršuje).

Endpointai:

- `GET /api/v1/checkout/cart?country_code=LT&channel=normal`:
  - `items[].delivery_window`
  - `delivery_window` (agreguotas)
- `POST /api/v1/checkout/checkout/preview`:
  - `items[].delivery_window`
  - `delivery_window` (agreguotas)
- `GET /api/v1/checkout/orders` ir `GET /api/v1/checkout/orders/{id}`:
  - `items[].delivery_window`
  - `delivery_window` (agreguotas)

Order istorijai / laiškams:

- `Order.delivery_window` yra grąžinamas iš **DB snapshot** (`Order.delivery_min_date/max_date` ir meta laukų), todėl nepasikeičia net jei vėliau admin'e pakeičiamos ETA taisyklės.

Agregavimo taisyklė (kai prekės turi skirtingus pristatymo langus):

- `min_date` = **max**(visų item `min_date`)  
  (t.y. pradžia yra vėliausia iš pradžių)
- `max_date` = **max**(visų item `max_date`)  
  (t.y. pabaiga yra vėliausia iš pabaigų)

Interpretacija:

- Jei vienas item atkeliauja vėliau, visas užsakymas negali būti pristatytas anksčiau (MVP: vienas bendras shipment).

#### (Optional) `WorkingDayOverride`

Jei reikia valdyti konkretaus sandėlio išimtis:

- `date`
- `warehouse` (FK)
- `is_working` (bool)

### 2) Warehouse kaip „fulfillment node“

Šiuo metu `Warehouse` turi `dispatch_days_min/max`.

MVP:

- interpretuoti `dispatch_days_min/max` kaip „processing“ (business days)
- pridėti:
  - `shipping_days_min/max` (business days) — transportavimas
  - `cutoff_time` (Time, tz-aware per settings TIME_ZONE) — iki kada užsakymas spėja į tą pačią dieną

### 3) Sudėtingos taisyklės (dropship windows)

Kai tiekėjai turi nestandartinius ciklus, reikalingas taisyklių sluoksnis.

#### `DeliveryRule`

Pasiūlymas:

- `code`, `name`, `is_active`
- `priority` (int)
- `valid_from`, `valid_to` (Date, optional) — sezoniškumui
- `timezone` (string, pvz. `Europe/Vilnius`)
- Targeting (bent vienas):
  - `warehouse_id` (FK, optional)
  - `brand_id` (FK, optional)
  - `category_id` (FK, optional; su descendants)
  - `product_group_id` (FK, optional)
  - `product_id` (FK, optional)
  - `channel` (normal/outlet)

Logikos laukai (variantai):

- **Variantas A (paprastas, MVP)**
  - `processing_business_days_min/max`
  - `shipping_business_days_min/max`
  - `cutoff_time` (optional)

- **Variantas B (fixed weekday delivery)**
  - `delivery_weekday` (0..6)
  - `order_window_start_weekday/time`
  - `order_window_end_weekday/time`

Svarbu: pradėti nuo MVP, o „Variantas B“ įdiegti tik kai realiai turim tokių tiekėjų.

## Skaičiavimo servisas

Vienas autoritetingas entrypoint:

- `shipping/services.py::estimate_delivery_window(...)`

Input:

- `warehouse_id`
- `country_code`
- `now` (timezone-aware datetime)
- `channel`
- `product_id`/`variant_id` (taisyklių matchinimui)

Output (normalizuotas):

```json
{
  "min_date": "2026-01-15",
  "max_date": "2026-01-16",
  "kind": "estimated",
  "source": "warehouse:KAUNAS",
  "rule_code": "default_mvp"
}
```

### Business days

Reikalingos helper funkcijos:

- `is_business_day(date, country_code, warehouse_id?)`
- `add_business_days(date, n, ...)`

Logika:

- skip weekend
- skip `Holiday` (pagal `country_code`)
- apply `WorkingDayOverride` jei yra

## Multi-warehouse / multi-offer elgsena

### Product detail

- Jei user nepasirinko varianto:
  - parinkti „best offer“ analogiškai kaip kainai (pirmas turintis stock + geriausias priority)
  - ETA skaičiuoti pagal to offer `warehouse`

### Cart

- ETA skaičiuoti per line item (variant + warehouse)
- summary lygyje rodyti:
  - arba blogiausią (max) intervalą visam krepšeliui
  - arba atskirai per item (rekomenduojama)

### Order

- Užsakymo metu išsisaugoti ETA snapshot:
  - `delivery_min_date`, `delivery_max_date`, `delivery_rule_code`, `warehouse_code`, `timezone`

## API kontraktas (siūlomas)

### Product detail

`GET /api/v1/catalog/products/{slug}`

Pridėti:

```json
{
  "delivery_window": {
    "min_date": "2026-01-15",
    "max_date": "2026-01-16",
    "kind": "estimated",
    "source": "warehouse:KAUNAS",
    "rule_code": "default_mvp"
  }
}
```

### Cart

Cart endpointuose (pvz. `GET /api/v1/checkout/cart`) grąžinti per line:

```json
{
  "lines": [
    {
      "variant_id": 80,
      "qty": 1,
      "delivery_window": {"min_date": "2026-01-15", "max_date": "2026-01-16"}
    }
  ]
}
```

### Order

Order detail/confirmation grąžina fiksuotą snapshot:

```json
{
  "delivery_window": {"min_date": "2026-01-15", "max_date": "2026-01-16", "kind": "snapshot"}
}
```

## Atviros vietos / sprendimai

- Ar ETA turi būti product-level default + variant-level (kai pasirinktas variantas)? Rekomendacija: taip.
- Holiday šaltinis: admin valdomas sąrašas arba importas (vėliau) — pradėti nuo admin.
- Timezone: naudoti projekto `TIME_ZONE` + per-rule override.

## Variantas B2: „cycle-based dropship“ (order window → inbound → pack → carrier)

Šis variantas skirtas tiekėjams, kurie dirba ciklais:

- prekė „parduodama“ (užsakymų rinkimas) X dienų / iki konkrečios datos
- tada tiekėjas vienu shipment’u atveža prekes iki mūsų sandėlio (inbound, pvz. 3 bd)
- tada mes supakuojam ir išsiunčiam (pack/handling, pvz. 1 bd)
- tada kurjeris pristato (carrier, pvz. 1–2 bd)

### Konfigūracija (DeliveryRule)

Reikalingi laukai:

- `order_window_start_weekday` (0..6) ir `order_window_start_time`
- `order_window_end_weekday` (0..6) ir `order_window_end_time`
- `timezone` (pvz. `Europe/Vilnius`)
- `supplier_inbound_business_days_min/max` (pvz. 3/3)
- `warehouse_pack_business_days_min/max` (pvz. 1/1)
- `carrier_business_days_min/max` (pvz. 1/2)

Pastaba: jei `now` nepatenka į order window, yra du režimai:

- „soft“ (MVP): užsakymas priskiriamas sekančiam ciklui
- „hard“: backend grąžina `kind="unavailable"` + artimiausio start laiko hint

### Algoritmas (aukšto lygio)

Tarkim turim `now` (timezone-aware) ir `rule.timezone`.

1) Nustatyti artimiausią `cycle_end_at` (datetime) pagal `order_window_end_*`, kuris yra `>= now`.
2) Jei `now` už window ribų ir naudojam „soft“ režimą, `cycle_end_at` turi būti sekantis (kito ciklo) end.
3) Apskaičiuoti inbound į mūsų sandėlį:

- `inbound_arrival_min_date = add_business_days(cycle_end_at.date(), supplier_inbound_min)`
- `inbound_arrival_max_date = add_business_days(cycle_end_at.date(), supplier_inbound_max)`

4) Apskaičiuoti mūsų pakavimą/išsiuntimą:

- `ship_out_min_date = add_business_days(inbound_arrival_min_date, warehouse_pack_min)`
- `ship_out_max_date = add_business_days(inbound_arrival_max_date, warehouse_pack_max)`

5) Apskaičiuoti pristatymą klientui:

- `delivery_min_date = add_business_days(ship_out_min_date, carrier_min)`
- `delivery_max_date = add_business_days(ship_out_max_date, carrier_max)`

6) Grąžinamas `delivery_window`:
 + (optional) `milestones` debug’ui.

### API output pavyzdys

```json
{
  "delivery_window": {
    "min_date": "2026-01-17",
    "max_date": "2026-01-18",
    "kind": "estimated",
    "rule_code": "supplier_cycle_a",
    "milestones": {
      "cycle_end_at": "2026-01-13T07:59:00+02:00",
      "inbound_arrival_min_date": "2026-01-16",
      "ship_out_min_date": "2026-01-17"
    }
  }
}
```

### Pavyzdys 1: „parduodam 3 dienas“

- Order window: Tue 08:00 → Fri 07:59
- Inbound: 3 bd
- Pack: 1 bd
- Carrier: 1–2 bd

Jei vartotojas užsisako ketvirtadienį, jis patenka į tą patį ciklą (cycle_end = penktadienis 07:59), todėl ETA bus trumpesnė.
Jei užsisako penktadienį 10:00 (už window ribų), „soft“ režimu jis patenka į kitą ciklą, todėl ETA bus ilgesnė.

### Pavyzdys 2: antradienis 08:00 → kitas antradienis 07:59, pristatoma penktadienį

Tokiam tiekėjui galima modeliuoti dviem būdais:

- B2 bazinis: window + inbound/pack/carrier (pristatymo diena bus „išvestinė“)
- B2+ (optional): papildomas laukas `fixed_delivery_weekday=Friday`, tada delivery skaičiuojama kaip artimiausias penktadienis po `ship_out_min_date`
