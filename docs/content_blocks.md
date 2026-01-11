# Content Blocks (papildoma informacija prekei / checkout)

## Tikslas

Reikia galimybės pridėti papildomą informaciją:

- globaliai (pvz. grąžinimo informacija)
- pagal taisykles (pvz. brand X + kategorijos A/B → konkreti dydžių lentelė)

Informacija turi būti valdoma paprastai (admin’e), greita (cache), ir lengvai išplečiama.

## Pasaulinė praktika (santrauka)

- Turinio blokai laikomi kaip „CMS content“ (DB)
- Priskyrimas vyksta per taisykles (targeting rules) su prioritetais
- API grąžina struktūrizuotą sąrašą, UI pats nusprendžia kaip renderinti

## Siūlomas duomenų modelis

### 1) `ContentBlock`

Ši dalis aprašo **realų DB modelį** (kaip yra įgyvendinta `catalog.models`).

- `key` (slug, unique) — stabilus identifikatorius FE’ui
- `type` (choice):
  - `rich_text`
  - `table`
  - `image`
  - `callout`
  - `accordion`
  - `attachments`
- `placement` (choice):
  - `product_detail`
  - `cart`
  - `checkout`
  - `order_confirmation`
  - `global`
- `is_active` (bool)
- `priority` (int, default 0)
- `valid_from`, `valid_to` (optional)

Pastaba: turinys (title/payload/markdown) laikomas per `ContentBlockTranslation`.

### 1a) `ContentBlockTranslation`

- `content_block` (FK)
- `language_code` (pvz. `lt`, `en`)
- `title` (string)
- `payload` (JSON)
- `markdown` (text) — admin patogumui; kai `ContentBlock.type=rich_text`, `payload` automatiškai sinchronizuojamas į `{ "markdown": <markdown> }`

### 2) `ContentRule`

Ryšys, kuris nusprendžia kada blokas rodomas.

- `content_block` (FK)
- `is_active`
- `priority` (int) — taisyklės prioritetas
- `is_exclusive` (bool)
- Targeting laukai (visi optional, bet bent vienas turi būti):
  - `channel` (string, optional; realiai tikimasi `normal|outlet`)
  - `brand` (FK)
  - `category` (FK) + `include_descendants` (bool)
  - `product_group` (FK)
  - `product` (FK)
- `valid_from`, `valid_to` (optional)

Pastaba: `ContentRule` neturi savo `placement` — placement ateina iš `ContentBlock.placement`.

## Atrankos algoritmas

Input: `product_id`, `category_id`, `brand_id`, `product_group_id`, `channel`, `placement`, `now`

1) Paimti visus aktyvius `ContentBlock` pagal `placement` ir `valid_from/to`
2) Paimti `ContentRule` ir pritaikyti filtrus:
   - match `channel` (jei nustatytas)
   - match `brand/category/group/product` (atsižvelgiant į descendants)
3) Sudėti rezultatus:
   - globalūs block’ai (`placement=global`) visada įtraukiami
   - tada matching rules pagal `priority`
4) Sort:
   - `rule.priority` desc
   - `block.priority` desc
5) Jei `is_exclusive=true`, nutraukti (tik tos taisyklės blokai)

Realus įgyvendinimas (dabartinis kodas):

- `global` blokai (`placement=global`) įtraukiami visada (pirma, pagal `block.priority`)
- tada pridedami matching taisyklių blokai (pagal `rule.priority` ir `block.priority`)
- deduplikacija pagal `block.id` (tas pats blokas grąžinamas 1 kartą)
- `is_exclusive`: jei aukščiausio prioriteto suveikusi taisyklė turi `is_exclusive=true`, paliekamos tik taisyklės su tuo pačiu top priority (kad galima būtų turėti kelis blokus tam pačiam exclusive lygiui)

## API kontraktas (realiai įgyvendintas)

Šioje vietoje pateikiamas FE kontraktas pagal esamą įgyvendinimą (backend grąžina `type` + `payload`, o FE pats renderina).

### Product detail

`GET /api/v1/catalog/products/{slug}`

Query:

- `country_code` (default `LT`)
- `channel` (default `normal`, allowed: `normal|outlet`)
- `language_code` (optional, pvz. `lt`, `en`) — content block vertimui parinkti

Pridėti:

```json
{
  "content_blocks": [
    {
      "key": "returns_standard",
      "title": "Grąžinimas",
      "placement": "product_detail",
      "type": "rich_text",
      "payload": {"markdown": "..."}
    },
    {
      "key": "size_chart_a",
      "title": "Dydžių lentelė",
      "placement": "product_detail",
      "type": "table",
      "payload": {
        "table": {
          "caption": "Dydžių lentelė",
          "columns": [{"key": "size", "label": "Dydis"}],
          "rows": [{"size": "S"}],
          "notes_markdown": ""
        }
      }
    }
  ]
}
```

