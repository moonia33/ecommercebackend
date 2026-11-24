# Omnibus: kainų istorija ir mažiausia kaina per 30 d.

ES direktyva 2019/2161 (98/6/EB 6a str.): skelbiant nuolaidą, ankstesnė kaina =
mažiausia kaina per 30 d. iki nuolaidos; nuolaidos % skaičiuojamas nuo jos (ESTT C-330/23).

## Duomenys

`catalog.VariantPriceHistory` – galutinės viešos kainos be PVM intervalai
(variantas × parduotuvė × kanalas `normal`/`outlet`):

- eilutė kuriama tik pasikeitus kainai; `valid_to=NULL` – dabartinė kaina;
- `prior_unit_net` – mažiausia kaina per 30 d. iki `valid_from` (fiksuojama kuriant eilutę),
  `NULL` – istorijos nėra;
- kaina = PDP logika: `resolve_shop_variant_price` → geriausias offer'is su likučiu
  (jei likučio nėra nė viename sandėlyje – geriausias iš visų, `money.price_offer`) →
  `apply_promo_to_unit_net`. B2B / klientų grupių kainos neįtraukiamos.

## Kaina sąrašuose

`money.listing_price_nets` (sąrašai, peržiūrėtos, suggest) – `product_offer_nets` per variantus:
varianto kaina = pigiausias offer'is su likučiu → jei nėra, pigiausias iš visų sandėlių →
be offer'io – list kaina. „Nuo“ – iš variantų su likučiu (jei yra), `price_to` – iš visų
variantų (su nuolaida). Rikiavimas pagal kainą (ORM ir Typesense `price_<ch>`) naudoja tą
pačią kainą; `vis_*`/`stock_*`/`disc_*` ir `?collection=sale` lieka tik pagal likutį.
Sąrašai `ShopPriceList` taisyklių netaiko (PDP taiko); prod'e kainoraščių nėra.

Celery beat `catalog.record_variant_prices` (kas 30 min.) perskaičiuoja visų aktyvių
variantų kainas (`catalog/services/price_history.py::record_all_prices`) – pagauna ir
pagal laiką įsijungiančias/išsijungiančias akcijas. Admin: „Variant price histories“ (tik skaitymui).

## API

- PDP `variants[].prior_price`, sąrašai / suggest `prior_price` (`MoneyOut | null`).
  Sąrašo prekės `prior_price` = min per aktyvius variantus, kur variantas be istorijos
  atstovaujamas dabartine kaina (konservatyvu).
- `OMNIBUS_ENFORCE=true`: `compare_at_price` = `prior_price`, `discount_percent` nuo jos;
  jei kaina nėra mažesnė už 30 d. mažiausią arba istorijos nėra – nuolaida nerodoma
  (PDP, sąrašai, suggest, peržiūrėtos, krepšelis). `false` (numatyta) – kaip anksčiau (nuo list kainos).

## Įjungimas

1. Deploy → istorija kaupiama nuo pirmo paleidimo.
2. FE rodo „Mažiausia kaina per 30 d.“ iš `prior_price`.
3. Po ~30 d. (arba anksčiau, sąmoningai) `OMNIBUS_ENFORCE=true` Coolify env.
