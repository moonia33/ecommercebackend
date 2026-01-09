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

Kol kas "promo" realiai egzistuoja kaip offer-level nuolaida per `InventoryItem` (override/percent).
Ateityje, jei atsiras atskiras promo engine, jis turi laikytis šito kontrakto:

- promo turi atsispindėti kaip aiškus discount breakdown (ne tik "sale price")
- useriui visur rodoma pora: **compare_at (list)** + **price (sale)**

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
