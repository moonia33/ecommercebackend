# Fast checkout (FE)

Šis dokumentas aprašo rekomenduojamą FE „greito užsakymo“ (wizard) flow, orientuotą į:

- paštomatus (pickup point)
- B2C (be sąskaitos įmonei)
- minimalų laukų pildymą

Dokumentacija yra carrier-agnostic: FE neturi hardcode'inti nei carrier pavadinimų (dpd/unisend/omniva…), nei konkrečių shipping method kodų – jis turi naudoti `GET /checkout/shipping-methods` atsakymą.

## Pagrindinis tikslas

Sumažinti žingsnius iki apmokėjimo:

- useriui rodomi tik būtini laukai (vardas, pavardė, tel.)
- paštomato atveju adresas sugeneruojamas automatiškai iš pasirinkto pickup point

## Bendri principai (no hardcode)

- FE visada naudoja `ShippingMethodOut.requires_pickup_point` (ne savo logiką), kad nuspręstų ar reikia paštomato.
- FE pickup point sąrašą ima iš `ShippingMethodOut.pickup_points_url` (vienas universalus contract).
  - Jei `pickup_points_url` tuščias, vadinasi šiam metodui pickup point'ų endpoint'as nenumatytas.
  - `pickup_points_url` jau turi `country_code` query param (FE gali papildyti filtrais, pvz. `search`, `city`, `postal_code`).

## Auth / profilis

### 1) OTP prisijungimas

- `POST /api/v1/auth/otp/request`
- `POST /api/v1/auth/otp/verify`

Po sėkmingo verify FE turėtų iškart kviesti `GET /api/v1/auth/me`.

### 2) Minimalus profilio užpildymas

Jei `me.first_name/last_name` tušti arba nėra `phones[]` su `is_primary=true`, FE turėtų parodyti trumpą formą:

- `first_name`
- `last_name`
- `phone` (E.164, pvz. `+3706...`)

Išsaugojimas:

- `PATCH /api/v1/auth/me` body: `{ "first_name": "...", "last_name": "...", "phone": "+3706..." }`

Galimos klaidos:

- `400 Invalid phone` – neteisingas formatas

## Greitas pristatymo parinkimas

### 3) Shipping metodų sąrašas

- `GET /api/v1/checkout/shipping-methods?country_code=LT`

FE rodo metodus pagal `name` ir `price`.

Papildomai:

- `ShippingMethodOut.image_url` (string): pilnas URL į shipping metodo logo/ikoną.
  - Jei `image_url` tuščias – FE turėtų rodyti fallback (pvz. carrier icon arba be paveiksliuko).
- `ShippingMethodOut.pickup_points_url` (string): pilnas URL pickup point sąrašui gauti.
  - Naudoti tik kai `requires_pickup_point=true`.
  - Šis laukas skirtas išvengti FE hardcode'inimo (nereikia žinoti konkrečių vežėjų ar endpoint'ų).

### 4) Vartotojo primary paštomatas (optional)

`GET /api/v1/auth/me` gali grąžinti `primary_pickup_point`. FE gali:

- iškart pasiūlyti „Naudoti paskutinį paštomatą“
- arba preselect'inti picker'yje

`primary_pickup_point` papildomai gali turėti:

- `shipping_method_image_url` – logo/ikonos URL, kurį FE gali rodyti šalia išsaugoto paštomato.

## Paštomato kelias (greitas)

### 5) Paštomato pasirinkimas

Jei pasirinktas shipping metodas turi `requires_pickup_point=true`, FE rodo pickup point picker.

Pickup point sąrašui gauti FE kviečia `pickup_points_url` iš pasirinkto shipping metodo (grįžta iš `GET /checkout/shipping-methods`).

Kai useris pasirinko pickup point:

### 6) Sugeneruoti adresą iš pickup point (1 request)

- `POST /api/v1/checkout/checkout/apply-pickup-point`

Body:

```json
{
  "shipping_method": "<shipping_method>",
  "pickup_point_id": "<pickup_point_id>",
  "set_as_primary_pickup_point": true
}
```

Response:

```json
{ "shipping_address_id": 123 }
```

Pastabos:

- backend sugeneruoja `UserAddress` iš pickup point adreso ir nustato jį kaip default shipping.
- billing default backend nekeičia, jei user jau turi `is_default_billing=true` adresą (pvz. įmonės sąskaitos adresą).
  - jei default billing adreso nėra, pickup point adresas gali būti nustatomas kaip default billing (pirmas pirkimas).
- backend (optional) įrašo primary pickup point į user profile

Galimos klaidos:

- `400 Missing first_name/last_name` – reikia užpildyti profilį
- `400 Missing phone` – reikia `PATCH /auth/me` su telefonu
- `400 Invalid pickup_point_id` – FE pickup point id neteisingas arba pasenęs

### 7) Checkout preview

- `POST /api/v1/checkout/checkout/preview`

Body:

```json
{
  "shipping_address_id": 123,
  "shipping_method": "<shipping_method>",
  "pickup_point_id": "<pickup_point_id>",
  "payment_method": "klix",
  "channel": "normal"
}
```

Pastaba: jei user turi `primary_pickup_point` ir `pickup_point_id` nepaduotas, backend gali jį paimti automatiškai (bet FE vis tiek rekomenduojama siųsti explicit).

### Suvestinė (visada rodoma)

FE checkout'e visada turi rodyti pilną suvestinę (nepriklausomai nuo to ar tai „fast checkout“):

- item'ai (krepšelio eilutės)
- `items_total`
- `discount_total`
- `shipping_total`
- `fees_total` ir `fees[]`
- `order_total`
- `delivery_window` (agreguotas)

Tam FE neturi naudoti atskiro "Skaičiuoti" mygtuko.

Rekomenduojamas UX:

- Kai pasikeičia bent vienas iš:
  - `shipping_method`
  - `pickup_point_id` (jei reikia)
  - `payment_method`
  - `coupon_code` (jei naudojamas)
  - `shipping_address_id`
  FE automatiškai kviečia `POST /checkout/preview` ir atnaujina suvestinę.
- Kviečiant preview UI lygyje daryti debounce (pvz. 200–400ms), kad nekviestų per dažnai.
- Kol preview negaunamas (arba grąžina klaidą), mygtukas "Mokėti" turi būti disabled.

### 8) Checkout confirm

- `POST /api/v1/checkout/checkout/confirm`

Body:

```json
{
  "shipping_address_id": 123,
  "shipping_method": "<shipping_method>",
  "pickup_point_id": "<pickup_point_id>",
  "payment_method": "klix",
  "consents": [
    {"kind":"terms","document_version":"v1"},
    {"kind":"privacy","document_version":"v1"}
  ]
}
```

## Kurjerio kelias

Jei pasirinktas `requires_pickup_point=false`, FE gali naudoti esamą checkout flow su address pasirinkimu/pildymu.

## i18n pastabos

- API kalba parenkama per `Accept-Language` / `?lang=` kaip aprašyta README.
- `ShippingMethod.name` šiuo metu yra DB tekstas (ne gettext).
- Pickup point pavadinimai (`pickup_point_name`) yra iš carrier duomenų.
