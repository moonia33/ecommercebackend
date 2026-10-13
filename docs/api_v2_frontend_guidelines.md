# API v2 gairės frontend komandai

Trumpa FE santrauka. Pilna instrukcija: `docs/frontend_svelte5_api_guide.md`.

## 1) URL ir routing taisyklės

- Visi endpointai po `/api/v1/...`.
- Nenaudoti locale segmente (`/api/v1/lt/...` yra neteisinga).
- Shop kontekstas nustatomas pagal domeną (`Host`), ne per URL.

## 2) Kalbos ir vertimai

Fallback tvarka:

1. `?lang=...`
2. `Accept-Language`
3. `Shop.default_language_code`
4. global `LANGUAGE_CODE`

Numatytos palaikomos kalbos: `lt`, `pl`, `en`.
Papildomos kalbos (pvz. `de`) įjungiamos backend konfigūracijoje per `SUPPORTED_LANGUAGE_CODES`.
Papildomai, kiekvienam shop kalbos aktyvuojamos per `ShopLanguage` (admin), todėl pagal domeną aktyvių kalbų rinkinys gali skirtis.
`ShopLanguage` yra kanoninis šaltinis; `Shop.default_language_code` sinchronizuojamas iš pažymėtos `is_default` kalbos.
Po kalbų sąrašo pakeitimų backend migracija automatiškai išvalo nebeleidžiamus kodus pagal `SUPPORTED_LANGUAGE_CODES`.

Svarbu FE:

- lokalizuoti `slug` gali skirtis tarp kalbų (`/catalog/products/{slug}`),
- filtravimui naudoti stabilias reikšmes (`feature.code`, `option_value.code`, canonical `feature value`),
- atvaizdavimui naudoti lokalizuotus tekstus (`name`, `label`, `value_label`).

## 3) Dokumentacija

- Swagger: `/api/v1/docs`
- OpenAPI: `/api/v1/openapi.json`

## 4) Auth

- Tik HttpOnly cookies.
- Visi request su `credentials: 'include'` / `withCredentials: true`.

## 5) Checkout esmė

- Cart:
  - `GET /api/v1/checkout/cart?country_code=LT`
  - `POST /api/v1/checkout/cart/items?country_code=LT`
- Checkout:
  - `POST /api/v1/checkout/checkout/preview`
  - `POST /api/v1/checkout/checkout/confirm`
- Jei `shipping_method` reikalauja pickup point, siųsti `pickup_point_id`.
- `checkout/confirm` siųsti su `Idempotency-Key`.

## 6) Multi-shop rollout

- `SHOP_CONTEXT_ENABLED=false` - legacy/global elgsena.
- `SHOP_CONTEXT_ENABLED=true` - shop-level katalogo, checkout, shipping ir payment filtravimas.
- `SHOP_CONTEXT_STRICT=true` - jei host neatitinka jokio shop, grąžinamas 404 vietoje fallback.
