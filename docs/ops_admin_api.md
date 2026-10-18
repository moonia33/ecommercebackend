# Ops Admin API (v1)

Pilnas OPS endpointų sluoksnis kasdienėms operacijoms.

Base path: `/api/v1/ops`

## Bendros taisyklės

- Auth: HttpOnly `access_token` cookie.
- Access: tik `is_staff` arba `is_superuser`.
- Shop kontekstas:
  - jei `SHOP_CONTEXT_ENABLED=true`, visi duomenys filtruojami per `request.shop`;
  - jei `SHOP_CONTEXT_STRICT=true` ir host neatitinka shop, grąžinamas `404`.
- Klaidos:
  - `401 Unauthorized`
  - `403 Ops access requires staff permissions`
  - `404 ... not found`
  - `400` validacijos / netinkamo veiksmo klaidos

---

## 1) Orders

## GET `/api/v1/ops/orders`

Puslapiuotas užsakymų sąrašas su kritine paieška pagal:

- prekę (`product_q`),
- tiekėją/sandėlį (`supplier_code`),
- gamintoją (`manufacturer_q`),
- bei jų AND kombinacijomis.

### Query

- baziniai: `shop_code`, `status`, `payment_status`, `shipping_type`, `assignee`, `date_from`, `date_to`, `q`, `page`, `page_size`
- tiekimo paieška: `product_q`, `supplier_code`, `manufacturer_q`, `remote_only`, `eta_from`, `eta_to`

### Response

- `page`, `page_size`, `total_count`
- `results[]` su:
  - `shop` (`id`, `code`, `name`, `primary_domain`)
  - `customer` (`user_id`, `email`, `full_name`, `phone`, `company`, `is_guest`)
  - bazinė order būsena (`status`, `delivery_status`, `fulfillment_mode`, ...)
  - `amount_gross`, `amount_currency`
  - `matched_items_count`
  - `matched_suppliers`
  - `matched_lines[]` (kokios eilutės atitiko filtrą, su variantu/miniatiūra/sandėliu/kainomis)

## GET `/api/v1/ops/orders/{order_id}`

Vieno užsakymo detalė su tokia pačia OPS struktūra kaip `results[]`.

## POST `/api/v1/ops/orders/{order_id}/actions/confirm`

Request:

```json
{ "note": "..." }
```

Veiksmas:

- jei `supplier_reservation_status=not_needed` -> `pending`
- įrašo `OrderEvent(name="ops_confirmed")`

## POST `/api/v1/ops/orders/{order_id}/actions/assign`

Request:

```json
{ "assignee": "operator-1", "note": "..." }
```

Veiksmas:

- įrašo `OrderEvent(name="ops_assigned", payload={assignee,...})`

## POST `/api/v1/ops/orders/{order_id}/actions/move-to-picking`

Request:

```json
{ "note": "..." }
```

Veiksmas:

- `supplier_reservation_status=reserved`
- `supplier_reserved_at=now` (jei buvo tuščias)
- `OrderEvent(name="ops_moved_to_picking")`

## POST `/api/v1/ops/orders/actions/bulk`

Request:

```json
{
  "action": "move_to_picking",
  "selected_ids": [1452, 1453],
  "assignee": "",
  "note": "bulk update"
}
```

Veiksmas:

- sukuria `OpsJob(job_type="orders_bulk_action")`
- vykdo sinchroniškai ir grąžina job būseną

---

## 2) Shipments / labels

## GET `/api/v1/ops/shipments`

Query: `status`, `shipping_method`, `page`, `page_size`.

Grąžina order-level siuntų būsenas:

- `delivery_status`, `shipping_method`
- `carrier_code`, `carrier_shipment_id`, `tracking_number`
- `label_generated_at`

## POST `/api/v1/ops/shipments/{order_id}/label`

Generuoja vieno užsakymo lipduką:

- DPD (`dpd_locker`, `dpd_courier`) -> DPD A6 generatorius
- Unisend/LPExpress (`unisend_*`, `lpexpress*`) -> Unisend 10x15 generatorius

Po sėkmės:

