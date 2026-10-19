# Inventory Sync v2 (planas — dar neimplementuota)

Tiekėjų likučių, savikainų ir akcijų sinchronizacija iš normalyzerio.
Sprendžia: 50k+ SKU mastą, per-supplier sync dažnumą ir tiekėjų akcijų importą.

## Kontekstas

- Keli tiekėjai gali siūlyti tą patį variantą (`Variant` × `Warehouse` = `InventoryItem`).
- Tiekėjų feed'ai turi `sale_price` lauką (užpildytas = akcija, tuščias = nėra),
  kartais ir `sale_qty` (vienetų skaičius išpardavimui). **Galiojimo datų feed'ai neduoda**
  — feed'as pats yra autoritetingas šaltinis.
- Sync dažnumas skiriasi per tiekėją: vieni kas valandą, kiti kartą per dieną,
  treti kartą per savaitę. Backend'as tvarkaraščių nežino — normalyzeris kviečia
  `/imports/stock` su savo `warehouse_code` ir dažnumu.

## Schema

```python
class StockImportItemIn(Schema):
    code: str                            # variant sku
    qty: int
    cost_net: Decimal | None = None      # None = nekeisti (tiekėjas neatsiuntė)
    sale_price_net: Decimal | None = None  # None = išvalyti offer (akcijos nėra)
    sale_qty: int | None = None          # None = visa qty po akcijos kaina
```

Semantikos skirtumas (sąmoningas):
- `cost_net: None` → **nekeisti** esamo `InventoryItem.cost_eur`
- `sale_price_net: None` → **išvalyti** `offer_price_override_eur` (akcija pasibaigė)

## Modelio pakeitimai

`InventoryItem`:
- `offer_qty` (naujas, nullable int) — vienetų skaičius parduodamas akcijos kaina;
  `NULL` arba `>= qty_on_hand` = visa atsarga po akcijos kaina.
- `offer_valid_from/to` NEREIKIA — feed'as pats valdo galiojimą.

## Elgsena

- `offer_qty > 0` (arba NULL) ir `offer_price_override_eur` set → rodoma akcijos kaina.
- Pardavus: `qty_on_hand` ir `offer_qty` mažėja kartu; `offer_qty = 0` → automatiškai
  grįžtama į list kainą nelaukiant kito feed'o.
- Ribos atvejis (užsakoma daugiau nei `offer_qty`): vienetų neskaldome —
  leidžiama visa užsakymo eilutė akcijos kaina; kitas feed'as patikslina.
  (Sąmoningai paprasta; line-split per sudėtinga.)
- Expired/absent akcija feed'e → offer laukai išvalomi, offer'is toliau
  parduoda list kaina (saugus fallback).

## Našumas (50k+ SKU)

Dabartinis `_apply_stock` yra per-eilutę (~6 užklausos/SKU → ~300k užklausų
per 50k feed'ą). Pakeisti į batch:

1. `Variant.filter(sku__in=visi_kodai)` → dict `sku→variant` (1 užklausa)
2. `InventoryItem.filter(variant_id__in=..., warehouse=wh)` → dict (1 užklausa)
3. Dirty-checking atmintyje → `bulk_update` + `bulk_create` (2 užklausos)
4. `ImportItem` — rašyti tik ERROR eilutes + `ImportRun` counter'ius
   (50k INSERT per sync nereikalingi).
5. Vienas `transaction.atomic()` visam run'ui.

Laukas `bulk_update(fields=[qty_on_hand, cost_eur, offer_price_override_eur, offer_qty, updated_at])`.

## Atviri klausimai

- Ar feed'as visada pilnas per tiekėją? Jei taip — verta `zero_missing`/`full_sync`
  flag'o: SKU, kurių nėra feed'e, to sandėlio `qty_on_hand=0`. Jei feed'ai daliniai —
  palikti kaip dabar (atnaujina tik gautas eilutes).
- `never_discount`/`allow_additional_promotions`/`offer_priority`/`offer_label`
  lieka admin'o kontroliuojami (importas jų neliečia).
