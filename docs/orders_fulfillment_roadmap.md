# Užsakymų / mokėjimų / fulfillment roadmap

Dabartinė būsena ir tolimesni darbai užsakymų srityje. Būsenų vertimai
(`checkout/statuses.py` + `GET /api/v1/checkout/statuses`) jau įgyvendinti.

---

## 1. Montonio integracija (payment provider)

**Kada:** kai klientas duos Montonio paskyrą (API key, access key/secret).

**Architektūra (sekti `bank_transfer` pattern'ą):**

- `PaymentIntent.Provider.MONTONIO = "montonio"` naujas choices.
- Naujas `payments/` app servisas:
  - `create_payment(order) → redirect_url` — Montonio POST `/payments` su
    `accessKey`, `merchantReference=order.id`, `returnUrl`, `notificationUrl`.
  - `handle_webhook(payload)` — Montonio callback'as order būsenai atnaujinti:
    `PaymentIntent.status → succeeded` + `Order.status → paid` vienoje
    transakcijoje (būtina `select_for_update` — webhook'ai kartais dubliuojasi).
  - Idempotencija: `external_id` unikalus; webhook'as jau `succeeded` intent'ui
    → no-op 200.
- `.env.example`: `MONTONIO_API_URL`, `MONTONIO_ACCESS_KEY`,
  `MONTONIO_SECRET_KEY`, `MONTONIO_RETURN_URL`, `MONTONIO_WEBHOOK_SECRET`.
- FE: `checkout/confirm` atsakyme `payment_redirect_url` jau yra — FE daro
  `window.location = payment_redirect_url`. Nieko naujo fronte.
- Testai: webhook su fake signature, idempotent retry, failed payment path.

**Sandėris:** Montonio apmokėjimo metodai (bank links, card) renkasi
Montonio pusėje — mums pakanka vieno provider kodo.

## 2. Sąskaitos (invoice / credit note)

**Modeliai (`checkout` app):**

```python
class Invoice(models.Model):
    class Kind(models.TextChoices):
        SALES = "sales"          # pardavimo sąskaita
        CREDIT = "credit"        # kreditinė sąskaita (grąžinimui)
    order = FK(Order, PROTECT, related_name="invoices")
    return_obj = FK("Return", null=True)   # tik credit kind
    kind = CharField(choices=Kind)
    number = CharField(max_length=32, unique=True)   # pvz. "INV-2026-000123"
    issued_at = DateTimeField()
    pdf = FileField(upload_to="invoices/%Y/%m/")
    # Denormalizuoti snapshot'ai (seller/buyer/totals) — sąskaita nesikeičia
    seller_snapshot = JSONField()
    buyer_snapshot = JSONField()
    totals = JSONField()  # net/vat/gross per tax rate
```

- **Numeracija:** per `Sequence` arba `Order.sequence` pattern — atskira
  sekvencija per metus + shop (`INV-2026-...`), transakcijoje.
- **PDF:** `weasyprint` arba `reportlab`; Celery task `generate_invoice_pdf_task`
  (lėtas operacija — ne request viduje).
- **Kada išrašoma:** konfigūruojama `SHOP_INVOICE_ON` — `paid` statusas arba
  `shipped` (pirkėjas turi teisę gauti sąskaitą iki prekių išsiuntimo).
- **Ops API:** `POST /ops/orders/{id}/invoice` (rankinė išrašymo),
  `GET` sąskaitų sąrašas per order, `download` endpoint.
- **Kreditinė:** kuriamas kartu su refund flow (žr. 3).

## 3. Grąžinimai / refund'ai

**Modeliai** (išplėsti gaps doc'o juodraštį):

```python
class Return(models.Model):
    class Status(models.TextChoices):
        REQUESTED, APPROVED, REJECTED, RECEIVED, COMPLETED, CANCELLED
    order = FK(Order, PROTECT)
    requested_by = FK(user)
    reason_code = CharField   # withdraw_14d / defective / wrong_item / other
    reason_text = TextField
    deadline_at = DateField   # skaičiuojama iš delivery_status → delivered
    status = CharField(choices=Status)

class ReturnLine(Return, FK OrderLine, qty, condition, inspected_at)

class Refund(models.Model):
    class Method(models.TextChoices):
        ORIGINAL_PAYMENT = "original_payment"   # Montonio refund API
        BANK_TRANSFER = "bank_transfer"         # rankinis pavedimas
        CREDIT_NOTE = "credit_note"             # tik kreditinė sąskaita
    return_obj = FK(Return)
    amount_gross = DecimalField
    method, status, provider_refund_id, invoice (credit note FK)
```

**Taisyklės pagal pristatymo būseną:**

- Return'as galimas tik jei `order.delivery_status in (delivered,)` **ir**
  `delivered_at <= today - 0` (reikia naujo lauko `Order.delivered_at`
  užfiksuoti iš carrier webhook/sync — dabar jo nėra!).
- `deadline_at = delivered_at + 14 d.` (ES atsisakymo teisė), konfigūruojama
  `RETURN_WINDOW_DAYS=14`.
- Kai bus `Shipment` modelis — `delivered_at`/window skaičiuojami **per eilutę**
  iš `ShipmentLine.shipment.delivered_at`, ne order'io lygmenyje.
- Jei `delivery_status != delivered` → API 409 "grąžinimas negalimas".
- Credit note variantas: jei pardavimo sąskaita jau išrašyta → credit invoice;
  jei dar neišrašyta (`Invoice` nėra) → paprastas pinigų grąžinimas be
  kreditinio dokumento. `Refund.method` pasirinkimas ops'ui.

### Per-prekės grąžinimo politika

Kai kurios prekės (elektronika, higienos, užsakomos) neturi 14d atsisakymo
teisės. `ReturnPolicy` modelis pagal `TaxClass` pattern'ą:

```python
class ReturnPolicy(models.Model):
    class Eligibility(models.TextChoices):
        STANDARD = "standard"          # 14d atsisakymo teisė
        DEFECTS_ONLY = "defects_only"  # tik garantija/brokas — ne withdrawal
        NON_RETURNABLE = "non_returnable"  # final sale
        EXTENDED = "extended"          # ilgesnis langas (marketing'ui)
    code = CharField(unique=True)        # "standard", "electronics"...
    eligibility, return_window_days, requires_approval
    description per kalbą (rodoma PDP ir checkout'e)

Product.return_policy = FK(ReturnPolicy, null=True)
```

**Resolution:** `product.return_policy` → `category.default_return_policy` →
shop default. Tas pats kaip `tax_class` šiandien.

**Kategorija yra tik default'as — priskyrimo mechanizmai (svarbu: kategorijoje
būna mišrių prekių). Return politika yra shop'o sprendimas — normalizeris
jos nesiunčia:**

1. **`EnrichmentRule` išplėtimas** — esamas masinis atributų mechanizmas:
   rule'as match'ina `name`/`description`/`sku` pattern'ą per
   brand/category/product_group scope + dry-run + per-product match audit
   (`EnrichmentMatch`). Pridėti `action` lauką (`set_feature` | `set_return_policy`)
   + `return_policy` FK — tada tas pats pipeline žymi ir features, ir
   return politiką (pvz. "viskam kur name contains 'baterija'/'elektroninis'
   kategorijoje X → defects_only").
2. **Ops bulk action** — esamas `POST /ops/catalog/bulk-actions`
   (activate/deactivate) išplečiamas `set_return_policy` action'u su
   `product_ids[]` + `policy_code` — pavienėms/parenkoms grupėms iš
   ops sąrašo UI.
3. **`ProductAdmin`** field + `list_editable` — individualios išimtys.

**Svarbiausia:** `Product.return_policy` visada laimi prieš kategoriją — tai
ir yra atsakas mišrioms kategorijoms. Kategorijų default'as tik sumažina
rankinį darbą (naujos prekės kategorijoje paveldi policy), o ne sprendžia.
Enrichment run'ai paleidžiami periodiškai per `catalog_import` sync — naujos
prekės iš normalizerio automatiškai peržiūrimos taisyklėmis.

**Snapshot:** `OrderLine.return_policy_code` + `returnable_until` snapshot'ai
užsakymo metu — politika gali keistis vėliau, bet pardavimo momentas fiksuotas.

### Du lygiai vietoj "admin'as tvirtina viską"

`ReturnLine` — tai **eilutės**, ne order'io, lygio. Eligibility skaičiuojama
per eilutę + `reason_code`:

| reason_code | standard | defects_only | non_returnable |
|---|---|---|---|
| `withdraw_14d` | ✓ auto | ✗ | ✗ |
| `defective` | ✓ auto | ✓ auto | ⚠ review |
| `wrong_item` / `damaged` | ✓ auto | ✓ auto | ⚠ review |

- `auto` — `Return.status → approved` iš karto (klientui siunčiamas return label)
- `review` — `requires_approval` → `requested` eilė ops'ui
- `✗` — API 409 "šios prekės negalima grąžinti" dar prieš sukuriant Return'ą;
  mixed order'yje FE rodo kiekio selektorių tik eligible eilutėms

**Defects/wrong_item neblokuojami net NON_RETURNABLE** — tai pardavėjo klaida /
atitikties garantija (legal requirement), ne atsisakymo teisė. Admin'ui lieka
tik flag'inti atvejai: neigiamai įvertintas `condition`, `non_returnable +
defective` claims (sukčiavimo kontrolė), didesnės sumos.

**Ops flow:** ops mato `returns` sąrašą → approve tik `requested` eilę →
klientas siunčia prekę → `received` → sandėlio inspekcija
(`ReturnLine.condition`) → `completed` + `Refund` + `CreditNote` jei reikia +
`InventoryItem` korekcija (qty +condition). Auto-approved return'ai praleidžia
pirmą žingsnį — ops'ui lieka inspekcija.

## 4. Split shipments — analizė

**Dabartinis modelis:** viena `Order` = viena siunta. `tracking_number`,
`carrier_shipment_id`, `shipping_label_pdf`, `delivery_status` — viskas
order'io lygmenyje. **Dalinio išsiuntimo šiandiena nepalaikoma.**

**Ko reikia:**

```python
class Shipment(models.Model):
    order = FK(Order, related_name="shipments")
    warehouse = FK(Warehouse)
    carrier_code, tracking_number, label_pdf, status (per-shipment delivery)
    shipped_at, delivered_at

class ShipmentLine(models.Model):
    shipment = FK(Shipment)
    order_line = FK(OrderLine)
    qty = PositiveSmallIntegerField()
```

- `Order.delivery_status` tampa **agregatu** iš `shipments[]` (partially_shipped
  / shipped / delivered naujos būsenos — pridėti į `statuses.py`).
- Ops API: `POST /ops/orders/{id}/shipments` (sukurti siuntą su
  `{lines: [{order_line_id, qty}]}`) → carrier label tik tai siuntai.
- Backward compatible: viena siunta = `Shipment` su visomis eilutėmis.
- ETA laukai lieka order'io lygmenyje (max iš shipment'ų).

**Pastaba:** `supplier_reservation_status` + `fulfillment_mode` jau skiria
stock/dropship/mixed — `Shipment` natūraliai susiejamas: siunta #1 iš
`local_wh` (stock dalis), siunta #2 kai `supplier_reservation → reserved`
(atvyko iš tiekėjo). Tai ir yra realus scenarijus, kurį aprašei.

## 5. SMS OTP (ateitis — Bulkgate)

- `accounts` OTP flow jau abstrahuotas (`EmailOTP` modelis + send).
- Pridėti `SMSOTP` arba generalizuoti `OTP.channel` (`email|sms`).
- `UserPhone` modelis jau yra — SMS OTP naudoja `phone` kaip identitetą.
- Bulkgate: HTTP API, `BULKGATE_API_TOKEN` env, siuntimas Celery.
- Reikia kliento sprendimo: SMS = kaina per siuntą, reikalinga jei dalis
  klientų neturi email'o arba retkarčiais email'ai krinta į spam'ą.

## Prioritetų siūlymas

| # | Darbas | Priklauso nuo | Blokuoja |
|---|---|---|---|
| 1 | Montonio | kliento acc/key'ai | mokėjimai online |
| 2 | `delivered_at` laukas + fiksavimas | carrier webhook'ai jau yra | returns |
| 3 | Invoice modelis + PDF | — | returns (credit note) |
| 4 | Return/Refund | #2, #3 | — |
| 5 | Split shipments (`Shipment` modelis) | ops flow'ų apsibrėžimas | dropship UX |
| 6 | SMS OTP | kliento sprendimas | — |

#5 yra struktūriškai didžiausias pakeitimas — jei dropship prekės ateis
vėliau nei sandėlio, be `Shipment` modelio ops'ui teks rankiniu darbu. Verta
padaryti prieš pirmą realų dropship supplier'į.
