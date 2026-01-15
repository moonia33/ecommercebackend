# Recommendations (PDP cross-sell / upsell)

Šitas dokumentas aprašo PDP rekomendacijų (cross-sell/upsell) realizaciją.

Tikslai:

- Turėti rankiniu būdu valdomą, simetrišką (mirror) rekomendacijų mechanizmą per admin.
- Turėti automatinį (rule-based) fallback, jei rankinių rekomendacijų nėra arba jų nepakanka.
- Užtikrinti multi-site matomumą: site nenaudojamas kaip atskiras scope rekomendacijoms, bet **filtruoja matomumą** pagal esamas assortment taisykles.

## Endpointas

- `GET /api/v1/recommendations/products/{slug}?country_code=LT&channel=normal`

Query parametrai:

- `country_code` (privalomas, 2 raidės, pvz. `LT`) – reikalingas kainodarai/VAT.
- `channel` (privalomas) – `normal` arba `outlet`.

Klaidos:

- `404 Product not found` – jei produktas nerastas arba nevisible pagal site assortment.
- `400 Site is not resolved` – jei request’e neatsiranda `request.site`.

## Response formatas

Response grąžina rekomendacijų blokus, kuriuos FE gali atvaizduoti kaip atskirus PDP komponentus.

```json
{
  "blocks": [
    {
      "key": "manual_complements",
      "name": "Dažnai perkama kartu",
      "items": [/* ProductListOut[] */]
    },
    {
      "key": "auto_cross_sell",
      "name": "Cross-sell",
      "items": [/* ProductListOut[] */]
    },
    {
      "key": "auto_upsell",
      "name": "Upsell",
      "items": [/* ProductListOut[] */]
    }
  ]
}
```

- `key` – stabilus identifikatorius FE pusėje (kad būtų galima mapping’inti į komponentus).
- `name` – label/pavadinimas, kurį galima rodyti UI.
- `items` – `list[ProductListOut]` (tas pats formatas kaip `/api/v1/catalog/products`).

Pastaba:

- Blokų sąrašas yra dinaminis: jei blokas neturi item’ų – jis negrąžinamas.

## Manual rekomendacijos (set / rinkiniai)

### Idėja

Rankinės rekomendacijos realizuojamos per **set/rinkinį**:

- Admin’e sukuri `RecommendationSet`.
- Pridedi į jį 2–N prekių (`RecommendationSetItem`).

**Mirror** logika:

- Jei prekė A ir prekė B yra tame pačiame set’e, tada:
  - PDP A rekomendacijose bus B
  - PDP B rekomendacijose bus A

Tai išsprendžia dvigubo rankinio darbo problemą.

### `kind`

Set’as turi `kind`, kuris virsta atskiru bloku FE pusėje:

- `complements` → `manual_complements` ("Dažnai perkama kartu")
- `upsell` → `manual_upsell` ("Rekomenduojame rinktis geresnį")
- `similar` → `manual_similar` ("Panašios prekės")

### Rikiavimas

- Manual item’ai rikiuojami pagal `sort_order`, tada pagal `id`.

## Auto rekomendacijos (rule-based)

Auto blokai yra skirti užpildyti PDP, kai manual rekomendacijų nėra arba norisi papildomų pasiūlymų.

### `auto_cross_sell`

- Jei produktas turi `group_id` → ieškoma kitų produktų tame pačiame `group`.
- Jei `group_id` nėra → ieškoma pagal `brand_id` ir `category_id` (jei jie yra).

### `auto_upsell`

- Ieškoma produktų toje pačioje `category` ir `brand`, kurių minimali aktyvaus varianto kaina patenka į band’ą:
  - nuo `+15%` iki `+80%` (lyginant su šio produkto min aktyvaus varianto kaina)

### Deduplikacija

- Auto rekomendacijos negrąžina prekių, kurios jau pateko į manual bloką.
- `auto_upsell` papildomai negrąžina prekių, kurios jau pateko į `auto_cross_sell`.

## Multi-site matomumas

Rekomendacijos neturi savo site scopo. Vietoje to, prieš grąžinant prekes:

- pritaikomos esamos catalog assortment taisyklės (site visibility / exclusions).

Tai garantuoja, kad FE negaus nevisible prekių.

## Konfigūracija (.env)

Limitai valdomi per settings/env (be kodo keitimo):

- `RECO_MANUAL_LIMIT` (default `12`) – manual blokams per `kind`.
- `RECO_CROSS_SELL_LIMIT` (default `12`) – `auto_cross_sell`.
- `RECO_UPSELL_LIMIT` (default `8`) – `auto_upsell`.

Pavyzdys `.env`:

```env
RECO_MANUAL_LIMIT=12
RECO_CROSS_SELL_LIMIT=8
RECO_UPSELL_LIMIT=6
```

## Admin

Modeliai:

- `recommendations.RecommendationSet`
- `recommendations.RecommendationSetItem`

Admin UI:

- `RecommendationSet` turi inline item’ų redagavimą (patogu suvesti rinkinį viename ekrane).
