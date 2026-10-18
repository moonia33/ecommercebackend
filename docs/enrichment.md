# Catalog enrichment

Enrichment verčia nestruktūrinį produktų tekstą (pavadinimas / aprašymas /
SKU) į struktūrines `FeatureValue` reikšmes — be normalyzerio pakeitimų.

```
produkto tekstas -> matcher (contains/regex) -> ištraukta reikšmė
    -> value_format + value_template -> FeatureValue (sukuriama, jei nauja)
    -> ProductFeatureValue priskiriama produktui
```

## Kaip paleisti

- **Admin**: Catalog → Enrichment rules → pažymėti taisykles → action
  „Run selected rules (dry-run)" arba „(apply)". Visada pradėk su dry-run —
  jis rašo tą patį audit'ą (`EnrichmentRun`/`EnrichmentMatch`), bet
  nieko nekeičia.
- **CLI**: `./manage.py enrich_catalog [--dry-run] [--rule-id N]
  [--since ISO] [--limit N]`

Po run'o atsidaryk `EnrichmentRun` įrašą — `matches` rodo kiekvieną
produktą: kuriame lauke match'ino, ką rado, kokią reikšmę ištraukė ir
kodėl priskyrė/praleido (`assigned` / `skipped_exists` /
`skipped_conflict`).

## EnrichmentRule laukai

| Laukas | Ką daro |
|---|---|
| `name` | Laisvas pavadinimas — tik tau, kad po metų atsimintum kam taisyklė |
| `is_active` | Neaktyvios taisyklės run'uose praleidžiamos |
| `priority` | Didesnis — vykdoma anksčiau tame pačiame produkte |
| `feature` | **PRIVALOMAS.** Į kurį feature rašoma reikšmė (pvz. `Lure Size`). Be jo taisyklė praleidžiama |
| `matcher_type` | `contains` = pažodinis substring'as (tekstas normalizuojamas: lowercase, whitespace collapse). `regex` = Python regex, case-insensitive |
| `pattern` | Ko ieškoma. Contains: paprastas tekstas („Pro Shad"). Regex: pvz. `(\d+(?:[.,]\d+)?)\s*cm\b`. **Tuščias nieko neranda** |
| `extract_group` | Tik regex: 0 = visas match'as; N = N-oji capture grupė. `(\d+)\s*cm` su 1 → „14" iš „14cm" |
| `value_template` | Apgaubia ištrauktą reikšmę: `{{value}} cm` → „14" tampa „14 cm" |
| `fixed_value` | Tik contains: rašoma ši fiksuota reikšmė, ignoruojant ką rado |
| `value_format` | `decimal_trim`: „10.00"→„10", „2,5"→„2.5". `raw`: lieka kaip ištraukta |
| `match_in_name` | Ieškoti pavadinime — patikimiausias laukas |
| `match_in_description` | Ieškoti aprašyme. **Dėmesio:** aprašymai mini kitus skaičius → dažni false-positive'ai |
| `match_in_sku` | Ieškoti SKU / tiekėjo kode |
| `brand` / `category` / `product_group` | Scope filtrai — taisyklė veikia tik šiems produktams. Visi tušti = visam katalogui |
| `include_descendants` | Su category scope: įtraukti ir vaikų kategorijas |

## Saugikliai

- Jei produktas jau turi to feature reikšmę ir `feature.allows_multiple=False`
  → `skipped_conflict` (nieko neperrašo).
- Ta pati `FeatureValue` pernaudojama — `(feature, value)` unikalus.
- Kiekvienas run'as pilnai audituojamas.

## FeatureValueGroup / OptionValueGroup — filtrų grupavimas

Tiekėjai rašo tas pačias reikšmes skirtingai: `10-30`, `10–35`, `30 g`,
`30-g`, `200`, `200-g`. Filtras tada rodo dešimtis beveik vienodų
checkbox'ų.

**Sprendimas:** grupė = vienas kanoninis checkbox'as filtre, agreguojantis
daug žalių reikšmių. PDP ir toliau rodo žalią reikšmę.

- Admin: Catalog → **Feature value groups** / **Option value groups**.
- `code` naudojamas URL filtre: `?feature=casting_weight:iki-35`.
- `values` — M2M žalios reikšmės (rankinis parinkimas per
  `filter_horizontal`).
- `min_value` / `max_value` — skaitinės ribos. Kai užpildytos, admin
  action „Auto-assign values whose range fits within bounds" priskiria
  reikšmes, kurių parsuotas range'as **telpa** į ribas (containment, ne
  overlap: „10-30" ∈ „iki 35 g", „20-100" ∉).
- `parse_numeric_range` supranta `10-30`, `10–35 g`, `30 g`, `10,5-30`;
  sudėtines (`12cm/4.75in`) saugiai praleidžia.

**Filtrų elgsena:** facet'uose grupuotos reikšmės slepiamos ir rodoma
grupės eilutė; nesugrupuotos rodomos žalios. Filtras priima ir grupės
`code`, ir žalią `value` — atgalinis suderinamumas išsaugotas.
