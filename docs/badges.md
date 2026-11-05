# Prekių badge'ai (marketingo žymės)

„Naujiena 2027“, „Akcija“, „Top“ ir pan. — **atskirai nuo feature'ų** (feature = techniniai
parametrai specifikacijų lentelėje ir filtruose). Kodas: `catalog/models/badges.py`,
`catalog/badges.py`.

## Modeliai

| Modelis | Paskirtis |
|---|---|
| `Badge` | `code` (slug, unikalus), `name` (LT), `style` (`primary/success/warning/danger/info/dark`), `color` (`#RRGGBB`, perrašo stilių), `priority` (didesnis – pirmiau), `is_active`, `valid_from`/`valid_to` |
| `BadgeTranslation` | `name` kitoms kalboms |
| `ProductBadge` | prekė ↔ badge; `source` = `manual` / `import` / `rule`; `rule` (kai rule); `valid_until` |
| `BadgeRule` | automatinis priskyrimas pagal sąlygas (žemiau) |

Viena prekė gali turėti daug badge'ų, vienas badge – daug prekių. Šaltiniai nepriklausomi:
taisyklė niekada nenuima rankinio ar importo priskyrimo ir atvirkščiai.

## Admin

- **Catalog → Badges**: CRUD, vertimai, prekių priskyrimas (inline). Prekės admine – badge inline.
- **Catalog → Badge rules**: sąlygos + actions „dry-run“ / „apply“. Išsaugojus taisyklę ji pritaikoma.
- Naktį 03:50 (Europe/Vilnius) Celery beat `catalog.apply_badge_rules` perskaičiuoja visas aktyvias taisykles.
- CLI: `manage.py apply_badge_rules [--dry-run] [--rule-id N]`.

## BadgeRule sąlygos (visos AND; tuščia = netaikoma)

`brand`, `category` (+ `include_descendants`), `product_group`, `sku_prefix`, `feature` / `feature_value`,
teksto matcher (`matcher_type` contains/regex, `pattern`, `match_in_name/description/sku` – kaip
enrichment), `in_stock_only` (+ `warehouse`), `discounted_only`, `created_within_days`.
Išjungta taisyklė nuima savo priskyrimus.

## Importas

Katalogo importo prekės laukas `badges: ["new-2027", ...]` (`docs/normalizer_integration.md`):
- `null` / nėra → nieko nekeisti;
- sąrašas → sinchronizuojami **tik `source=import`** priskyrimai: nebeatsiųsti nuimami;
- nežinomas kodas → sukuriamas `Badge(code, name=code)` – pavadinimą/stilių sutvarkyti admine.

Albatros: `new-2027` iš Stock failo A stulpelio „New 2027“ / `2027 NEW.xlsx` (normalyzer dar nesiunčia).

## Output

- Prekių sąrašas ir detalė: `badges: [{code, name, style, color}]` (tik gyvi: aktyvus, galioja
  pagal datas, rūšiuota pagal `priority`, be dublių, išversta pagal kalbą). Papildomos užklausos nereikia.
- Typesense: `badges` (`string[]`, facet); katalogo `?collection=<kodas>` filtruoja prekes su badge'u.
- Badge / priskyrimo pakeitimai perindeksuoja susijusias prekes (`search/signals.py`).
- FE: `ProductBadges.svelte` (kortelė + PDP). Senas `labels.includes('new')` rodomas tik kai badge'ų nėra.
