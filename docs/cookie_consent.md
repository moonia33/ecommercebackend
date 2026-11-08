# Slapukų sutikimas (cookie banner)

Juostą rodo storefront'as (bz_sveltefront). Backend'as teikia:

- `ShopConfig.gtm_container_id`, `ga4_measurement_id`, `meta_pixel_id` – žymos, kurias frontas
  krauna su Google Consent Mode v2 (numatytai visi signalai `denied`). Meta Pixel kraunamas tik
  gavus „Rinkodara“ sutikimą.
- `ShopConfig.cookie_consent_version` – padidinus, visi lankytojai klausiami iš naujo
  (pvz. pridėjus naują marketingo servisą).
- Visa tai grąžina `GET /api/v1/shop/config`.
- `POST /api/v1/analytics/cookie-consent` – sutikimo įrodymas, tik pridedami įrašai
  (`CookieConsentRecord`: anoniminis `consent_id`, versija, veiksmas, kategorijos, naršyklė, laikas).
  IP nesaugomas. Admin'e įrašai tik skaitomi.
  Ribojimas: 30 įrašų per valandą vienam IP (`CONSENT_RATE_LIMIT`, Django cache/Redis), virš to 429.
  IP imamas iš `CF-Connecting-IP` (`api.client_ip`), nes origin priima tik Cloudflare srautą.

```json
{"consent_id": "uuid", "version": 1, "action": "accept_all|reject_all|custom",
 "functional": true, "analytics": true, "marketing": false}
```
