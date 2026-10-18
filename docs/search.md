# Search — Typesense

Katalogo paieška (`?q=`) eina per Typesense; kai jis nesukonfigūruotas arba
nepasiekiamas — automatinis fallback į ORM `icontains`. API kontraktas
nepasikeičia.

## Konfigūracija

```env
TYPESENSE_HOST=localhost      # arba docker serviso vardas
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=http
TYPESENSE_API_KEY=...
```

Lokaliai: `docker run -d --name typesense -p 8108:8108 -v typesense-data:/data
typesense/typesense:29.0 --data-dir /data --api-key=<key> --enable-cors`,
tada `./manage.py reindex_products`.

## Svarbūs techniniai sprendimai

- **Jokio `locale` ant searchable laukų.** Typesense locale processing
  (pvz. `lt`) vykdomas *prieš* synonym lookup → sinonimai netrigeruoja
  (patikrinta v28/v29). Diakritikus dengia `_norm()` — `search_text`
  laukas turi fold'intą kopiją, tad „meskere" randa „meškerė".
- **`infix: true`** ant `name`/`sku`/`search_text` — fragmentinė paieška
  („5500" randa „SLA-5500") per `infix=fallback`.
- **`ensure_collection()`** palygina laukų signature'us (`name`, `type`,
  `facet`, `optional`, `infix`, `index`) — schema pasikeitus kolekcija
  perkuriama automatiškai. `id` laukas išskirtas (Typesense managed).
- **Importo klaidos loguojamos** — `import_` per-document `success`
  tikrinamas; anksčiau reject'ai buvo nurisami tyliai.

## Sinonimai (admin → Search → Search synonyms)

Kiekviena eilutė = `syn-{id}` sinonimas Typesense'e, multi-way:
bet kuris terminas plečia visus kitus **užklausos metu**.

- `terms` — kableliais atskirti variantai („vojdzer, vojader, voyager").
- `root` — tik UX patogumas („kanoninis" terminas); jis **pridedamas prie
  grupės kaip paprastas narys**. Typesense `root` sinonimai veiktų
  index'o metu (dokumento terminai → root), o ne užklausos metu — mums
  to nereikia, tad `root` API'e nesiunčiamas.
- Sync: signalas → Celery `sync_search_curation_task`; naktinis resync
  04:45. Valdomi tik `syn-*` id — rankomis sukurti neliečiami.

## Overrides (curation)

`SearchOverride` — pin'uotos/exclude'intos prekės užklausai
(`query` + `exact`/`contains`), su `starts_at`/`ends_at` akcijų langais.
Pasibaigusios akcijos naktiniame resync'e išsivalo iš engine'o.

## Kas ORM'e (dar ne Typesense)

Struktūriniai filtrai (`category`, `brand`, `group`, `feature`, `option`)
ir facetų skaičiavimas — ORM. Typesense dokumentai jau neša
`category_ids[]`, `feature_facets[]`, `brand_id` facet laukus, jei vėliau
norėtume perkelti.