- atnaujina order carrier/label/tracking laukus (per esamas integracijas)
- įrašo `OrderEvent(name="ops_label_generated")`

## POST `/api/v1/ops/shipments/labels/bulk`

Request:

```json
{ "order_ids": [1452, 1453, 1454] }
```

Veiksmas:

- sukuria `OpsJob(job_type="shipment_labels_bulk")`
- vykdo sinchroniškai (sėkmės/klaidos įrašomos `result_payload`)

## GET `/api/v1/ops/shipments/labels/{job_id}`

Grąžina bulk lipdukų job būseną.

## POST `/api/v1/ops/shipments/{shipment_id}/mark-shipped`

Veiksmas:

- nustato `order.delivery_status = picked_up` (vieninga būsena: vežėjas paėmė siuntą)
- `OrderEvent(name="ops_mark_shipped")`

## GET `/api/v1/ops/invoices?order_id=`

Sąskaitų sąrašas (filter pagal užsakymą): `number`, `kind` (`sales`/`credit`), `issue_date`, `total_gross`, `emailed_at`, `has_pdf`.

## GET `/api/v1/ops/invoices/{invoice_id}/pdf`

PDF stream'as (autentifikuota — failai privačiame bucket'e, ne CDN).

## POST `/api/v1/ops/invoices/{invoice_id}/resend`

Pakartotinai siunčia sąskaitą klientui el. paštu.

## POST `/api/v1/ops/shipments/{shipment_id}/refresh-tracking`

Veiksmas:

- įrašo `OrderEvent(name="ops_refresh_tracking")` su esamais tracking duomenimis.

---

## 3) Payments

## GET `/api/v1/ops/payments`

Query: `status`, `provider`, `order_id`, `page`, `page_size`.

Grąžina `PaymentIntent` sąrašą.

## GET `/api/v1/ops/payments/{payment_id}`

Vieno `PaymentIntent` detalė.

## POST `/api/v1/ops/payments/{payment_id}/reconcile`

Request:

```json
{ "note": "manual reconciliation note" }
```

Veiksmas:

- įrašo reconcile informaciją į `raw_response.ops_reconcile`
- `OrderEvent(name="ops_payment_reconciled")`

## POST `/api/v1/ops/payments/{payment_id}/mark-paid`

Request:

```json
{ "note": "manual paid" }
```

Veiksmas:

- `PaymentIntent.status = succeeded`
- `Order.status = paid`
- kviečia `capture_inventory_for_order` ir `redeem_coupon_for_paid_order`
- `OrderEvent(name="ops_payment_mark_paid")`

## POST `/api/v1/ops/payments/{payment_id}/refund`

Request:

```json
{ "note": "customer return" }
```

Veiksmas:

- `PaymentIntent.status = cancelled`
- `Order.status = cancelled`
- kviečia `release_coupon_for_order` ir `release_inventory_for_order`
- `OrderEvent(name="ops_payment_refund")`

---

## 4) Inventory

## GET `/api/v1/ops/inventory/alerts`

Query: `threshold`, `page`, `page_size`.

Grąžina `InventoryItem` su `qty_on_hand <= threshold`.

## GET `/api/v1/ops/inventory/items`

Query: `sku`, `product_q`, `warehouse_code`, `page`, `page_size`.

## POST `/api/v1/ops/inventory/items/{item_id}/adjust`

Request:

```json
{
  "delta": -2,
  "reason_code": "damage",
  "comment": "package damaged"
}
```

Taisyklės:

- negalima padaryti `qty_on_hand < 0`
- negalima padaryti `qty_on_hand < qty_reserved`

---

## 5) Catalog ops

## GET `/api/v1/ops/catalog/products`

Query: `q`, `is_active`, `page`, `page_size`.

Grąžina produktų sąrašą su `variants_count`, `brand_name`, `category_name`.

## POST `/api/v1/ops/catalog/products/{product_id}/toggle-active`

Perjungia `is_active`.

## POST `/api/v1/ops/catalog/variants/{variant_id}/update-price`

Request:

```json
{ "price_eur": "12.50" }
```

