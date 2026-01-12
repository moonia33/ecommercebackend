# Analytics events (frontend + backend contract)

Šis dokumentas aprašo:

- kaip frontend’as identifikuoja naršytoją (`visitor_id` / UUID)
- kokius pagrindinius e-commerce eventus siunčiame (view, cart, checkout, purchase)
- rekomenduojamą payload (kad tiktų Facebook/Google remarketingui ir Newsman automations)
- kaip susieti newsletter subscribe su Newsman `subscriber_id`

## Terminai

- **`visitor_id`** – atsitiktinis UUID, skirtas atpažinti naršytoją (ypač prieš login). Nėra PII.
- **`user`** – prisijungęs vartotojas (pas jus pirkimas tik prisijungus).
- **`event`** – faktas apie veiksmą (pvz. produkto peržiūra). Eventai vėliau eksportuojami į:
  - Facebook Ads (Pixel/CAPI)
  - Google Ads remarketing
  - Newsman.com automations

## `visitor_id` (UUID) – kaip tai realizuojasi

### Kodėl reikia

- leidžia sekti funnel’ą prieš login (jei ateity norėsite)
- leidžia susieti page view tipo eventus, net jei dar nėra `user`
- leidžia daryti deduplikaciją (pvz. vienas view kas X min)

### Kur laikyti

Rekomenduojamas variantas – **cookie**:

- pavadinimas: `vid`
- reikšmė: UUID v4 (`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`)
- TTL: 365 dienos
- `SameSite=Lax`
- `Secure=true` (production)

Alternatyva: `localStorage` (tada backend’ui reikėtų perduoti su request header/body).

### Generavimas FE pusėje

Pseudokodas (naršyklėse, kur yra `crypto.randomUUID()`):

```ts
export function getOrCreateVisitorId(): string {
  const key = 'vid';
  const fromCookie = readCookie(key);
  if (fromCookie) return fromCookie;

  const vid = crypto.randomUUID();
  setCookie(key, vid, { days: 365, sameSite: 'Lax', secure: true });
  return vid;
}
```

Jei `crypto.randomUUID()` nėra (senesni browseriai) – naudokite UUID library.

### Kaip backend’as gauna `visitor_id`

- FE siunčia `vid` cookie automatiškai.
- Backend’as gali pasiimti iš `request.COOKIES.get('vid')`.

## Eventų siuntimas (bendras principas)

Yra 2 klasės eventų:

- **Server-side** (rekomenduojama): add_to_cart, remove_from_cart, purchase, cart state – generuojami backend’e, nes tai „tiesa“.
- **Client-side**: page view tipo eventai (pvz. category_view) – jei norite, galima siųsti iš FE.

Šiame projekte MVP rekomendacija:

- `product_view` – backend’e (product detail endpoint’e)
- `add_to_cart` / `remove_from_cart` – backend’e
- `begin_checkout` / `view_cart` – backend’e (ten kur yra endpointai)
- `purchase` – backend’e (order create/paid)

## "Recently viewed" (peržiūrėtos prekės)

Tikslas: turėti stabilų, mažą sąrašą peržiūrėtų prekių, kurį galima rodyti UI (pvz. homepage blokas ar cart drawer) **neapkraunant** `AnalyticsEvent` žurnalo.

### Kaip pildoma

- Kai vartotojas atidaro produkto detalę, backend’as registruoja `product_view` eventą.
- Tuo pačiu metu backend’as atnaujina `RecentlyViewedProduct` įrašą (upsert) ir pritaiko cap.

### Cap (limit)

- Maksimalus prekių skaičius yra ribojamas per setting’ą `RECENTLY_VIEWED_MAX`.
- Default: `12`.
- Cap taikomas atskirai:
  - prisijungusiam user’iui
  - anoniminiam visitor’iui pagal `vid` cookie

### Anon → user merge

- Kai anon vartotojas prisijungia (login/register/otp_verify), anon sąrašas pagal `vid` yra suliejamas į user’io sąrašą.
- Po merge anon įrašai išvalomi.
- Po merge vėl pritaikomas cap.

