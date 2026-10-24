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
- Digital lines (`Product.is_gift_card=True`) are always non-returnable
  (`returnable=False`, `return_days=0`) — an issued code cannot be
  "shipped back". Applies to all current and future digital products
  flagged `is_gift_card`.
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
  link, `reverses` self-FK for reversal pairs). Never update/delete
  ledger rows.
- `services.debit_card()` / `credit_card()` — `select_for_update`
  row-locked, always inside `transaction.atomic()`.

Customer endpoints (`/giftcards`):
- `GET /giftcards/mine` — cards owned or purchased by the user.
- `POST /giftcards/check {code}` — balance/status without claiming;
  refuses cards owned by another account.
- `POST /giftcards/redeem {code}` — claims a floating card to the
  account (owner set once; second claim → 400).

### Paying WITH a gift card (split tender)

`CheckoutConfirmIn.gift_card_code` — the card is **applied** at
confirm, inside the same DB transaction as order creation:

- Applied amount = `min(card.balance, order.total_gross)`, recorded on
  `PaymentIntent.gift_card_amount`; `amount_gross` holds the remainder
  the external provider must collect. `OrderOut` exposes both as
  `gift_card_amount` / `provider_amount` (strings).
- **Balance > total** → only the order total is debited; the rest stays
  on the card (status stays `active` unless the balance hits 0).
- **Full cover** → `PaymentIntent.provider = gift_card`, status
  `succeeded`, order `paid` immediately, `finalize_paid_order` runs.
- **Partial cover** → `provider` stays the chosen method; Montonio is
  called with `amount_override = remainder`. Gift card is never offered
  as a payment-method radio (filtered in `payment_options`) — the FE
  shows an always-visible "apply code" box instead.
- **Inbank** + gift card is rejected (400) — installment financing
  cannot take an amount override.
- Buying a gift card **with** a gift card is rejected (400) — prevents
  laundering another card's balance into a fresh card.
- Using a card claims it to the authenticated user (floating card →
  owner); cards owned by another account are refused.

### Restoration (never lose balance)

`restore_card_debits_for_order(order)` credits every DEBIT for the
order at most once (`reverses` link makes it idempotent). Called from:

- Montonio/Inbank order-creation failure (502 path).
- Payment-failure/cancel webhooks (failed intent, cancelled order).
- `expire_pending_payment_reservations()` — pending orders that never
  got paid (row lock uses `select_for_update(of=("self",))` — Postgres
  forbids locking the nullable `payment_intent` join side).
- Ops cancel/refund endpoints.

### Selling gift cards (digital product)

- `Product.is_gift_card` — the catalog product. One product, variants
  = denominations (e.g. 20/50/100 €). Issued face value = the **gross
  unit price actually paid**, so promo/discounted sales stay correct.
- Cart is "digital" when every line is a gift card → preview skips
  shipping (`shipping_total=0`, `is_digital=True`), confirm accepts
  `shipping_address_id=None` (billing falls back to the user's default
  address for the invoice) and stores `shipping_method="digital"`.
- Mixed carts keep the normal physical flow; inventory reservation
  skips gift-card lines (nothing physical to reserve).
- `CheckoutConfirmIn.gift_card_recipient_email` — optional; only valid
  when the cart contains gift-card items, validated as an email, stored
  on `Order.gift_recipient_email`.
- `finalize_paid_order(order_id)` — shared by direct gift-card,
  Montonio webhook, Inbank callback and ops mark-paid. It captures
  reserved inventory, redeems coupon usage, calls
  `issue_cards_for_paid_order` and marks digital-only orders
  `delivery_status=delivered` (digital fulfillment).
- Issuance is idempotent (`order.issued_gift_cards.exists()` guard) —
  repeated webhooks are safe. One `GiftCard` per unit of qty; email via
  `gift_card_issued` EmailTemplate to the recipient (or purchaser).
  Manual staff issuance in `/admin/giftcards/giftcard/` still works.