Atnaujina `Variant.price_eur`.

## POST `/api/v1/ops/catalog/offers/{inventory_item_id}/set-visibility`

Request:

```json
{ "visibility": "OUTLET" }
```

Leidžiamos reikšmės: `NORMAL`, `OUTLET`.

## POST `/api/v1/ops/catalog/bulk-actions`

Request:

```json
{
  "action": "deactivate_products",
  "product_ids": [501, 502]
}
```

Leidžiami action:

- `activate_products`
- `deactivate_products`

---

## 6) Audit

## GET `/api/v1/ops/audit/events`

Query: `order_id`, `kind`, `name`, `page`, `page_size`.

Duomenų šaltinis: `OrderEvent`.

---

## 7) Jobs

Duomenų šaltinis: `checkout.OpsJob`.

## GET `/api/v1/ops/jobs`

Query: `status`, `job_type`, `page`, `page_size`.

## GET `/api/v1/ops/jobs/{job_id}`

Vieno job detalė.

## POST `/api/v1/ops/jobs/{job_id}/retry`

Paleidžia job vykdymą iš naujo su tuo pačiu `request_payload`.

Palaikomi job tipai:

- `orders_bulk_action`
- `shipment_labels_bulk`

---

## OpenAPI

- Swagger UI: `/api/v1/docs`
- OpenAPI JSON: `/api/v1/openapi.json`
- Tag: `ops`

---

## Frontend integracijos pavyzdžiai (ready-to-use)

## Orders paieška pagal prekę + tiekėją + gamintoją

Request:

```http
GET /api/v1/ops/orders?status=paid&product_q=ABC-123&supplier_code=remote_wh_1&manufacturer_q=bosch&remote_only=true&page=1&page_size=20
```

Response (sutrumpintas):

```json
{
  "page": 1,
  "page_size": 20,
  "total_count": 1,
  "results": [
    {
      "id": 1452,
      "status": "paid",
      "payment_status": "succeeded",
      "delivery_status": "pending",
      "fulfillment_mode": "dropship",
      "matched_items_count": 1,
      "matched_suppliers": ["remote_wh_1"],
      "matched_lines": [
        {
          "order_line_id": 9801,
          "sku": "ABC-123",
          "name": "Bosch grąžtas 13mm",
          "qty": 2,
          "warehouse_code": "remote_wh_1",
          "brand_name": "Bosch"
        }
      ]
    }
  ]
}
```

## Bulk order action

Request:

```http
POST /api/v1/ops/orders/actions/bulk
Content-Type: application/json
```

```json
{
  "action": "move_to_picking",
  "selected_ids": [1452, 1453],
  "note": "remote shipment arrived"
}
```

Response (`OpsJob`):

```json
{
  "id": 31,
  "job_type": "orders_bulk_action",
  "status": "success",
  "request_payload": {
    "action": "move_to_picking",
    "selected_ids": [1452, 1453],
    "assignee": "",
    "note": "remote shipment arrived"
  },
  "result_payload": {
    "action": "move_to_picking",
    "updated_ids": [1452, 1453],
    "failed_ids": []
  }
}
```

## Vieno order lipduko generavimas

Request:

```http
POST /api/v1/ops/shipments/1452/label
```

Response:

```json
{
  "ok": true,
  "message": "Label generated",
  "updated_ids": [1452],
  "failed_ids": []
}
```

## Bulk lipdukų generavimas

Request:

```http
POST /api/v1/ops/shipments/labels/bulk
Content-Type: application/json
```

```json
{
  "order_ids": [1452, 1453, 1454]
}
```

Response (`OpsJob`):

```json
{
  "id": 32,
  "job_type": "shipment_labels_bulk",
  "status": "success",
  "result_payload": {
    "updated_ids": [1452, 1453],
    "failed": [
      {
        "order_id": 1454,
        "error": "Shipping method 'xyz' is not supported for label generation"
      }
    ]
  },
  "error_message": "Failed for 1 order(s)"
}
```

## Mark payment paid (manual)

Request:

```http
POST /api/v1/ops/payments/887/mark-paid
Content-Type: application/json
```

```json
{
  "note": "bank transfer matched manually"
}
```

Response:

```json
{
  "ok": true,
  "message": "Payment marked as paid",
  "updated_ids": [887],
  "failed_ids": []
}
```

## Inventory adjust

Request:

```http
POST /api/v1/ops/inventory/items/1901/adjust
Content-Type: application/json
```

```json
{
  "delta": 5,
  "reason_code": "restock",
  "comment": "supplier delivery received"
}
```

Response:

```json
{
  "ok": true,
  "message": "Inventory adjusted",
  "updated_ids": [1901],
  "failed_ids": []
}
```

## Catalog quick actions

- `POST /api/v1/ops/catalog/products/{id}/toggle-active`
- `POST /api/v1/ops/catalog/variants/{id}/update-price`
- `POST /api/v1/ops/catalog/offers/{id}/set-visibility`

`set-visibility` request:

```json
{ "visibility": "OUTLET" }
```

## Jobs polling pattern (frontend)

Po bulk operacijos:

1. paimti `job.id` iš response;
2. poll'inti `GET /api/v1/ops/jobs/{job_id}` kas 2-5 s;
3. stabdyti poll'inimą kai `status in ["success", "failed"]`;
4. rodyti `result_payload` ir `error_message`.

## Shipments registry (multi-parcel orders)

An order can have multiple `Shipment` rows (e.g. admin corrects a wrongly
picked locker, split delivery, customer return). Order-level
`tracking_number` / `carrier_*` / `delivery_status` mirror the primary
outbound shipment and stay backward-compatible.

- `GET /ops/shipments/registry?tracking=&order_id=&kind=&status=&carrier_code=&shipping_method=&page=&page_size=`
  — **shipment registry (paginated)**: `{page, page_size, total_count,
  results: ShipmentRow[]}`. This is the source of truth for the `/siuntos`
  list — one row per `Shipment` (outbound and return), not per order.
  `tracking` is a substring match. `GET /ops/shipments` stays as the legacy
  order-level projection for backwards compatibility.
- `POST /ops/orders/{id}/shipments` — register a shipment:
  `{kind, shipping_method?, carrier_code?, tracking_number?, pickup_locker_id?, notes?}`.
  Use `kind=return` to register a customer return parcel (DPD return service
  assigns the tracking automatically after label generation, or enter it manually).
- `PATCH /ops/shipments/{id}` — update carrier / method / locker /
  tracking / status / kind.
- `POST /ops/shipments/id/{id}/label` — generate the label **per shipment**
  (DPD A6 / Unisend; writes to the shipment and mirrors the primary outbound
  fields onto the order).
- `POST /ops/shipments/{order_id}/label` — legacy order-scoped label;
  resolves/creates the primary outbound shipment first.
- `GET /ops/lockers?country_code=&q=` — locker directory for admin pickers.

### Carrier / locker change with price delta

- `PATCH /ops/orders/{id}/shipping` — body `{shipping_method?, pickup_locker_id?}`.
  Recalculates shipping totals, returns `price_delta_gross`,
  `balance_due` and `new_shipment_id`. A method/locker change always
  creates a NEW outbound shipment (the previous pending/packed one is
  cancelled); on paid orders the balance delta automatically becomes a
  pending surcharge/refund row.

### Order shipping address

- `GET /ops/orders/{id}/addresses` — saved addresses of the order's
  customer (`UserAddress` rows, default shipping first).
- `PATCH /ops/orders/{id}/address` — set the order shipping address:
  `{user_address_id}` copies a saved address, or pass raw fields
  `{full_name, company, line1, city, postal_code, country_code, phone}`
  (line1/city/postal_code/country_code required). `save_to_customer`
  persists a one-off address back to the customer profile;
  `set_default_shipping` promotes it (unique default per user).

Carrier label generation resolves the receiver via
`order_shipping_snapshot()`: order fields first, then the customer's
default shipping address, then the primary `UserPhone`. Locker methods
fall back to the customer's `UserPickupPoint` when it matches the
shipping method — labels no longer fail on admin-created orders.