### FE API contract

Endpoint:

- `GET /api/v1/catalog/recently-viewed?country_code=LT&channel=normal&limit=12`

Query parametrai:

- `country_code` (privalomas, 2 raidės, pvz. `LT`) – reikalingas kainodarai/VAT.
- `channel` (privalomas) – `normal` arba `outlet`.
- `limit` (optional) – jei nepaduotas, naudojamas `RECENTLY_VIEWED_MAX`.

Auth / cookies:

- Endpointas veikia ir anon, ir prisijungus.
- FE turi siųsti cookies (cookie-only auth):
  - `fetch(..., { credentials: 'include' })`
  - arba axios `withCredentials: true`

Response:

- Grąžina `list[ProductListOut]` (tas pats formatas kaip `/api/v1/catalog/products`).

### Jei visgi reikia FE event endpoint’o

Suderinsime endpointą (pavyzdys):

- `POST /api/v1/analytics/events`

Body:

```json
{
  "name": "category_view",
  "occurred_at": "2026-01-11T12:00:00Z",
  "object": { "type": "category", "id": 123, "slug": "suns" },
  "context": { "country_code": "LT", "channel": "normal", "language_code": "lt" },
  "payload": { "q": null, "sort": "best_selling" }
}
```

## Pagrindiniai eventai (kanoninis sąrašas)

### 1) `product_view`

Kada: kai vartotojas atidaro produkto detalę.

Minimalus payload eksportui:

- product id/sku
- currency
- value (kaina)

Rekomenduojama:

```json
{
  "name": "product_view",
  "object": { "type": "product", "id": 1001, "slug": "nike-air" },
  "payload": {
    "currency": "EUR",
    "value": "79.99"
  }
}
```

### 2) `add_to_cart`

```json
{
  "name": "add_to_cart",
  "object": { "type": "product", "id": 1001, "slug": "nike-air" },
  "payload": {
    "currency": "EUR",
    "value": "79.99",
    "qty": 1
  }
}
```

### 3) `remove_from_cart`

```json
{
  "name": "remove_from_cart",
  "object": { "type": "product", "id": 1001, "slug": "nike-air" },
  "payload": {
    "currency": "EUR",
    "value": "79.99",
    "qty": 1
  }
}
```

### 4) `view_cart`

```json
{
  "name": "view_cart",
  "object": { "type": "cart" },
  "payload": {
    "currency": "EUR",
    "value": "159.98",
    "items": [
      { "product_id": 1001, "qty": 1, "price": "79.99" },
      { "product_id": 2002, "qty": 1, "price": "79.99" }
    ]
  }
}
```

### 5) `begin_checkout`

```json
{
  "name": "begin_checkout",
  "object": { "type": "cart" },
  "payload": {
    "currency": "EUR",
    "value": "159.98",
    "items": [
      { "product_id": 1001, "qty": 1, "price": "79.99" }
    ]
  }
}
```

### 6) `purchase`

```json
{
  "name": "purchase",
  "object": { "type": "order", "id": 555 },
  "payload": {
    "currency": "EUR",
    "value": "159.98",
    "items": [
      { "product_id": 1001, "qty": 1, "price": "79.99" }
    ]
  }
}
```

## Newsman subscribe + `subscriber_id`

Rekomendacija:

- Kai user užsiprenumeruoja newsletter:
  - backend’as sukuria/atnaujina subscriberį Newsman pusėje
  - iš Newsman atsakymo pasiima `subscriber_id`
  - `subscriber_id` saugomas DB ir naudojamas vėlesniems Newsman eventams

### FE contract (pavyzdys)

- `POST /api/v1/newsletter/subscribe`

Body:

```json
{
  "email": "user@example.com",
  "consent": true
}
```

Response:

```json
{
  "status": "ok",
  "newsman_subscriber_id": "abc123"
}
```

## Newsman Remarketing (JS) – eventų siuntimas

Šaltinis: `docs/newsman.txt` (Newsman Remarketing Developer Javascript API).

