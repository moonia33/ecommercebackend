# Home page (homebuilder) – API kontraktas

Šiame projekte home puslapis yra konfigūruojamas per atskirą `homebuilder` app’ą (ne CMS pages) ir pateikiamas kaip sekcijų (`sections[]`) sąrašas. Frontend’as renderina sekcijas pagal `type` ir `payload`, o backend’as „resolve’ina“ dinamiškus duomenis (pvz. produktų grid’ą).

## Endpoint (realiai įgyvendintas)

`GET /api/v1/home`

Query:

- `country_code` (default `LT`)
- `channel` (default `normal`, allowed: `normal|outlet`)
- `language_code` (optional, pvz. `lt`, `en`)

## Response (aukšto lygio)

```json
{
  "code": "home",
  "title": "",
  "seo_title": "",
  "seo_description": "",
  "updated_at": "2026-01-11T10:00:00Z",
  "sections": [
    {
      "type": "hero",
      "payload": {
        "title": "",
        "slides": [
          {
            "image": {"src": "https://.../hero.jpg", "alt": ""},
            "title": "New in",
            "subtitle": "",
            "cta": {"label": "Shop now", "url": "/catalog/new"}
          }
        ]
      }
    },
    {
      "type": "product_grid",
      "payload": {
        "id": "bestsellers_main",
        "title": "Best sellers",
        "limit": 12,
        "stock_policy": "in_stock_first",
        "pinned": {"product_slugs": ["a", "b"], "position": "start"},
        "source": {
          "kind": "listing",
          "category_slug": "women",
          "brand_slug": null,
          "group_code": null,
          "q": null,
          "feature": null,
          "option": null,
          "sort": "best_selling",
          "in_stock_only": true
        }
      },
      "items": [
        {
          "id": 1,
          "sku": "SKU",
          "slug": "product",
          "name": "Product",
          "is_active": true,
          "brand": {"id": 1, "slug": "brand", "name": "Brand"},
          "category": {"id": 2, "slug": "cat", "name": "Category"},
          "images": [{"url": "https://...", "alt_text": "", "sort_order": 0, "avif_url": null, "webp_url": null}],
          "price": {"currency": "EUR", "net": "10.00", "vat_rate": "0.21", "vat": "2.10", "gross": "12.10"},
          "compare_at_price": null,
          "discount_percent": null
        }
      ]
    }
  ]
}
```

## Sekcijų tipai

### `type=hero`

Slideris (1..N skaidrių). Frontend’as renderina hero.

`payload`:

- `slides[]`:
  - `image.src`, `image.alt`
  - `title`, `subtitle`
  - `cta.label`, `cta.url`

### `type=product_grid`

Dinaminis produktų blokas su:

- rankiniu pasirinkimu (`pinned`)
- taisyklėmis kaip listing’e (`source.kind=listing`)
- `limit`
- `stock_policy`:
  - `in_stock_first` (default) – out-of-stock leidžiami, bet stumiami į apačią
  - `hide_oos` – rodomi tik turintys stock

Pinned out-of-stock šiame projekte yra slepiami (t.y. jei pinned produktas neturi stock – jis nepatenka į output).

`payload.source` yra analogiškas `/api/v1/catalog/products` filtrams:

- `category_slug`
- `brand_slug`
- `group_code`
- `q`
- `feature` (formatas kaip listinge: `code:value,code:value`)
- `option` (formatas kaip listinge: `type:value,type:value`)
- `sort`: `best_selling`, `created_at`, `price`, `discounted`, ...
- `in_stock_only`: `true|false`

### `type=category_grid`

Kategorijų blokas. Gali būti rankinis arba taisyklinis.

`payload`:

- `title`
- `limit`
- `source`:
  - `kind=manual`: `category_slugs[]`
  - `kind=rules`: pvz. `root_slug` + `include_descendants=true` (rekomenduojama)

Response’e `items` yra `CategoryOut[]` (minimalus view)

### `type=rich_text`

Statinis tekstas (markdown) – FE renderina per savo markdown rendererį.

### `type=newsletter`

Placeholder sekcija (ateičiai). Backend’as gali grąžinti tik `payload` su tekstais/CTA.

## TypeScript tipai (rekomenduojami)

```ts
export type HomeSectionType =
  | 'hero'
  | 'product_grid'
  | 'category_grid'
  | 'rich_text'
  | 'callout'
  | 'newsletter';

export type HeroSection = {
  type: 'hero';
  payload: {
    slides: Array<{
      image: { src: string; alt?: string };
      title?: string;
      subtitle?: string;
      cta?: { label: string; url: string };
    }>;
  };
};

export type ProductGridStockPolicy = 'in_stock_first' | 'hide_oos';

export type ProductGridSource = {
  kind: 'listing';
  category_slug?: string | null;
  brand_slug?: string | null;
  group_code?: string | null;
  q?: string | null;
  sort?: string | null;
  in_stock_only?: boolean;
};

export type ProductGridSection = {
  type: 'product_grid';
  payload: {
    id: string;
    title?: string;
    limit: number;
    stock_policy?: ProductGridStockPolicy;
    pinned?: { product_slugs: string[]; position?: 'start' | 'end' };
    source: ProductGridSource;
  };
  items: any[]; // ProductListOut (naudokite jau turimą tipą iš catalog)
};

export type HomeSection = HeroSection | ProductGridSection | { type: string; payload: any; [k: string]: any };

export type HomeOut = {
  code: string;
  title: string;
  seo_title: string;
  seo_description: string;
  updated_at: string;
  sections: HomeSection[];
};
```

## Admin workflow (marketingui)

Admin’e yra atskiras meniu **Homebuilder**:

- `Home pages` (sukurkite įrašą su `code=home`)
- `Home sections` (bendras blokų sąrašas su `type` + `sort_order`)
- Pagal sekcijos tipą pildomi atitinkami modeliai:
  - `Hero section` + `Hero slides` + `Hero slide translations`
  - `Product grid section` + `Pinned products`
  - `Category grid section` + `Pinned categories`
  - `Rich text section` + `translations` (markdown)
  - `Newsletter section` + `translations`