### Order line editing (automatic balance)

- `GET /ops/catalog/variants/search?q=&is_active=&limit=` — flat variant
  picker search (variant SKU/barcode/name, product name/SKU) returning
  shop-resolved prices and stock; used by the "add line" modal.
- `POST /ops/orders/{id}/lines` — `{variant_id, qty, offer_id?}` adds a
  line priced from the shop price list; totals recalculated.
- `PATCH /ops/orders/{id}/lines/{line_id}` — `{qty?, unit_gross?,
  unit_net?, name?}` edits the line. `qty=0` removes the line (partial
  removal = qty reduction; exchanges = remove+add). `unit_gross` is the
  admin-facing price incl. VAT — the backend picks a `unit_net` whose
  computed gross matches exactly. Both endpoints return the full order
  detail.

### Order discounts

- `POST /ops/orders/{id}/discount` — `{kind, value?, code?, name?}`:
  - `kind=percent` — `value` = percent off item totals (manual).
  - `kind=amount` — `value` = fixed discount in EUR **incl. VAT**,
    capped at the remaining item gross (manual).
  - `kind=coupon` — `code` = an existing `promotions.Coupon` code;
    validates activity window + usage limits and reserves a redemption
    (`times_redeemed` increments). One coupon per order. Coupons that
    grant free shipping also zero the order shipping.
  Manual discounts are stored as `OrderDiscount(kind=manual)` rows and
  apply to items only (same semantics as storefront coupons).
- `POST /ops/orders/{id}/discounts/{discount_id}/remove` — removes the
  row; a coupon redemption is released (`times_redeemed` decremented)
  and shipping is restored if the coupon had zeroed it.

`GET /ops/orders/{id}` returns `discounts[]` and
`totals.discounts_net|vat|gross`.

### Invoices / order documents

Sales invoices auto-issue when an order reaches `delivered`
(Celery task: snapshot -> private-storage PDF -> templated email).

- `GET /ops/invoices?order_id=` — list; `GET /ops/invoices/{id}` —
  detail with `lines[]` (incl. `credited_qty` per line).
- `POST /ops/orders/{id}/invoice` — issue the sales invoice now
  (idempotent; one active sales invoice per order).
- `POST /ops/invoices/{id}/credit` — `{lines?: [{invoice_line_id, qty}],
  discount_gross?, send_email?}`. `lines` omitted = full credit;
  `lines: []` = no item lines (discount-only credit). Partial credits
  are cumulative — the sum across issued credit notes can never exceed
  the invoiced qty of a line. Credit lines mirror the source invoice
  line amounts proportionally (exact VAT/rounding). Discount clawback:
  proportional to the credited share of product gross by default
  (percent semantics), or the explicit `discount_gross` override
  (gross, capped at the remaining discount). Numbers come from the
  KRF series.
- `POST /ops/invoices/{id}/cancel` — marks cancelled (kept for audit);
  cancelling a sales invoice frees re-issue; cancelling a credit note
  frees the credited qty.
- `POST /ops/invoices/{id}/resend` — re-email; `GET
  /ops/invoices/{id}/pdf` — private-storage PDF (lazy-generated).

Whenever `total_gross` changes on an order with a confirmed payment,
`reconcile_order_balance()` maintains a single pending auto `Surcharge`:

- positive delta -> `direction=charge` (customer owes)
- negative delta -> `direction=refund` (we owe; resolved manually)
- zero -> pending auto rows are cancelled

Unpaid orders never get surcharge rows — editing just changes the total.
The amount is never entered manually for auto rows.

### Surcharge lifecycle

- `POST /ops/orders/{id}/surcharges` — manual charge
  `{amount_gross, reason?, method: montonio|bank_transfer, send_email?}`
  (kept for edge cases; auto rows are preferred).