- `order_paid` confirmation email: `finalize_paid_order` also sends the
  purchaser a "mokėjimas gautas" email (`order_paid` EmailTemplate,
  lt/en/pl) exactly once — `Order.paid_confirmation_sent_at` is claimed
  atomically before sending, so webhook + admin double-finalize can't
  duplicate it. Digital orders get the gift-card wording, physical a
  "ruošiame siuntą" wording via `{% if is_digital %}`.

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

## Storefront navigation — `cms` menus

Named menus drive the storefront header (Shopify-style):

- `Menu(code, name, shop?, is_active)` — `code="header"` = bottom nav
  strip, `code="promo"` = promo ribbon above the header.
- `MenuItem` (MPTT tree, `DraggableMPTTAdmin`) with polymorphic target:
  `kind` ∈ `link | category | cms_page | brand | preset`, plus `icon`
  (Lucide name), `badge`, `highlight`, `open_new_tab`, `show_in`
  (`all|desktop|mobile`). `MenuItemTranslation(title, badge)`.
- `preset` targets resolved FE-side (`discounted → /katalogas?sort=
  discounted`, `new`, `bestsellers`, `all_categories`, `gift_cards`).
- `MegaMenuFeature(category?, sort_order, image, link_url)` +
  translations — promo card rendered inside the catalog mega-menu;
  `category=None` ("default") is the fallback card.
- Shop scoping: a shop-specific `Menu(code, shop)` wins; the global
  (`shop=None`) menu is the fallback for every storefront.
  `MegaMenuFeature`s merge — shop-specific cards override global ones
  keyed by `category_id`/"default".

`GET /cms/navigation?language_code=lt` →
`{promo: NavItemOut|null, items: NavItemOut[](tree), mega_features:
{key: MegaMenuFeatureOut}}` — one request, resolved labels+urls,
in-memory tree build, `LocMemCache` 1h keyed by
`shop/language/version`; `post_save/post_delete` on
Menu/MenuItem/MenuItemTranslation/MegaMenuFeature bumps a version key
(`cms.signals`) so edits propagate without TTL waits.

Admin drag ordering: `/catalog/categories` orders by `tree_id, lft`
(`DraggableMPTTAdmin` drag in `CategoryAdmin` reorders the mega-menu);
`order_insertion_by=["name"]` still applies to NEW roots.

Seed: `cms/migrations/0010_seed_header_menu.py` creates a global
`header` menu (Nuolaidos/Naujienos/Populiariausios/Visos kategorijos/
Visos prekės + "Puslapiai" parent with 4 cms_page children) and a
`promo` ribbon item.

FE: `+layout.server.ts` fetches `getNavigation` alongside categories
(→ `null` on error); `Header.svelte` renders the promo ribbon, the
bottom nav strip (dropdowns for children, badge/highlight/icon), and
`CatalogMenu` shows `mega_features[activeCategory]` / `default`.

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

Checkout (`/checkout`) gift-card UX:
- Always-visible code field in the payment step → `POST
  /giftcards/check`, then an applied chip with `−X €`; remainder goes
  through the normal payment-method radios (Inbank options hidden
  while a card is applied).
- `balance > total` → hint that the leftover stays on the card;
  `remaining = 0` → radios replaced by "visa suma padengta".
- Digital-only carts (all items `is_gift_card`) → the delivery step
  becomes "Sąskaitos duomenys" (billing address auto-picked) plus an
  optional recipient-email input; no shipping method/locker UI.
- `/uzsakymas` shows the tender split (`gift_card_amount` /
  `provider_amount`) and renders `shipping_method="digital"` as
  "El. paštas (skaitmeninis)".

## TODO / phase 2

- Inbank + gift card split tender (needs a proper amount override in
  `create_pos_session` before it can be enabled).
- Refund allocation on partial-tender refunds: currently the card part
  is restored by `restore_card_debits_for_order`; per-line refund
  splitting between card and provider is manual.
- Sender name / gift message fields on the gift-card product and in the
  issued-card email.
- First-class exchange flow (currently: return + new order, marked
  with MissingFeature on `/grazinimas`).
- Email change flow (marked MissingFeature on personal info page).
- Blog: consider Strapi/headless CMS as a separate API rather than
  overloading this backend (decision pending, not implemented).
- Email sending tasks must respect `NewsletterSubscriber.is_active`.
