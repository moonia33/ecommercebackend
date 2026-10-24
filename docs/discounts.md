# Discounts / Promotions / Coupons – contract

Šitas dokumentas aprašo "nuolaidų kontraktą" (ką skaičiuojam, kur skaičiuojam, ką grąžinam API), kad frontas ir invoice generavimas visada turėtų aiškią bazę.

## Terminai

- **List price** – bazinė kaina (be nuolaidų), šaltinis: `Variant.price_eur` (EUR, neto).
- **Offer price** – pardavimo kaina, kai eilutė pririšta prie `InventoryItem` (offer) ir taikoma:
  - `offer_price_override_eur` (neto) arba
  - `offer_discount_percent` (nuo list price).
- **Discounted item** – cart/order line, kuri turi `offer_id` ir offer kaina skiriasi nuo list price.
- **never-discount** – `InventoryItem.never_discount=True` reiškia, kad šitam offer'iui **jokios nuolaidos** netaikomos:
  - offer nuolaida ignoruojama (naudojama list price)
  - kuponas/pre-order discount šitai eilutei netaikomas

## Totals kontraktas

Pagrindinis totals principas:

- **items_subtotal** = prekių suma (pagal galutinę pardavimo kainą, t.y. offer price arba list price)
- **discount_total** = visų nuolaidų suma (coupon + promo; visada neigiama dalis)
- **shipping_total** = pristatymo suma
- **fees_total** = papildomi mokesčiai

Galutinė formulė:

- **order_total** = **(items_total − discount_total) + shipping_total + fees_total**

Svarbu:

- `discount_total` mažina **tik items dalį** (t.y. nuolaidos nenaikinam per fees).
- Jei yra `free_shipping` kuponas – jis keičia `shipping_total` į 0.00, bet tai nėra tas pats kaip items discount.

## Kuponai (Coupon)

### Kur taikoma

- Kuponas skaičiuojamas nuo eligible items sumos.
- Kuponas **nemažina** fees.

### Stacking su discounted items (pasirinktas variantas)

- Jei item yra discounted (t.y. offer price < list price), kuponas jam taikomas **tik jei**:
  - `Coupon.apply_on_discounted_items=True`

- Jei item yra promo-discounted (t.y. promo engine sumažino kainą ir UI gauna `compare_at_price`), kuponas jam taikomas **tik jei**:
  - `Coupon.apply_on_discounted_items=True`

Papildomai:

- Jei `InventoryItem.never_discount=True`, kuponas niekada netaikomas tai eilutei.

### Usage limitai

- `usage_limit_total` ir `usage_limit_per_user` rezervuojami **order sukūrimo metu** (`checkout_confirm`).
- Rezervacija sukuria `CouponRedemption` ir padidina `Coupon.times_redeemed` iškart (kad limitai veiktų ir bank transfer scenarijuose).
- Jei orderis atšaukiamas (`CANCELLED`) – rezervacija atlaisvinama ir `times_redeemed` sumažinamas.

## Promo (akcijos)

Yra du sluoksniai:

1. **Offer-level nuolaida** per `InventoryItem` (override/percent) — kaip anksčiau.
2. **`PromoRule`** — katalogo lygio automatinės taisyklės (`promotions` app).

### PromoRule semantika

- `shop` (nullable FK → `api.Shop`): `null` = taisyklė galioja **visoms**
  parduotuvėms; užpildyta — tik tai parduotuvei. `load_active_promo_rules`
  filtruoja `shop_id` arba `shop IS NULL`.
- `condition_groups`: **OR tarp grupių**, **AND grupės viduje**. Sąlygų tipai:
  `category`, `brand`, `product`, `variant`, `product_group`, `feature_value`.
- **Kategorijos sąlyga apima visas subkategorijas** — match'inama per MPTT
  (`tree_id` + `lft`/`rght`), ne tik tiesioginis `category_id`. Taisyklę
  užtenka priskirti tėvinei kategorijai.
- Legacy `scope` (`all`/`category`/`brand`/`product`/`variant`) vis dar
  palaikomas; category scope'ui taip pat galioja subtree match.
- `priority` + `id` rikiavimas — pirmoji atitinkanti taisyklė laimi
  (shop-specifinės ir globalios varžosi tame pačiame prioriteto sąraše).
- Jei offer jau "discounted" — papildoma promo netaikoma, nebent
  `allow_additional_promotions=True`.
- `channels` / `customer_groups` / `start_at`/`end_at` — kaip anksčiau.

### PVM / reverse charge

- Visais atvejais PVM = pristatymo šalies tarifas (`TaxRate` pagal
  `country_code` + `tax_class`, su galiojimo datomis).
- **Išimtis — B2B eksportas (atvirkštinis apmokestinimas)**: kai
  `UserAddress.company_vat_no` užpildytas IR šalis ≠ home šalis
  (`SHOP_HOME_COUNTRY`, default `LT`) → `is_reverse_charge()` → PVM=0
  eilutėms, siuntimui ir mokesčiams (fees).
- Sprendimas persistuojamas `Order.reverse_charge` + `Order.customer_vat_no`
  — `recalculate_totals()` ir sąskaitos lieka deterministiniai.
- Ne-LT šaliai be PVM kodo taikomas tos šalies tarifas; trūkstant
  sukonfigūruoto tarifo — aiški klaida (400), ne tylus 0.

## API kontraktas (UI + Invoice)

### Catalog

UI turi gebėti rodyti naudą (perbrauktą kainą, procentą, range), todėl katalogo endpointai turi grąžinti:

- Variant lygyje:
  - `price` (sale)
  - `compare_at_price` (list, jei yra nuolaida)
  - `discount_percent`
- Product list lygyje papildomai reikia:
  - `price_from` / `price_to` (range) arba bent jau `price_from` ("nuo")
  - (pasirinktinai) `compare_at_price_from` / `compare_at_price_to` jei norim range ir perbrauktai

### Checkout / Orders

Kad invoice ir UI būtų aiškūs:

- Order'e reikia turėti:
  - `discount_total`
  - **discount breakdown** (pvz. `discounts[]`: kind/code/name/net/vat/gross)
- Order line'e reikia turėti kainas poroje:
  - `unit_price` (sale)
  - `compare_at_unit_price` (list)
  - analogiškai ir line total porą, jei reikia

Tai leidžia:

- rodyti useriui realią naudą
- invoice generavimui aiškiai žinoti, kokia nuolaida buvo pritaikyta ir nuo ko skaičiuota