- `GET /ops/orders/{id}/surcharges` — list (direction/source included).
- `POST /ops/surcharges/{id}/collect` — `{method, send_email?}` picks the
  collection channel: `montonio` generates a Stargate payment link with a
  `{order}-sur-{id}` merchant reference (the webhook marks it paid
  automatically); `bank_transfer` emails the configured `PaymentMethod`
  instructions (admin-editable, no redeploy). Emails via the seeded
  `order_surcharge` template (lt/en/pl).
- `POST /ops/surcharges/{id}/mark-paid` / `cancel` / `resolve`
  (resolve = refund direction, money returned manually).

`GET /ops/orders/{id}` additionally returns `shipments[]`, `surcharges[]`,
and `totals.paid_gross`/`totals.balance_due`.

### Label regeneration

`POST /ops/shipments/id/{id}/label` on a shipment that already has
`label_generated_at` forks it: a fresh outbound shipment is created for
the new label (new tracking), the old one is cancelled and kept as
history — PrestaShop DPD module style.

### Order creation & merge

- `POST /ops/orders` — create an order for a customer email/user:
  `{user_email|user_id, shipping_method, pickup_locker_id?, shipping_*,
   shipping_net_manual?, status?, note?, lines: [{variant_id, qty}]}`.
  Prices resolve from the shop price list; totals via `recalculate_totals`.
  Stock is **reserved per line** (`InventoryAllocation`, `qty_reserved`):
  available qty is taken from warehouses in sort order and `line.offer` is
  backfilled to the first allocated item. Lines that cannot be fully
  allocated stay partially unallocated (order is still created — the goods
  are expected from a supplier).
- `POST /ops/orders/actions/merge` — `{primary_order_id, merged_order_ids}`.
  Lines/fees/discounts/shipments/surcharges/payments/events/inventory
  allocations move to the primary order; merged orders are cancelled.

### Inventory reservations & supplier reservation

- Line add / qty increase reserves additional stock; qty decrease /
  removal releases it (`qty_reserved`, or `qty_on_hand` when the
  allocation was already captured). `reserve_inventory_for_line` /
  `release_inventory_for_line` in `checkout/services.py`.
- `supplier_reservation_status` is a **manual** flag managed by the
  manager — it tracks "I ordered these items at the supplier / I'm
  waiting for the parcel". It is NOT derived from internal stock
  allocations. The ops "confirm" action bumps `not_needed -> pending`
  as a reminder; everything else is set by hand.
- `PATCH /ops/orders/{id}/supplier` — manual supplier workflow:
  `{supplier_reservation_status?, supplier_reference?, supplier_notes?}`;
  `reserved` stamps `supplier_reserved_at`.
