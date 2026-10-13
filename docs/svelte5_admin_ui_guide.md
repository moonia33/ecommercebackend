# Svelte 5 Admin UI instrukcija

## Paskirtis

Admin UI yra skirta operaciniam darbui:

- produktų ir variantų administravimui,
- užsakymų valdymui,
- pristatymo etikečių (lipdukų) generavimui,
- mokėjimų ir pristatymo būsenų stebėsenai.

Šis dokumentas apibrėžia ilgalaikę Svelte 5 panelės struktūrą.

## 1. Moduliai (MVP -> LTS)

### 1.1 Katalogo modulis

- produktų sąrašas su filtrais (brand, category, active, stock state);
- produkto peržiūra ir redagavimas;
- variantų ir kainų valdymas;
- inventoriaus (offer) valdymas per sandėlius.

### 1.2 Užsakymų modulis

- užsakymų sąrašas su filtrais (`status`, `payment_status`, `delivery_status`, `shop`);
- order detail su timeline;
- rankiniai veiksmai:
  - pažymėti apmokėtu (kai leidžiama),
  - atnaujinti pristatymo būseną,
  - generuoti lipduką.

### 1.3 Shipping/Carrier modulis

- pickup point sync status (DPD/Unisend);
- label generation log;
- tracking atnaujinimų istorija.

### 1.4 Konfigūracijos modulis

- shop-level nustatymai (`Shop`, `ShopConfig`, `ShopEmailConfig`);
- payment/shipping policy per shop;
- API raktų ir provider konfigūracijų būsenos indikatoriai.

## 2. API integracijos gairės admin panelei

Kadangi dalis admin funkcijų dar veikia per Django Admin actions, Svelte UI turi būti ruošiamas per API orchestration:

- naudoti `checkout/orders` endpointus read srautui;
- veiksmams (label, status update) naudoti atskirus backend admin API endpointus, kai tik jie išskiriami;
- iki to laiko laikyti hybrid modelį:
  - operatyvinis darbas per Svelte UI,
  - kritiniai fallback veiksmai per Django admin.

## 3. DPD lipdukų spausdinimo workflow

Tikslinis UI scenarijus:

1) operatorius pasirenka orderį arba orderių sąrašą;
2) spaudžia `Generuoti A6 lipduką`;
3) UI kviečia backend veiksmą;
4) backend grąžina PDF failą arba failo URL;
5) UI:
   - atidaro print preview,
   - leidžia parsisiųsti,
   - parodo veiksmų istoriją.

### Būtini duomenys UI lygmenyje

- `order_id`
- `carrier_code`
- `shipping_method`
- pristatymo adresas / pickup duomenys
- siuntėjo profilio validumo būsena (ar sukonfigūruotas provider)

### Klaidos ir UX

- jei trūksta carrier config, rodyti aiškią klaidą su veiksmu "Atidaryti konfigūraciją";
- jei order būsena netinkama lipdukui, blokuoti veiksmą UI lygyje;
- masinei generacijai rodyti progress indikatorius ir dalinių nesėkmių sąrašą.

## 4. Svelte 5 rekomenduojama projekto struktūra

- `src/lib/admin-api/client.ts` - authenticated HTTP client
- `src/lib/admin-api/services/orders.ts` - order API
- `src/lib/admin-api/services/catalog.ts` - catalog API
- `src/lib/admin-api/services/shipping.ts` - shipping/carrier API
- `src/lib/admin-stores/*.svelte.ts` - state valdymas
- `src/routes/admin/*` - UI route komponavimas

## 5. Teisės ir role modelis

Rekomenduojama:

- `admin` - pilnos teisės;
- `operator` - užsakymai, lipdukai, tracking;
- `content_manager` - katalogo/turinio redagavimas;
- `support` - read-only + riboti status update.

UI taisyklė:

- action mygtukai aktyvuojami tik jei role leidžia ir backend grąžina leidimą.

## 6. Auditas ir observability

Kiekvienas kritinis admin veiksmas turi būti loguojamas:

- kas inicijavo,
- kada,
- ką keitė,
- ar pavyko,
- klaidos detalė.

Minimalūs įvykiai:

- `order_label_generated`
- `order_delivery_status_changed`
- `order_payment_status_changed`
- `catalog_item_updated`

## 7. 3 etapų įgyvendinimas admin UI kontekste

### Etapas 1

- order list/detail + single label generation + klaidų apdorojimas;
- read-only produkto peržiūra.

### Etapas 2

- pilnas katalogo redagavimas;
- masinė lipdukų generacija;
- shop-aware filtravimas visose lentelėse.

### Etapas 3

- pilnas operacinis dashboard;
- KPI ir alertai;
- job monitorius importams/callback/sync.

## 8. Release checklist admin komandai

- auth/cookie sesija veikia stabiliai;
- veiksmai turi optimistic/pessimistic loading būsenas;
- visos kritinės klaidos turi user-friendly pranešimus;
- label generation turi retry;
- audit log rodomas bent order detalėje;
- smoke test: 1) atidaryti orderį 2) sugeneruoti label 3) atsispausdinti PDF 4) matyti tracking/būseną.
