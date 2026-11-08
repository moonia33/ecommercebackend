# Veiklos (žvejybos būdai ir kitos sritys)

Kategorija atsako į klausimą „kas yra prekė“, o veikla – „kam ji naudojama“. Veiklos yra atskira
daugelio su daugeliu ašis: viena prekė gali priklausyti kelioms veikloms (pvz. palapinė – ir
„Karpinė žvejyba“, ir „Stovyklavimas“). Kategorijų medis nekeičiamas.

## Modeliai (`catalog/models/activities.py`)

| Modelis | Paskirtis |
|---|---|
| `Activity` | Dviejų lygių medis: sritis (`parent=None`, pvz. `zvejyba`) → veikla (`karpine`). Pavadinimas, `short_description`, `content_json` (Tiptap įvadas), nuotrauka, SEO, `sort_order`, `is_active`, `min_products` (fronte rodoma tik turint bent tiek prekių, numatyta 20). |
| `ActivityTranslation` | Vertimai (`language_code`): pavadinimas, aprašymas, turinys, SEO. Tušti laukai imami iš LT. |
| `ActivityRule` | Automatinė priskyrimo taisyklė, žr. žemiau. |
| `ProductActivity` | Priskyrimas: `source=rule` (taisyklė, valdo variklis), `manual` (rankinis), `exclude` (rankinis išėmimas – nugali taisykles ir rankinį). |

Filtras ar kiekis pagal sritį apima visas aktyvias jos veiklas (unikalios prekės).

## Taisyklės (`ActivityRule`)

- **Sąraše – bet kuris (OR):** `categories` (su vaikais, jei `include_descendants`), `brands`.
- **Tarp skirtingų sąlygų – visos (AND):** kategorijos, gamintojai, `product_group`, `sku_prefix`,
  `feature`/`feature_value` (prekės ar varianto), tekstas.
- `exclude_categories` – šių kategorijų (su vaikais) prekės niekada nepriskiriamos
  (pvz. `atsargines-dalys`).
- Tekstas: `matcher_type=contains` – kelios frazės per `|` (OR), lyginama mažosiomis raidėmis;
  `regex` – Python regex. `match_in_name` / `match_in_description`.
- Taisyklė be jokios teigiamos sąlygos nieko nepriskiria.
- Išjungta taisyklė (arba išjungta veikla) pritaikius nuima savo priskyrimus.

Pavyzdys: „Palapinės + Fox/Trakker/Nash → Karpinė“ = `categories=[palapines-ir-skeciai]`,
`brands=[fox, trakker, nash]`.

## Komandos

```bash
manage.py seed_activities                       # pradinis medis + IŠJUNGTOS taisyklės (idempotentiška)
manage.py apply_activity_rules --dry-run --verbose-rules   # kiek atitiktų kiekviena taisyklė
manage.py apply_activity_rules [--rule-id N]    # įrašo ir perindeksuoja pakeistas prekes
```

Naktinis perskaičiavimas: Celery beat `catalog.apply_activity_rules` (04:10 Europe/Vilnius),
kad naujos importo prekės gautų veiklas. Admine taisyklės išsaugojimas iškart ją pritaiko;
yra veiksmai „dry-run“ ir „pritaikyti“ pažymėtoms taisyklėms. Prekės admine – inline
„Product activities“ rankiniams priskyrimams ir išėmimams.

## Paieška (Typesense)

Laukas `activities: string[]` (facet) – prekės veiklų ir jų sričių kodai, be išimtų.
Po deploy'aus naujas laukas pridedamas automatiškai, bet seni dokumentai jo neturi, kol
neperindeksuoti: `manage.py reindex_products` (~8 min) arba `apply_activity_rules` (perindeksuoja
tik pakeistas prekes).

## API

- `GET /api/v1/catalog/activities?include_hidden=false` – sritys su veiklomis:
  `code`, `name`, `parent_code`, `short_description`, `image_url`, `product_count`, `visible`,
  `children`. Be `include_hidden` nerodomos veiklos, kurių `product_count < min_products`.
- `GET /api/v1/catalog/activities/{code}` – nusileidimo puslapio duomenys: viskas kaip aukščiau +
  `content_json`, `seo_title`, `seo_description`, `parent`, `children` (sričiai),
  `top_categories` ir `top_brands` (iki 24, su kiekiais). Nerasta – 404.
- Filtras `activity=<kodas>` veikia `GET /catalog/products`, `GET /catalog/products/facets` ir
  `GET /catalog/categories/{slug}/products`. Nežinomas kodas – 404.

## Darbo tvarka

1. Taisyklės kuriamos išjungtos → `apply_activity_rules --dry-run --verbose-rules`.
2. Kiekiai ir pavyzdžiai peržiūrimi, tada taisyklė įjungiama ir pritaikoma.
3. Neaiškios prekės nepriskiriamos spėjant – geriau rankinis priskyrimas.
4. Po pritaikymo patikrinti Typesense dokumentų skaičių ir `activities` reikšmes.