- `supplier_code` on `GET /ops/orders` matches `line.offer.warehouse.code`
  **and** active allocations' warehouses — so "orders with unshipped
  items from supplier X" is `supplier_code=<wh_code>` + a delivery/status
  filter. `product_q` matches SKU/name/**barcode (EAN)** — a scanner can
  be used to find the order for an arriving item. `supplier_status`
  filters by the manual reservation state.
- `matched_lines[]` carries `warehouse_code/name/country/kind`,
  `allocated_qty`, `unallocated_qty` per line.

`GET /ops/orders/{id}` additionally returns `shipments[]` and `surcharges[]`.

## Warehouses & fulfillment

`Warehouse.kind = local|remote` (local = own stock, remote = imported
supplier warehouses). `Order.fulfillment_mode` is derived automatically when
lines change (post_save/post_delete on OrderLine): all local -> `stock`,
any mix -> `mixed`, all remote -> `remote` (the legacy `dropship` value is
remapped to `remote` by migration 0029).

The delivery ETA snapshot (`delivery_min_date`/`delivery_max_date` +
`delivery_eta_*`) is recomputed on the same signal via
`shipping.estimate_delivery_window` aggregated over lines (worst-case wins).
Configure `DeliveryRule` rows (LEAD_TIME or CYCLE) to feed it — no rules ->
no ETA.

### Returns (RMA)

Lifecycle: `pending -> approved -> received -> completed` (credit
issued). `rejected`/`cancelled` are terminal; `approved -> cancelled`
while nothing was received. `pending -> approved` sends the customer the
seeded `return_instructions` email (lt/en/pl, best-effort).

- `GET /ops/returns?q=&status=&order_id=&limit=` — registry; `q` matches
  RMA number, order id, line SKU and return-shipment tracking.
- `GET /ops/returns/{id}` — detail with `lines[]`
  (`qty`/`received_qty`/`credited_qty`/`reason` + product
  `returnable`/`return_days`).
- `POST /ops/orders/{id}/returns` — `{lines: [{order_line_id, qty,
  reason?}], note?, approve? (default true), allow_expired?}`.
  Validation: line belongs to the order; `Product.returnable=false` is a
  hard block; cumulative qty across non-rejected/cancelled returns can
  never exceed the ordered qty; when `order.delivered_at` is set, the
  window `delivered_at + return_days` is enforced (`return_days` =
  product override or `settings.DEFAULT_RETURN_DAYS`, default 14) unless
  `allow_expired`.
- `POST /ops/returns/{id}/status/{status}` — transition; `approved`
  triggers the instructions email.
- `POST /ops/returns/{id}/shipment` — `{carrier_code?, tracking_number?,
  notes?}` creates/links a `Shipment(kind=return)` so it shows up in the
  shipments registry and is tracked like outbound parcels.
- `POST /ops/returns/{id}/receive` — `{lines: [{return_line_id, qty}],
  restock_warehouse_id?}`. Marks goods received (idempotent; cumulative
  `received_qty` capped at requested `qty`). With `restock_warehouse_id`
  the received qty is added to that warehouse's `InventoryItem`
  (`qty_on_hand +=`, created if missing) — damaged goods simply omit the
  warehouse. Auto-transitions to `received` when all lines are fully in.
- `POST /ops/returns/{id}/credit` — `{discount_gross?, send_email?}`.
  Issues a KRF credit note for everything `received_qty > credited_qty`,
  mapped to the sales invoice's lines via `InvoiceLine.order_line`
  (requires an issued sales invoice first). The discount clawback
  follows the same rules as manual crediting: proportional by default,
  `discount_gross` overrides. Auto-transitions to `completed` when every
  line is fully credited. Refund of the money itself stays manual
  (surcharge `direction=refund` records the balance).

`matched_lines[]` on order detail/list additionally exposes
`returnable`, `return_days`, `returned_qty` (cumulative, active returns
only) and `return_deadline` (`delivered_at + return_days`). Warehouses
for restocking come from `GET /ops/warehouses`.

### Documents registry (/dokumentai)

- `GET /ops/invoices/registry?q=&kind=&status=&order_id=&date_from=&date_to=&page=&page_size=`
  — paginated `{page, page_size, total_count, results}`. `q` matches
  invoice number, order id, buyer email/name/company. `kind`:
  `sales|credit`; `status`: `issued|cancelled`. Every invoice row
  carries `buyer_name`/`buyer_email` (from the immutable buyer
  snapshot). `GET /ops/invoices?order_id=` (flat list) is unchanged.

### Thumbnails in lists

`GET /ops/catalog/products` rows now carry `thumbnail_url`
(listing rendition preferred). `GET /ops/inventory/items` and
`/ops/inventory/alerts` rows carry `product_id` + `thumbnail_url`
(product image via the variant's product).

### Bulk document export

- `POST /ops/invoices/export` — `{format: "pdf_zip"|"csv",
  invoice_ids?: [...] , q?, kind?, status?, order_id?, date_from?,
  date_to?}`. Selection is explicit `invoice_ids` or the same filter set
  as the registry. Capped at 300 documents per request.
  - `pdf_zip` → `saskaitos.zip` with one `{number}.pdf` per document
    (lazy-generates missing PDFs; skipped numbers abort with 409 only
    when *nothing* produced a file).
  - `csv` → `saskaitos.csv`, UTF-8 BOM + `;` delimiter (Excel LT
    locale). One row per document: number, kind, status, issue_date,
    order_id, credit_of, buyer name/company/VAT/email/country, currency,
    total_net/vat/gross. Document-level totals — the exact
    accounting-program import layout is TBD and will replace/extend
    this format when known.
