# Trūkumai ir tobulintinos sritys

Dokumentas atnaujintas 2026-10-14 po antros pilnos peržiūros ir Neopay/Klix pašalinimo.

---

## Neseniai išspręsta

| # | Trūkumas | Sprendimas |
|---|---|---|
| 1 | Nėra asinchroninių užduočių | Celery + Redis: `send_email_task`, `process_product_image_task`, `notify_back_in_stock_task`, `execute_ops_job_task`, `expire_inventory_reservations_task` |
| 2 | Neopay webhook neaiškus | Neopay ir Klix pašalinti visiškai (su visais pėdsakais). Bus naujas provideris |
| 3 | Docker | `Dockerfile` + `docker-compose.yml` (db, redis, web, worker, beat) |
| 4 | `.delay()` krenta be Redis | `config.celery.safe_delay` helperis + stale PENDING sweep; `ops_api` fallback'ina į sync vykdymą; dev/test `CELERY_TASK_ALWAYS_EAGER=True` |
| 5 | Celery beat schedule | `api.0004_seed_periodic_tasks` migracija seed'ina `PeriodicTask` įrašus (DatabaseScheduler) |
| 6 | Nedeklaruotos dependencijos | `requirements.txt`: pridėta `requests`, `PyJWT`, `gunicorn` |

---

## Kritinio prioriteto trūkumai

### 1. Nėra aktyvaus online mokėjimo provaiderio

`PaymentIntent.Provider` šiuo metu turi tik `bank_transfer`. Reikia naujo provaiderio integracijos (LT rinkai realūs kandidatai: **Montonio**, **Paysera**, **MakeCommerce**, **Stripe**). `PaymentIntent` modelis ir `payment_redirect_url` payload struktūra jau paruošti — integracija pridedama kaip naujas provider `Provider` choices + `payments` app servisas ir callback endpoint'as.

### 2. Nėra grąžinimų ir refund workflow

Nėra modelių: kliento grąžinimo prašymas, grąžinta prekė, refund mokėjimas, atsargų korekcija.

Minimalūs modeliai `checkout` aplikacijoje:

```python
class Return(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="returns")
    requested_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    status = models.CharField(choices=[...])  # requested/approved/received/completed

class ReturnLine(models.Model):
    return_obj = models.ForeignKey(Return, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT)
    qty = models.PositiveSmallIntegerField()
    condition = models.CharField(...)  # good/damaged

class Refund(models.Model):
    return_obj = models.ForeignKey(Return, on_delete=models.PROTECT)
    payment_intent = models.ForeignKey(PaymentIntent, on_delete=models.PROTECT)
    amount_eur = models.DecimalField(max_digits=10, decimal_places=2)
    provider_refund_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(...)  # pending/completed/failed
```

### 3. Nėra order confirmation el. laiško

`send_templated_email` kviečiamas tik OTP ir back-in-stock. Po `/checkout/confirm` (ir ypač po sėkmingo apmokėjimo) klientas nieko negauna. Reikia:

- `order_confirmation` template + kvietimas po `Order` sukūrimo
- `payment_received` template + kvietimas kai `PaymentIntent.status → succeeded`
- `order_shipped` template + kvietimas kai `delivery_status → shipped`

---

## Svarbaus prioriteto trūkumai

### 4. Testų aprėptis kritiškai maža

| Aplikacija | Eilučių testams |
|---|---|
| `checkout` | **0** |
| `payments` | **0** |
| `accounts` | **~0** (stub) |
| `promotions` | **0** |
| `shipping` | **0** |
| `api` | ~386 (multi-shop izoliacija, ops API) |
| `catalog` | ~130 |

Prioritetas: `checkout` confirm flow + idempotency, `inventory` capture/release, kupono rezervavimas, `accounts` OTP flow. Dabar testai praeina be Redis (`CELERY_TASK_ALWAYS_EAGER`).

### 5. Rate limiting autentifikacijoje

OTP turi `attempts` ir resend cooldown'ą per email, bet nėra IP lygio limitų (`/auth/otp/request` — email flooding, `/auth/login` — brute force). Spręsti `django-ratelimit` arba nginx/Cloudflare lygmenyje.

### 6. Dideli failai

- `checkout/api.py` ~2350 eil. → skaldyti į `checkout/api/` paketą (cart, preview/confirm, orders, payment options)
- `checkout/ops_api.py` ~1450 eil. → `ops_orders`, `ops_shipments`, `ops_inventory`, `ops_catalog`, `ops_jobs`
- `catalog/models.py` ~1300 eil. → `catalog/models/` paketas (taxonomy, product, features, inventory, pricing_rules, content)

### 7. Paieška nėra implementuota

`search/` app buvo ištrintas (liko tik `.pyc` liekanos — ištrinti katalogą). Reikalingas sprendimas: Meilisearch (rekomenduojama — mature faceting, self-host docker) arba PostgreSQL `pg_trgm`/tsvector (paprastesnis path mažam katalogui).

---

## Vidutinio prioriteto trūkumai

### 8. CSRF su cookie auth

Ninja API neturi CSRF apsaugos; `SameSite=Lax` dengia daugumą atvejų, bet jei FE kitame subdomene — rizika. Spręsti CSRF token endpoint'u arba `SameSite=Strict`.

### 9. Guest checkout kontradikcija

`ALLOW_GUEST_CHECKOUT=True` default'as, bet `checkout_confirm` reikalauja auth ir `Order.user` yra not-null. Arba implementuoti guest flow, arba pakeisti default'ą į `False` ir pašalinti setting'ą.

### 10. Reviews / wishlist / PDF sąskaitos / CI/CD

- `ProductReview` modelis (`catalog` app) + `is_verified_purchase` per `OrderLine`
- `WishlistItem` modelis (`unique_together: user+variant+shop`)
- `Invoice` modelis + `weasyprint`/`reportlab` PDF generavimas per Celery
- `.github/workflows/ci.yml`: `migrate --check`, `manage.py check`, `manage.py test`

### 11. Tuščios `views.py` stub bylos

`accounts/views.py`, `catalog/views.py`, `api/views.py`, `notifications/views.py` — ištrinti.

### 12. `analytics` be skaitymo API

`track_event` naudojamas viduje (checkout), bet nėra endpoint'ų nei FE eventams, nei KPI skaitymui.

---

## Suvestinė

| # | Trūkumas | Prioritetas |
|---|---|---|
| 1 | Mokėjimų provaideris | Kritiškas |
| 2 | Grąžinimai/refund'ai | Kritiškas |
| 3 | Order confirmation email | Kritiškas |
| 4 | Testų aprėptis | Svarbus |
| 5 | Rate limiting | Svarbus |
| 6 | Dideli failai | Svarbus |
| 7 | Paieška | Svarbus |
| 8 | CSRF | Vidutinis |
| 9 | Guest checkout | Vidutinis |
| 10 | Reviews/wishlist/PDF/CI | Vidutinis |
| 11 | views.py stub'ai | Mažas |
| 12 | Analytics API | Mažas |