### 1) Snippetas (įkelti vieną kartą)

- Įkelkite Newsman Remarketing snippetą globaliai (pvz. per GTM arba per app layout).
- Pakeiskite `data-site-id` į jūsų ID iš Newsman „Settings → Remarketing“.

### 2) User identify po login

Kai user prisijungia (OTP flow), rekomenduojama iškviesti:

```js
_nzm.identify({ email: user.email, first_name: user.first_name, last_name: user.last_name });
```

Jei neturit vardo/pavardės:

```js
_nzm.identify({ email: user.email });
```

### 3) Ecommerce modulis

Newsman ecommerce modulis yra panašus į Google Enhanced Ecommerce, todėl pattern’as:

```js
_nzm.run('require', 'ec');
_nzm.run('set', 'currencyCode', 'EUR');
```

### 4) `product_view` → `detail`

```js
_nzm.run('require', 'ec');
_nzm.run('set', 'currencyCode', 'EUR');
_nzm.run('ec:addProduct', {
  id: String(product.id),
  name: product.name,
  category: product.category_path, // pvz. "Men/Shoes/Sneakers"
  price: String(product.price_eur)
});
_nzm.run('ec:setAction', 'detail');
_nzm.run('send', 'pageview');
```

#### 30 min dedup (client-side)

Kad `product_view` (detail) nesidubliuotų, FE turi deduplikaciją (30 min langas):

- laikykite `localStorage` raktą, pvz. `pv:<productId>` su paskutinio siuntimo timestamp
- jei nuo paskutinio siuntimo praėjo < 30 min – **nebesiųsti**

Pseudokodas:

```ts
const KEY = `pv:${product.id}`;
const last = Number(localStorage.getItem(KEY) || '0');
const now = Date.now();
if (now - last >= 30 * 60 * 1000) {
  localStorage.setItem(KEY, String(now));
  // call _nzm detail event
}
```

### 5) `add_to_cart` → `add`

```js
_nzm.run('require', 'ec');
_nzm.run('set', 'currencyCode', 'EUR');
_nzm.run('ec:addProduct', {
  id: String(product.id),
  name: product.name,
  price: String(product.price_eur),
  brand: product.brand,
  category: product.category_path,
  quantity: qty
});
_nzm.run('ec:setAction', 'add');
_nzm.run('send', 'event', 'UX', 'click', 'add to cart');
```

### 6) `remove_from_cart` → `remove`

```js
_nzm.run('require', 'ec');
_nzm.run('set', 'currencyCode', 'EUR');
_nzm.run('ec:addProduct', {
  id: String(product.id),
  quantity: qty
});
_nzm.run('ec:setAction', 'remove');
_nzm.run('send', 'event', 'UX', 'click', 'remove from cart');
```

### 7) `purchase` → `purchase`

```js
_nzm.run('require', 'ec');
_nzm.run('set', 'currencyCode', 'EUR');

for (const item of order.items) {
  _nzm.run('ec:addProduct', {
    id: String(item.product_id),
    name: item.name,
    category: item.category_path,
    price: String(item.price_eur),
    quantity: item.qty
  });
}

_nzm.run('ec:setAction', 'purchase', {
  id: String(order.id),
  affiliation: 'Shop',
  revenue: String(order.total_eur),
  tax: String(order.tax_eur || 0),
  shipping: String(order.shipping_eur || 0)
});

_nzm.run('send', 'pageview');
```

> Pastaba: purchase eventą rekomenduojama iššauti checkout success puslapyje (kai order status yra galutinis).

## Pastabos dėl privatumo / consent

- `visitor_id` nėra PII.
- Jei norite pilno GDPR, reikia atskirti `necessary` ir `marketing/analytics` consent.
- Kadangi pas jus pirkimas tik prisijungus, server-side `purchase` dažnai laikomas „transactional“ eventu; bet remarketingui (FB/Google) vis tiek gali reikėti marketing consent – priklauso nuo jūsų teisinio vertinimo.
