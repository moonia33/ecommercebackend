# Importu vykdymo planas (be vertimu pirmame etape)

Sis dokumentas apraso sutarta igyvendinimo krypti:

- Vertimai bus globalus (ne per-site).
- Category/Brand vertimu nedarome.
- Pirmiausia paleidziame importus taip, kad frontas kuo greiciau turetu prekiu.
- Vertimu sluoksni numatome is anksto, bet jo nejungiame pirmajame etape.

## Tikslai

1. Greitai uzpildyti kataloga prekemis ir likuciais.
2. Islaikyti stabilu importu procesa keliems tiekejams.
3. Kategorijas valdyti per mapinima i jau turima struktura (Prestashop).
4. Palikti aiskias integracijos vietas ateities vertimu sluoksniui.

## Scope (pirmas etapas)

- Supplier importu branduolys (core app + adapteriu kontraktas).
- Kategoriju mapinimas (supplier -> esama `Category`).
- Produktu/variantu upsert logika.
- Likuciu atnaujinimas per sandeliu modelius.
- Importu run auditas ir klaidu atsekamumas.

Neitraukiama i pirma etapa:

- DI vertimai.
- Category/Brand vertimu modeliai arba workflow.
- SEO teksto generavimas is DI.

## Pagrindiniai principai

### 1) Source tekstas laikomas toks, koks ateina

- Jei importas EN kalba, `Product.name/description` laikinai lieka EN.
- Tai leidzia frontui dirbti be laukimo ir neblokuoja MVP.

### 2) Kategoriju mapinimas yra privalomas

- Importas nesukuria nauju kategoriju automatiskai pagal supplier feed.
- Supplier kategorijos siejamos su jau perkelta Prestashop `Category` struktura.

### 3) Importas idempotentiskas

- Pakartotinis paleidimas neturi dubliuoti produktu/variantu.
- Naudojami stabilus isoriniai raktai (`supplier_sku`, `external_id`, pan.).

### 4) Vertimams paliekami "hook'ai"

- Po upsert kvieciamas tuscias/no-op extensibility taskas (pvz. `enqueue_translation_candidates`).
- Veliau, ijungus vertimus, nebereikes perrasyti importu branduolio.

## Siuloma architektura

### `suppliers` core app (naujas)

Rekomenduojami modeliai:

- `Supplier`
  - kodas, pavadinimas, aktyvumas, konfigurojama metadata.
- `SupplierImportRun`
  - paleidimo tipas (`catalog`, `stock`, `full`), statusas, statistika, klaidos.
- `SupplierProductMap`
  - susiejimas tarp tiekejo produkto rakto ir vidinio produkto/varianto.
- `SupplierCategoryMap`
  - susiejimas tarp tiekejo kategorijos rakto/path ir vidines `Category`.

Pastaba: modeliu pavadinimus galima koreguoti, svarbu islaikyti atsakomybes.

### Adapterio kontraktas kiekvienam tiekėjui

Kiekvienas tiekejo pluginas turi realizuoti ta pati kontrakta:

1. `fetch` - paima zalius duomenis (XML/CSV/API/HTML scraping).
2. `normalize` - normalizuoja i bendra vidini DTO formata.
3. `upsert` - iraso/atnaujina `Product`, `Variant`, `InventoryItem`, map'us.

Tai leidzia lengvai prideti naujus importus, net jei failu formatai skiriasi.

## Kategoriju mapinimo workflow

1. Sukuriamas supplier importas.
2. Surenkamos unikalios supplier kategorijos.
3. Per admin arba importuojamu mapping failu atliekamas susiejimas:
   - `supplier_category` -> `catalog.Category`.
4. Tik susietos kategorijos leidziamos i produkcini importa.

Rekomendacija:

- tureti "strict mode" (default), kuris skipina nesumapintas kategorijas ir fiksuoja klaidas.

## Variantu strategija (MVP)

- Jei tiekejas duoda aiskias variantu dimensijas (pvz. size/color):
  - kuriame atskirus `Variant`.
- Jei duomenu nepakanka:
  - 1 produktas = 1 variantas.

Papildomai verta laikyti:

- `external_attributes` JSON (zaliems atributams), kad veliau butu lengva normalizuoti.

## Sandelio/logistikos kryptis

- Naudojame esamus `Warehouse` + `InventoryItem`.
- Pradziai:
  - arba po viena warehouse kiekvienam tiekėjui,
  - arba pagal tavo veiklos logika (local + tiekėjai).
- Likutis atnaujinamas atskiru stock importu run'u.

## Vertimu sluoksnis (ateiciai, bet suplanuotas dabar)

Siame etape vertimu NEJUNGIAME, bet paruosiam integracijos taskus:

- `post_upsert` hook, kuris ateityje:
  - kurs vertimu kandidatus,
  - perduos i DI pipeline,
  - pildys `ProductTranslation` (`lt`, veliau `pl/lv/et`).

Svarbu:

- Vertimai bus globalus.
- Category/Brand vertimu nenumatome.

## Etapai

### Etapas 1 - importo pamatas (prioritetas)

- Sukurti `suppliers` core modelius.
- Sukurti pirma tiekejo adapteri su `fetch/normalize/upsert`.
- Ijungti run auditus, statistiką ir klaidu logika.

### Etapas 2 - kategoriju mapinimas ir stabilizacija

- Ijungti `SupplierCategoryMap` workflow.
- Prideti strict validacijas nesumapintoms kategorijoms.
- Sutvarkyti variantu strategija pagal realu feed.

### Etapas 3 - translation-ready hook'ai

- Idiegti no-op hook po produkto upsert.
- Paruosti DTO, kuris veliau bus naudojamas DI vertimams.
- Dokumentuoti ijungimo taskus ateities vertimu etapui.

## Rizikos ir kaip jas valdyti

- Nenuoseklus supplier feed formatas:
  - adapteriu kontraktas + normalize sluoksnis.
- Kategoriju neatitikimai:
  - strict mapinimas + klaidu ataskaitos.
- Dubliavimas ir nekontroliuojami update:
  - isoriniai raktai + idempotentiski upsert'ai.
- Per daug rankinio darbo:
  - import run ataskaitos, bulk mapinimo priemones.

## Priemimo kriterijai (Definition of Done pirmai fazei)

1. Importas sukuria/atnaujina produktus be manual SQL taisymu.
2. Kategorijos neprasislysta be mapinimo (strict mode).
3. Likučiai atsinaujina atskiru paleidimu.
4. Import run'ai turi aišku statusa, statistika ir klaidu suvestine.
5. Fronto katalogas uzsipildo prekemis be vertimu sluoksnio.
6. Yra aiskus hook taskas, per kuri veliau bus ijungiami vertimai.