Pastabos FE'ui:

- `content_blocks[]` yra jau surikiuoti taip, kaip reikia atvaizdavimui.
- FE neturi bandyti perskaičiuoti taisyklių; FE tiesiog renderina.

### FE komponentų parinkimas (mapping)

FE turi renderinti pagal `type`:

- `rich_text` → `RichTextBlock` (renderina `payload.markdown`)
- `table` → `TableBlock` (renderina `payload.table`)
- `image` → `ImageBlock`
- `callout` → `CalloutBlock`
- `accordion` → `AccordionBlock`
- `attachments` → `AttachmentsBlock`

Rekomendacija FE architektūrai:

- turėti vieną `ContentBlockRenderer` (switch pagal `type`)
- nežinomą `type` ignoruoti (arba log’inti į Sentry), bet nelaikyti hard-error

### Payload kontraktai (stabilūs)

Žemiau nurodyti payload formatai yra rekomenduojamas stabilus kontraktas. `rich_text` ir `table` jau naudojami; kiti tipai pridėti, kad FE iškart žinotų struktūrą.

#### `type=rich_text`

```json
{ "markdown": "..." }
```

#### `type=table`

```json
{
  "table": {
    "caption": "Dydžių lentelė",
    "columns": [
      {"key": "size", "label": "Dydis"},
      {"key": "chest", "label": "Krūtinė (cm)"}
    ],
    "rows": [
      {"size": "S", "chest": "86–90"}
    ],
    "notes_markdown": "Matavimai yra orientaciniai."
  }
}
```

FE pastabos:

- `rows[]` raktai turi atitikti `columns[].key`
- UI rekomendacija: horizontal scroll mobiliajame

#### `type=image`

```json
{
  "src": "https://...",
  "alt": "...",
  "href": "https://...",
  "caption": "...",
  "width": 800,
  "height": 600
}
```

FE pastabos:

- `href` optional (jei pateikta — paveikslėlis kaip link)
- `width/height` optional (jei yra — geresnis CLS)

#### `type=callout`

```json
{
  "variant": "info",
  "title": "Svarbu",
  "markdown": "..."
}
```

Kur `variant` ∈ `info|warning|success|error`.

#### `type=accordion`

```json
{
  "items": [
    {"id": "q1", "title": "Klausimas", "markdown": "Atsakymas"}
  ]
}
```

#### `type=attachments`

```json
{
  "items": [
    {
      "title": "Instrukcija",
      "url": "https://.../file.pdf",
      "mime": "application/pdf",
      "size_bytes": 123456
    }
  ]
}
```

### FE tipai (TypeScript)

Žemiau pateikiami tipai yra 1:1 pagal dabartinį API `content_blocks[]` kontraktą.

```ts
export type ContentPlacement =
  | 'product_detail'
  | 'cart'
  | 'checkout'
  | 'order_confirmation'
  | 'global';

export type ContentBlockType =
  | 'rich_text'
  | 'table'
  | 'image'
  | 'callout'
  | 'accordion'
  | 'attachments';

export type RichTextPayload = {
  markdown: string;
};

export type TableColumn = {
  key: string;
  label: string;
};

export type TableRow = Record<string, string>;

export type TablePayload = {
  table: {
    caption?: string;
    columns: TableColumn[];
    rows: TableRow[];
    notes_markdown?: string;
  };
};

export type ImagePayload = {
  src: string;
  alt?: string;
  href?: string;
  caption?: string;
  width?: number;
  height?: number;
};

export type CalloutVariant = 'info' | 'warning' | 'success' | 'error';

export type CalloutPayload = {
  variant: CalloutVariant;
  title?: string;
  markdown: string;
};

export type AccordionPayload = {
  items: Array<{ id: string; title: string; markdown: string }>;
};

export type AttachmentsPayload = {
  items: Array<{ title: string; url: string; mime?: string; size_bytes?: number }>;
};

export type UnknownPayload = Record<string, unknown>;

export type ContentBlockOut = {
  key: string;
  title: string;
  placement: ContentPlacement;
  type: ContentBlockType;
  payload:
    | RichTextPayload
    | TablePayload
    | ImagePayload
    | CalloutPayload
    | AccordionPayload
    | AttachmentsPayload
    | UnknownPayload;
};

export type ProductDetailOutWithContentBlocks = {
  // ...kiti product detail laukai
  content_blocks: ContentBlockOut[];
};

// Helper type guards
export function isRichTextBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: RichTextPayload } {
  return b.type === 'rich_text' && typeof (b.payload as any)?.markdown === 'string';
}

export function isTableBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: TablePayload } {
  return (
    b.type === 'table' &&
    typeof (b.payload as any)?.table === 'object' &&
    Array.isArray((b.payload as any)?.table?.columns) &&
    Array.isArray((b.payload as any)?.table?.rows)
  );
}

export function isImageBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: ImagePayload } {
  return b.type === 'image' && typeof (b.payload as any)?.src === 'string';
}

export function isCalloutBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: CalloutPayload } {
  return b.type === 'callout' && typeof (b.payload as any)?.markdown === 'string';
}

export function isAccordionBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: AccordionPayload } {
  return b.type === 'accordion' && Array.isArray((b.payload as any)?.items);
}

export function isAttachmentsBlock(b: ContentBlockOut): b is ContentBlockOut & { payload: AttachmentsPayload } {
  return b.type === 'attachments' && Array.isArray((b.payload as any)?.items);
}
```

