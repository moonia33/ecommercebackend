# CMS: blogas ir turinio redaktorius (Tiptap)

> **Būsena:** deploy'inta 2026-11-08. Backend `e14f47d` ([ecommercebackend#20](https://github.com/moonia33/ecommercebackend/pull/20)), admin'as `fbbc48b` ([ecommerceadmin#2](https://github.com/moonia33/ecommerceadmin/pull/2)), storefront `690d991` ([bz_sveltefront#9](https://github.com/moonia33/bz_sveltefront/pull/9)). Migracija `cms/0014_blog_and_rich_text` pritaikyta.
> Instrukcija rašantiems – wiki: /baltijoszvynas/vadovas/blogas.

## Sprendimas ir kodėl taip
- Blogas ir turinio redaktorius padaryti **esamame Django `cms` app'e**, ne atskirame CMS (Wagtail, Directus, Strapi, Ghost). Priežastys: jau yra `shop` dimensija, vertimai su fallback grandine, SEO laukai; straipsnius reikia sieti su prekėmis ir kategorijomis (paprasti `ManyToMany`), o kainas / likučius / badge'us API grąžina tuo pačiu `products_payload`, kaip katalogas. Vienas deploy, viena DB, vienas backup.
- Redaktorius – **Tiptap 3** (MIT, nemokamas; mokama tik jų debesų platforma, kurios nenaudojame) **ecommerceadmin'e** (admin.baltijoszvynas.lt), nes rašys ir netechniniai žmonės.
- Turinys saugomas **Tiptap JSON** (`body_json`), ne Markdown: Markdown pameta lentelių, paveikslėlių ir prekių blokų struktūrą. HTML generuoja **backend'as** (išvalytas), front'as tik rodo.

## Duomenų modelis (`cms/models.py`)

| Modelis | Paskirtis / svarbūs laukai |
|---|---|
| `CmsMedia` | Redaktoriuje įkelti paveikslėliai: `image` (`cms/media/%Y/%m/`, R2 per `cdn.baltijoszvynas.lt`), `alt_text`, `width`, `height`, `uploaded_by`, `created_at`. |
| `BlogCategory` + `BlogCategoryTranslation` | Blogo kategorijos (ne katalogo!): `slug`, `shop` (null = visos), `sort_order`, `is_active`; vertimas – `name`, `description`. |
| `BlogPost` | `shop` (null = visos parduotuvės), `status` (`draft` / `published`), `published_at`, `author` (FK į vartotoją), `author_name` (rodomas vardas), `cover_image` (`cms/blog/`), `categories` (M2M `BlogCategory`), `related_products` (M2M `catalog.Product`), `related_categories` (M2M `catalog.Category`), `is_featured`, `created_at`, `updated_at`. |
| `BlogPostTranslation` | Viena eilutė kalbai: `language_code`, `slug` (kalbinis), `title`, `excerpt`, `body_json`, `seo_title`, `seo_description`, `cover_image_alt`, `reading_minutes` (skaičiuojamas `save()` metu: žodžiai / 200, min. 1), `updated_at`. |
| `BlogSlugRedirect` | Seni slug'ai: `post`, `language_code`, `old_slug`. Naudojami 301 nukreipimui. |
| `CmsPageTranslation.body_json` | **Naujas** nullable laukas informaciniams puslapiams. Senas `body_markdown` paliktas: kol puslapis neperrašytas naujame redaktoriuje, API rodo Markdown kaip anksčiau. |

### Būsenos
DB yra tik dvi reikšmės (`draft`, `published`). Trečia – **„suplanuota“** – išvedama: `published` + `published_at` ateityje. Viešas API rodo tik `status=published AND published_at <= now()`, todėl suplanuotas straipsnis pasirodo pats, be cron'o.
- Publikuojant be datos `published_at` nustatomas į dabar (ir ops API, ir `BlogPost.save()` – kad veiktų ir iš Django admin).
- Laikas be laiko juostos (naive) interpretuojamas kaip `Europe/Vilnius` (`timezone.make_aware`).

### Slug'ai ir nukreipimai
- Slug'as unikalus kalbos ribose toje pačioje parduotuvėje (ar globaliai, jei `shop` tuščias). Konfliktas → 400 „Slug „…“ (lt) jau naudojamas kitame įraše“.
- Pakeitus slug'ą senas įrašomas į `BlogSlugRedirect`. Viešas detail endpoint'as per seną slug'ą grąžina `redirect_to_slug`, front'as daro **301**.
- Jei slug'ą perima kitas įrašas, seno įrašo nukreipimas ištrinamas **tik jei perimantis įrašas jau publikuotas** (juodraštis negali „pavogti“ gyvo adreso). Valymas ribojamas ta pačia parduotuve.
- Jei straipsnis neišverstas į prašomą kalbą, grąžinamas fallback kalbos vertimas, bet `redirect_to_slug` tik tada, kai slug'as tikrai kitas (kitaip būtų nukreipimas į save).

### Vertimai
Ops API siunčia **pilną** vertimų sąrašą. Vertimai, kurių nėra užklausoje (arba be pavadinimo), **ištrinami** – admin'e pašalinta kalba dingsta ir iš svetainės. Publikuoti reikia bent vieno vertimo su pavadinimu. Kalbos – `SUPPORTED_LANGUAGE_CODES` (lt, en, pl, lv, et, de); fallback – `translation_fallback_chain()`.

## Rich text (`cms/richtext.py`, `cms/content.py`)
- `doc_to_segments(doc)` verčia Tiptap JSON į segmentų sąrašą: `{"type":"html","html":…}` ir `{"type":"products","product_ids":[…],"title":…}`.
- Palaikomi mazgai: `paragraph`, `heading` (h2–h4; h1 pasilieka puslapio pavadinimui), `bulletList`, `orderedList` (`start`), `listItem`, `blockquote`, `codeBlock`, `horizontalRule`, `hardBreak`, `image`, `table`/`tableRow`/`tableHeader`/`tableCell` (`colspan`, `rowspan`), `productCards`. Žymės: `bold`, `italic`, `underline`, `strike`, `code`, `link`.
- Viskas dar kartą praleidžiama per **bleach** su baltuoju sąrašu (`ALLOWED_TAGS`, `ALLOWED_ATTRS`). Nuorodos – tik `http(s)://`, `mailto:`, `tel:`, santykinės `/…` ir `#…`; `javascript:` ir `//host` atmetami. Paveikslėliai – tik `http(s)://` ir `/…`, gauna `loading="lazy"`. Išorinės nuorodos gauna `target="_blank" rel="noopener noreferrer"`.
- `productCards` – mūsų Tiptap mazgas (`attrs.productIds`, `attrs.title`), daugiausia 12 prekių bloke. API jį paverčia `content_blocks` elementu `{"type":"products","title":…,"products":[ProductListOut…]}` – kainos, likučiai, badge'ai ir pristatymo terminai skaičiuojami **tuo pačiu kodu kaip katalogas**, parduotuvės ir kalbos kontekste. Nebeaktyvios / nerastos prekės tiesiog praleidžiamos.
- Tas pats `content_blocks` formatas naudojamas ir `/cms/pages/{slug}` (info puslapiams), kai yra `body_json`.
- JSON dokumento dydis ribojamas 1 MB.

## Viešas API (`/api/v1/cms/blog/…`, `cms/blog_api.py`)
Parduotuvė nustatoma pagal domeną (shop context), kalba – `language_code` parametras arba `Accept-Language`. **Storefront visada siunčia `language_code=instanceLocale()`**, kad svetainės kalbą lemtų domenas, o ne naršyklė.

| Metodas | Kelias | Parametrai | Atsakymas |
|---|---|---|---|
| GET | `/cms/blog/posts` | `language_code`, `category` (blogo kategorijos slug), `product` (prekės ID), `catalog_category` (katalogo kategorijos ID), `featured`, `page`, `page_size` (≤48, default 12) | `{page, page_size, total_count, results: BlogPostListOut[]}`, rikiuota `-published_at` |
| GET | `/cms/blog/posts/{slug}` | `language_code` | `BlogPostOut`: sąrašo laukai + `seo_title`, `seo_description` (fallback į title/excerpt), `updated_at`, `content_blocks`, `alternates` (kalba+slug), `related_products` (≤12), `redirect_to_slug` |
| GET | `/cms/blog/categories` | `language_code` | `[{slug, name, description, post_count}]` |
| GET | `/cms/blog/sitemap` | – | `[{language_code, slug, updated_at}]`, `updated_at` = vėlesnis iš vertimo ir įrašo |

`BlogPostListOut`: `id, slug, language_code, title, excerpt, cover_image, cover_image_alt, published_at, reading_minutes, author_name, categories[], is_featured`.

## Ops API (admin'ui, `cms/ops.py`, prefiksas `/api/v1/ops`)
Visi endpoint'ai reikalauja ops vartotojo (staff / superuser) – tas pats `_require_ops_user` kaip kituose ops moduliuose.

| Metodas | Kelias | Paskirtis |
|---|---|---|
| GET | `/cms/lookups` | Parduotuvės, kalbos, numatytoji kalba, pagalbos (CMS) kategorijos, blogo kategorijos – formų pasirinkimams |
| POST | `/cms/media` | Paveikslėlio įkėlimas redaktoriui (`file`, `alt_text`) → `{id, url, width, height}` |
| GET / POST | `/cms/pages` | Puslapių sąrašas (`q`, `shop_id`, puslapiavimas) / naujas puslapis |
| GET / PATCH | `/cms/pages/{id}` | Puslapis su visais vertimais / atnaujinimas |
| POST | `/cms/pages/{id}/hero?language_code=` | Hero paveikslėlis konkrečiai kalbai |
| GET / POST | `/cms/blog/posts` | Įrašų sąrašas (`q`, `state` = draft/scheduled/published, `shop_id`) / naujas įrašas |
| GET / PATCH / DELETE | `/cms/blog/posts/{id}` | Įrašas / atnaujinimas / ištrynimas |
| POST | `/cms/blog/posts/{id}/cover` | Viršelio įkėlimas |
| GET / POST / PATCH | `/cms/blog/categories[/{id}]` | Blogo kategorijos |

**Įkėlimai:** tik JPEG, PNG, WebP, AVIF, GIF; ≤10 MB; turinys tikrinamas Pillow `verify()` (pervadintas ne-paveikslėlis atmetamas); **SVG draudžiamas** (XSS rizika).

## Django admin
Blog posts / Blog categories / Cms media taip pat matomi Django admin'e (`cms/admin.py`) – avariniam taisymui. Kasdieniam darbui naudoti admin.baltijoszvynas.lt.

## Admin FE (ecommerceadmin)
- Meniu „Turinys“: **Puslapiai** (`/turinys`, `/turinys/[id]`) ir **Blogas** (`/blogas`, `/blogas/[id]`; naujas – `/blogas/naujas`, `/turinys/naujas`).
- Komponentai `src/lib/components/cms/`: `RichEditor.svelte` (Tiptap: StarterKit, Link, Image, Table, Underline, Placeholder + mūsų `productCardsNode.ts`), `ProductPicker.svelte` (prekių paieška pagal pavadinimą/SKU), `LanguageTabs.svelte`, `SeoFields.svelte` (Google peržiūra, 60/160 simbolių skaitikliai), `legacy.ts` (Markdown → HTML per `marked`, `slugify`).
- Paveikslėliai: mygtukas, nutempimas (drop) ar įklijavimas (paste) → `POST /ops/cms/media` → įterpiamas `<img>` su CDN URL.
- **Seni Markdown puslapiai** atidaromi konvertuoti į redaktorių; išsaugojus įrašomas `body_json`, o `body_markdown` lieka DB (nebenaudojamas, kol yra `body_json`). Neišsaugoti puslapiai svetainėje rodomi kaip anksčiau.
- API klientas: `src/lib/api/client.ts` (`cmsLookups`, `cmsPages…`, `blogPosts…`, `uploadCmsMedia`…).

## Storefront (bz_sveltefront)
| Kelias | Failas | Kas |
|---|---|---|
| `/patarimai` (ne-LT instance – `/blog`) | `src/routes/patarimai/+page.*` | Sąrašas po 12, `?kategorija=<slug>` filtras, `?page=N`; canonical su puslapio numeriu; BreadcrumbList |
| `/patarimai/[slug]` | `src/routes/patarimai/[slug]/+page.*` | Straipsnis: `content_blocks` (HTML + prekių blokai per `ProductRow`), susijusios prekės, JSON-LD `Article` + `BreadcrumbList`, canonical, OG (`og:type=article`). `redirect_to_slug` → 301 |
| `/sitemap-patarimai.xml` | `src/routes/sitemap-patarimai.xml/+server.ts` | Instance kalbos straipsniai, cache 1 h |
| `/info/[slug]` | `src/routes/info/[slug]/+page.svelte` | Dabar rodo ir `products` blokus |
| `/p/[slug]` | `src/routes/p/[slug]/+page.*` | „Susiję straipsniai“ – iki 3 straipsnių, kuriuose prekė pažymėta kaip susijusi (`getBlogPosts({product})`) |

- `/naujienos` lieka **naujoms prekėms** – todėl blogas yra `/patarimai`.
- Alias `'/patarimai': '/blog'` – `vite.config.ts` `ROUTE_ALIASES`; nuorodos per `lh()`.
- Komponentai: `src/lib/components/blog/BlogPostCard.svelte`, `RelatedPosts.svelte`, `date.ts` (lt datos Vilniaus laiku).
- API: `getBlogPosts`, `getBlogPost`, `getBlogCategories` (`src/lib/api/endpoints.ts`), tipai `BlogPostOut` ir kt. (`types.ts`).

## Testai
`python manage.py test cms` – 9 testai: rich text valymas (`javascript:` atmetimas, išorinės nuorodos), prekių blokai, vieši filtrai, suplanuoti įrašai nematomi, slug'o nukreipimai (ir juodraštis nepavagia), vertimų trynimas, publikavimas be datos, ops leidimai (ne-staff → 403), įkėlimo validacija (SVG ir netikras failas atmetami). Prieš merge pilnas `cms legal api` – 209 testai OK.

## Patikrinta po deploy'aus (stage, curl)
`/api/v1/cms/blog/posts` ir `/categories` → 200 (tušti), `/patarimai` → 200, `/sitemap-patarimai.xml` → 200, `/info/*`, `/pagalba`, prekės puslapis → 200, admin `/blogas`, `/turinys` → 200. Naršyklėje viso kelio (rašymas → publikavimas → svetainė) dar netestuota.

## Žinomi apribojimai ir kiti žingsniai
- **AI vertimas** (mygtukas „Išversti į …“ redaktoriuje per OpenAI, verčiant Tiptap JSON tekstinius mazgus) – dar nepadaryta.
- **Sitemap / robots:** `/sitemap-patarimai.xml` dar neįrašytas į `robots.txt`, bendro `sitemap.xml` (index) nėra; stage `robots.txt` = `Disallow: /` (`ROBOTS_DISALLOW_ALL`). Sitemap'ai ir `llms.txt` (AI botams) – atskiras darbas.
- RSS nėra.
- Įrašų versijų / revizijų istorijos nėra (yra tik teisiniams dokumentams – žr. [Teisiniai dokumentai, sutikimai ir slapukai](legal_documents.md)).
- Nenaudojami `CmsMedia` failai automatiškai netrinami.
- `cms/content.py` prekių bloko serializacijos klaida (pvz. trūksta PVM nustatymo) nesugriauna puslapio – blokas praleidžiamas, klaida loguojama.
