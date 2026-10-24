# Customer account area — backend

Endpoints backing the Svelte account pages (`/paskyra/*`) and public
help/stores pages. All customer endpoints are JWT-authenticated and
scoped to the owning user + current shop context.

## RMA (customer-facing) — `checkout/api/returns.py`

Mounted under `/checkout`:

| Route | Description |
|---|---|
| `GET /checkout/returns` | List own returns (last 50), shop-scoped |
| `GET /checkout/returns/{id}` | Return detail: lines, received/credited qty, tracking, credit invoice |
| `POST /checkout/orders/{order_id}/returns` | Create RMA `{lines:[{order_line_id, qty, reason}], note}` → status `pending` (ops approves) |
| `POST /checkout/returns/{id}/cancel` | Customer cancel — allowed from `pending`/`approved` only (lifecycle enforced by `set_return_status`) |

Rules (reused from `checkout/returns.py`):
- Cumulative returned qty across open returns never exceeds ordered qty.
- Non-returnable products (`Product.returnable=False`) hard-block.
- Return window: `line_return_days` (product-specific `return_days` or
  default) counted from `order.delivered_at`; customers can NOT pass
  `allow_expired` (ops-only flag).
- `OrderLineOut` now exposes `returnable`, `return_days`, `returned_qty`
  so the FE can show a "Grąžinti" CTA per line.
- Status labels: `statuses.py` `return_status` (lt/en/pl).
- Refunds are NOT automatic — ops issues a credit invoice
  (`/ops/returns/{id}/credit`) and the money movement stays manual.

## Newsletter — `notifications/api.py` (`/newsletter`)

`NewsletterSubscriber(email unique, user FK null, source, is_active,
subscribed_at, unsubscribed_at)`. One row per lowercased email; the row
is kept on unsubscribe as a suppression record.

| Route | Auth | Description |
|---|---|---|
| `POST /newsletter/subscribe` | public | Idempotent `{email, source}` — reactivates, links authed user |
| `POST /newsletter/unsubscribe` | public | Idempotent opt-out |
| `GET /newsletter/status` | JWT | Current user's subscription |
| `PUT /newsletter/preference` | JWT | `{subscribed: bool}` toggle |

Transactional email (orders, invoices) is unaffected — this is a
marketing preference only. Email-sending tasks should filter
`NewsletterSubscriber.is_active`.

## Gift cards — `giftcards/` app

Stored-value payment instrument, **not** a discount/coupon:

- `GiftCard(code unique, currency, initial_amount, balance, status
  active|depleted|voided, purchaser, owner, recipient_email,
  issued_order, expires_at)`. Codes are generated from an unambiguous
  alphabet; treat as bearer credentials (anyone holding the code can
  spend it — like cash).
- `GiftCardTransaction` — append-only ledger
  (`issue|debit|credit|adjust`, signed amount, `balance_after`, order
  link). Never update/delete ledger rows.
- `services.debit_card()` / `credit_card()` — `select_for_update`
  row-locked, always inside `transaction.atomic()`.

Customer endpoints (`/giftcards`):
- `GET /giftcards/mine` — cards owned or purchased by the user.
- `POST /giftcards/check {code}` — balance/status without claiming;
  refuses cards owned by another account.
- `POST /giftcards/redeem {code}` — claims a floating card to the
  account (owner set once; second claim → 400).

Checkout integration (`PaymentIntent.Provider.GIFT_CARD`):
- Payment method is enabled via `PaymentMethod(code="gift_card",
  provider="gift_card", is_active=True)` — create it in admin.
- `confirm` requires `gift_card_code`; inside the same DB transaction
  the card is row-locked and debited for `order.total_gross`.
  Insufficient balance / unknown / expired / voided → 400 and the whole
  order rolls back. On success the PI is `succeeded` and the order `paid`.
- MVP limitation: the card must cover the **full** total — split payment
  (card + other method) is planned, not built.
- Refund path: `credit_card()` exists (order cancel/return → credit
  back); wiring it into the cancel flow is a TODO.
- Selling gift cards through the catalog (gift-card product → issue on
  paid order → email the code) is phase 2. For now staff issues cards
  in `/admin/giftcards/giftcard/`.

## CMS hub — `cms` additions

- `CmsCategory(slug, shop, icon, sort_order, is_active)` +
  `CmsCategoryTranslation(language_code, name, description)` — help
  center grouping. `icon` = Lucide icon name for the FE grid.
- `CmsPage.category` FK + `CmsPage.sort_order` — pages slot into
  categories.
- `StoreLocation(shop, name, line1, city, postal_code, country_code,
  phone, email, lat/lng, opening_hours {mon: "9-18"}, services [],
  is_pickup_point, is_active, sort_order)` — structured store data
  (NOT a CMS page) so maps/pickup logic can consume it.

Public endpoints:
- `GET /cms/categories` → `[{slug, name, description, icon, page_count}]`
- `GET /cms/categories/{slug}/pages` → `[{slug, title, updated_at}]`
- `GET /cms/stores` → `StoreLocationOut[]`
- `GET /cms/pages/{slug}` — unchanged, used by `/pagalba/[cat]` links
  (FE renders topics on `/info/[slug]`).

## Frontend routes (Svelte)

Account shell: `/paskyra` + `AccountNav` (layout with sidebar nav).
- `/paskyra` — dashboard (profile quick links, last orders, locker card)
- `/paskyra/informacija` — profile edit + consent toggles
- `/paskyra/uzsakymai`, `/paskyra/uzsakymai/[id]` — orders + detail
- `/paskyra/adresai` — address CRUD; `primary_pickup_point` shown as a
  separate "Paštomatas" badge card (locker identity comes from the
  pickup-point record, not inferred from address text)
- `/paskyra/dovanu-korteles` — card list, check balance, redeem
- `/paskyra/naujienlaiskis` — newsletter toggle
- `/paskyra/grazinimai`, `/paskyra/grazinimai/[id]`,
  `/paskyra/grazinimai/naujas` — RMA list/detail/create
- `/pagalba`, `/pagalba/[cat]` — help center
- `/parduotuves` — store locations
- `/grazinimas` — return/exchange policy + CTA
- Footer newsletter form → real `POST /newsletter/subscribe`.

## TODO / phase 2

- Gift-card product in catalog → issue + email code on paid order.
- Split tender: gift card partial coverage + second payment method.
- Auto-credit card on order cancellation / completed return.
- First-class exchange flow (currently: return + new order, marked
  with MissingFeature on `/grazinimas`).
- Email change flow (marked MissingFeature on personal info page).
- Blog: consider Strapi/headless CMS as a separate API rather than
  overloading this backend (decision pending, not implemented).
- Email sending tasks must respect `NewsletterSubscriber.is_active`.