### Cart / Checkout

Ši dalis yra planuojama (dar neįgyvendinta visuose endpointuose):

- global blocks (`placement=cart`) — vieną kartą
- optional: per line item blocks, jei reikia (rečiau)

```json
{
  "content_blocks": [
    {"key": "returns_standard", "placement": "cart", "type": "rich_text", "payload": {"markdown": "..."}}
  ]
}
```

### Order confirmation

Order confirmation turėtų būti snapshot (pagal užsakymo momentą), jei norim kad tekstai nesikeistų.

Minimalus variantas:

- order response grąžina tik `content_block_keys`
- frontend renderina pagal cache’intą `GET /content-blocks` lookup

Patikimesnis variantas:

- order išsisaugo snapshot `title/body` (kad nekistų istoriškai)

## Admin UX

- `Content blocks` sąrašas
- `Content rules` inline arba atskiras admin
- Peržiūros testas (optional): „simulate“ su `product_id + channel + placement`

## Performance / caching

- `ContentBlock` ir `ContentRule` galima cache’inti (pvz. 60s–5min)
- Matching daryti be brangių join’ų:
  - iš anksto susirinkti product kontekstą (brand/category/group)
  - tada filtruoti rules su paprastais `WHERE` ir `OR`

## Atviros vietos / sprendimai

- Ar `content_blocks` turi būti grąžinami su `body_html` ar tik `body_markdown`? Rekomendacija: grąžinti vieną (`body`) ir leisti frontui renderinti markdown.
- Ar reikalingi skirtingi blokai pagal locale? Jei taip, pridėti `language_code`.

## Block tipai (recommended)

Kad būtų galima turėti ne tik tekstą, bet ir „prijungiamus“ blokus (lentelės, FAQ, paveikslėliai, callout'ai), rekomenduojama papildyti `ContentBlock` su:

- `type` (choice)
- `payload` (JSON)

### Siūlomi `type`

- `rich_text` — `payload.markdown`
- `table` — struktūrinė lentelė (dydžių lentelėms)
- `image` — paveikslėlis su alt ir (optional) link
- `callout` — „info/warn/success“ blokas su trumpu tekstu
- `accordion` — FAQ / sekcijos (list)
- `attachments` — failai (pvz. PDF instrukcijos)

Pastaba: jei norite MVP greičiau, galite pradėti tik nuo `rich_text` ir `table`, o kitus pridėti vėliau.

## Lentelės (size chart) – 2 variantai

Frontas renderina markdown, todėl dydžių lentelėms yra du realūs sprendimai.

### Variantas A: Markdown lentelė (`type=rich_text`)

Bloke laikote paprastą markdown su lentele.

Pliusai:

- greita pradžia
- admin’e paprasta redaguoti

Minusai:

- sunku validuoti (ar tikrai teisingas stulpelių skaičius)
- sunku padaryti sudėtingesnį UI (sticky header, scroll, responsive)
- vertimams (i18n) lentelė tampa „dideliu tekstu“

Rekomendacija: tinka, jei lentelės paprastos ir jų nedaug.

### Variantas B: Struktūrinė lentelė (`type=table`)

Bloke laikote lentelės struktūrą JSON formatu, o frontas renderina per savo komponentą.

Pliusai:

- lengva validuoti ir garantuoti struktūrą
- lengva padaryti gerą UX (responsive, scroll, highlight)
- galima semantiškai išversti (header labels vs row labels)

Minusai:

- admin’e reikia patogesnio UI (inline lentelės editor) arba pradinio „raw JSON“ (MVP)

Siūlomas `payload` formatas:

```json
{
  "table": {
    "caption": "Dydžių lentelė",
    "columns": [
      {"key": "size", "label": "Dydis"},
      {"key": "chest", "label": "Krūtinė (cm)"},
      {"key": "waist", "label": "Liemuo (cm)"}
    ],
    "rows": [
      {"size": "S", "chest": "86–90", "waist": "70–74"},
      {"size": "M", "chest": "90–94", "waist": "74–78"}
    ],
    "notes_markdown": "Matavimai yra orientaciniai."
  }
}
```

FE rekomendacija:

- renderinti lentelę su horizontal scroll mobiliajame
- palaikyti `caption` ir `notes_markdown`

## Size chart pernaudojimas (brand + kategorijos)

Dažniausias realus scenarijus: viena dydžių lentelė galioja visam brand'ui ir konkrečioms kategorijoms (pvz. `Adidas` + kategorijos `A` ir `B`).

Rekomendacija:

- Kurti **vieną** `ContentBlock` (pvz. `key=size_chart_adidas_shoes`, `type=table`, `placement=product_detail`)
- Priskirti jį per **kelias** `ContentRule` taisykles (arba vieną taisyklę su category descendants), pvz.:
  - `brand=Adidas` + `category=Shoes` (`include_descendants=true`)
  - `brand=Adidas` + `category=Kids-shoes` (`include_descendants=true`)

Taip admin'e lentelė redaguojama vienoje vietoje, o pritaikymas valdosi taisyklėmis.

Papildoma rekomendacija:

- `ContentBlock.key` laikyti stabilų ir semantišką (brand + kategorija), nes frontas dažnai nori analitikos / QA patikrinimų pagal key.

## Admin UX (rekomendacijos dydžių lentelėms)

Kad lenteles būtų lengva kurti neturint programavimo žinių, rekomenduojama admin'e turėti „spreadsheet“ tipo redaktorių (panašiai kaip Excel/Google Sheets):

- Copy/paste (CSV/TSV) į grid'ą
- Pridėti / trinti eilutes ir stulpelius
- Preview (kaip atrodys product detail'e)
- `Duplicate` (kopijuoti lentelę naujam brand'ui/kategorijai)
- Templates (pvz. „Batų dydžiai“, „Drabužių dydžiai“) — optional, bet labai paspartina darbą

## Kelių kalbų (i18n) rekomendacija

Gali reikėti kelių kalbų, todėl rekomenduoju iš karto pasirinkti vieną iš šių modelių.

### Variant A (rekomenduojamas): translations per bloką

`ContentBlock` lieka „nepriklausomas nuo kalbos“ (turi tik `key`, `placement`, `type`, `priority`, `is_active`).
Turinys laikomas atskiroje lentelėje:

- `ContentBlockTranslation`:
  - `content_block` (FK)
  - `language_code` (pvz. `lt`, `en`)
  - `title`
  - `payload` (arba `markdown`)

Pliusai:

- viena taisyklių sistema visoms kalboms
- `key` stabilus, nedubliuojamas per kalbas

API elgsena:

- endpointas priima `language_code` (arba naudoja request locale)
- jei vertimo nėra, fallback pagal grandinę (žr. žemiau)

### i18n fallback (dabartinis įgyvendinimas)

Backend bando rasti vertimą pagal šią tvarką:

1) `language_code` (jei paduotas)
2) `settings.LANGUAGE_CODE` (tik bazinis, pvz. `en` iš `en-us`)
3) `lt`
4) `en`

Jei vertimo nėra nė viena kalba, blokas grąžinamas su tuščiu `title` ir tuščiu `payload`.

FE rekomendacija:

- jei `payload` tuščias, bloką galima nerodyti (arba rodyti tik jei FE turi fallback content pagal `key`).

### Variant B (paprastesnis MVP): `ContentBlock` turi `language_code`

`ContentBlock` turi `language_code`, o `key` yra unique per (`key`, `language_code`).

Pliusai:

- paprasčiau DB schema

Minusai:

- taisyklės ir blokai dubliuojasi per kalbas
- didesnė rizika „išsiskyrusioms“ taisyklėms

## API kontraktas (patikslintas)

Kad FE galėtų renderinti skirtingų tipų blokus, rekomendacija grąžinti:

- `key`
- `title`
- `placement`
- `type`
- `payload`

Pvz.

```json
{
  "content_blocks": [
    {
      "key": "returns_standard",
      "title": "Grąžinimas",
      "placement": "product_detail",
      "type": "rich_text",
      "payload": {"markdown": "..."}
    },
    {
      "key": "size_chart_a",
      "title": "Dydžių lentelė",
      "placement": "product_detail",
      "type": "table",
      "payload": {"table": {"columns": [], "rows": []}}
    }
  ]
}
```
