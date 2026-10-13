# Ops Admin panelė (MVP) - specifikacija

## Paskirtis

Šis dokumentas apibrėžia atskirą kasdienio darbo panelę vadybininkams (Order/Shipping/Payment/Inventory/Catalog Ops), paliekant Django admin techniniam administravimui.

Tikslas: intuityvus, action-first workflow be tiesioginio darbo su žemų lygių modeliais.

Įgyvendinto OPS API sluoksnio kontraktas dokumentuotas faile `docs/ops_admin_api.md`.

## 1) MVP ekranai ir darbo srautai

## 1.1 Navigation ir IA (information architecture)

- `Inbox` (užsakymų eilė)
- `Shipping Desk` (siuntos, lipdukai, tracking)
- `Payments Desk` (mokėjimų statusai, nesutapimai)
- `Inventory Desk` (trūkumai, rezervacijos, papildymo signalai)
- `Catalog Ops` (greiti pakeitimai: aktyvumas/kaina)
- `Audit & Jobs` (masinių veiksmų ir async užduočių istorija)

## 1.2 Inbox (užsakymai)

Paskirtis: viena vieta naujiems ir procesiniams užsakymams.

Privalomi filtrai:

- status (`new`, `awaiting_payment`, `paid`, `picking`, `packed`, `shipped`, `issue`)
- mokėjimo būsena
- pristatymo tipas (`courier`, `pickup`. )
- šalis/shop
- data intervalas
- prekė (pagal `sku`, produkto pavadinimą arba produkto kodą)
- tiekėjas (pagal `warehouse.code` / tiekėjo kodą)
- gamintojas (pagal `brand` / gamintojo pavadinimą)

Kritinis scenarijus (būtina):
- kai nuotolinis sandėlis vėluoja ir susikaupia daug užsakymų, operatorius privalo rasti visus užsakymus:
  - su konkrečia preke,
  - su konkrečiu tiekėju,
  - su konkrečiu gamintoju,
  - ir jų kombinacijomis (AND logika).

Greiti veiksmai:

- `Patvirtinti užsakymą`
- `Perkelti į komplektavimą`
- `Atidėti su priežastimi`
- bulk veiksmai visam filtrui

## 1.3 Shipping Desk

Paskirtis: siuntos paruošimas ir lipdukų valdymas.

Pagrindiniai veiksmai:

- generuoti lipduką vienam užsakymui
- generuoti lipdukus bulk režimu
- atspausdinti / atsisiųsti PDF
- pažymėti „išsiųsta“
- sinchronizuoti tracking statusą

Klaidų tvarkymas:

- aiškūs business klaidų kodai (`pickup_required`, `carrier_not_available`, `label_generation_failed`)
- `retry` mygtukas nepavykusiems lipdukams

## 1.4 Payments Desk

Paskirtis: mokėjimų kontrolė ir išimtys.

Pagrindiniai scenarijai:

- laukia apmokėjimo
- apmokėta, bet neužfiksuota užsakyme
- nepilnas apmokėjimas
- grąžinimo iniciavimas / stebėjimas

Greiti veiksmai:

- `Suderinti mokėjimą`
- `Pažymėti kaip apmokėtą` (su auditu)
- `Pradėti refund`

## 1.5 Inventory Desk

Paskirtis: operacinis prekių prieinamumo valdymas.

MVP funkcijos:

- „trūksta likučio“ sąrašas
- rezervacijų stebėjimas
- greitas sandėlio kiekio koregavimas (su priežastimi)
- low-stock perspėjimų sąrašas

## 1.6 Catalog Ops

Paskirtis: greiti kasdieniai katalogo pakeitimai be pilno produkto redagavimo.

MVP:

- aktyvuoti/deaktyvuoti produktą
- atnaujinti bazinę kainą
- pakeisti matomumą (`normal` / `outlet`)
- masinis veiksmų taikymas

---

## 2) API kontraktai (OPS sluoksnis)

Visi endpointai:

- prefiksas: `/api/v1/ops/...`
- auth: HttpOnly cookie sesija
- reikalauja role-based teisių
- shop kontekstas pagal domeną + role scope

## 2.1 Užsakymai

- `GET /api/v1/ops/orders`
  - paskirtis: inbox sąrašas su filtrais, rūšiavimu ir puslapiavimu
  - query:
    - baziniai: `status`, `payment_status`, `shipping_type`, `assignee`, `date_from`, `date_to`, `q`, `page`, `page_size`
    - kritiniai paieškai pagal prekių tiekimą:
      - `product_q` (`sku` / produkto pavadinimas / produkto kodas)
      - `supplier_code` (pvz. `warehouse.code`)
      - `manufacturer_q` (brand kodas/pavadinimas)
      - `remote_only` (`true/false`) - rodyti tik užsakymus, kurių eilutės tenkinamos per nuotolinį sandėlį
      - `eta_from`, `eta_to` - preliminarios atvykimo datos filtrui
- `GET /api/v1/ops/orders/{order_id}`
  - paskirtis: detalė + timeline + galimi veiksmai
- `POST /api/v1/ops/orders/{order_id}/actions/confirm`
- `POST /api/v1/ops/orders/{order_id}/actions/assign`
- `POST /api/v1/ops/orders/{order_id}/actions/move-to-picking`
- `POST /api/v1/ops/orders/actions/bulk`

Bulk action request (pavyzdys):

```json
{
  "filter_snapshot": {
    "status": "paid",
    "shipping_type": "pickup"
  },
  "action": "move_to_picking",
  "selected_ids": ["ord_1", "ord_2"]
}
```

Kritinės paieškos request pavyzdys:

```http
GET /api/v1/ops/orders?status=paid&product_q=ABC-123&supplier_code=remote_wh_1&manufacturer_q=bosch&remote_only=true&page=1&page_size=50
```

Kritinės paieškos response laukai (minimaliai):

- `matched_items_count` - kiek eilučių užsakyme atitiko filtrą
- `matched_suppliers` - kurie tiekėjai/sandėliai atitiko
- `earliest_eta` - artimiausia prognozuojama atvykimo data
- `latest_eta` - vėliausia prognozuojama atvykimo data

## 2.2 Siuntos ir lipdukai

- `GET /api/v1/ops/shipments`
- `POST /api/v1/ops/shipments/{order_id}/label`
- `POST /api/v1/ops/shipments/labels/bulk`
- `GET /api/v1/ops/shipments/labels/{job_id}`
- `POST /api/v1/ops/shipments/{shipment_id}/mark-shipped`
- `POST /api/v1/ops/shipments/{shipment_id}/refresh-tracking`

Label generation response (pavyzdys):

```json
{
  "job_id": "job_123",
  "status": "queued",
  "estimated_seconds": 8
}
```

## 2.3 Mokėjimai

- `GET /api/v1/ops/payments`
- `GET /api/v1/ops/payments/{payment_id}`
- `POST /api/v1/ops/payments/{payment_id}/reconcile`
- `POST /api/v1/ops/payments/{payment_id}/mark-paid`
- `POST /api/v1/ops/payments/{payment_id}/refund`

## 2.4 Sandėlis

- `GET /api/v1/ops/inventory/alerts`
- `GET /api/v1/ops/inventory/items`
- `POST /api/v1/ops/inventory/items/{item_id}/adjust`

Adjustment request (pavyzdys):

```json
{
  "delta": -2,
  "reason_code": "damage",
  "comment": "Pakuotė pažeista"
}
```

## 2.5 Catalog ops

- `GET /api/v1/ops/catalog/products`
- `POST /api/v1/ops/catalog/products/{product_id}/toggle-active`
- `POST /api/v1/ops/catalog/variants/{variant_id}/update-price`
- `POST /api/v1/ops/catalog/offers/{inventory_item_id}/set-visibility`
- `POST /api/v1/ops/catalog/bulk-actions`

## 2.6 Audit ir job'ai

- `GET /api/v1/ops/audit/events`
- `GET /api/v1/ops/jobs`
- `GET /api/v1/ops/jobs/{job_id}`
- `POST /api/v1/ops/jobs/{job_id}/retry`

---

## 3) Role ir permission matrica

| Sritis / veiksmas                   | Operatorius | Finansai | Sandėlis | Vadovas | Superadmin |
| ----------------------------------- | ----------- | -------- | -------- | ------- | ---------- |
| Peržiūrėti užsakymų inbox           | ✅          | ✅       | ✅       | ✅      | ✅         |
| Keisti užsakymo statusą (operacinį) | ✅          | ⚪       | ✅       | ✅      | ✅         |
| Generuoti siuntų lipdukus           | ✅          | ⚪       | ✅       | ✅      | ✅         |
| Žymėti „išsiųsta“                   | ✅          | ⚪       | ✅       | ✅      | ✅         |
| Peržiūrėti mokėjimų neatitikimus    | ⚪          | ✅       | ⚪       | ✅      | ✅         |
| Žymėti mokėjimą „paid“ ranka        | ⚪          | ✅       | ⚪       | ✅      | ✅         |
| Inicijuoti refund                   | ⚪          | ✅       | ⚪       | ✅      | ✅         |
| Koreguoti sandėlio likučius         | ⚪          | ⚪       | ✅       | ✅      | ✅         |
| Keisti produkto aktyvumą/kainą      | ⚪          | ⚪       | ✅       | ✅      | ✅         |
| Matyti audit trail ir job klaidas   | ✅          | ✅       | ✅       | ✅      | ✅         |
| Keisti globalią konfigūraciją       | ⚪          | ⚪       | ⚪       | ⚪      | ✅         |

Legenda:

- `✅` leidžiama
- `⚪` draudžiama (arba tik read-only, jei aiškiai įjungta atskirai)

## Permission modelio rekomendacija

- Bazė: RBAC (`role` -> permission set)
- Papildomai: shop scope (naudotojas gali dirbti tik su priskirtais shop)
- Visi write veiksmai privalo rašyti audit event:
  - `who`
  - `when`
  - `shop`
  - `entity`
  - `before/after` santrauka
  - `reason` (jei privaloma pagal veiksmą)

---

## 4) Backlog ir sprint planas

## Sprint 1 (MVP pagrindas, 2 savaitės)

Tikslas: paleisti kasdienį užsakymų + siuntų darbą.

Užduotys:

1. OPS auth guard + role check middleware
2. `GET /ops/orders`, `GET /ops/orders/{id}`
3. kritinė paieška Inbox'e: `product_q` + `supplier_code` + `manufacturer_q` + `remote_only`
4. order action endpointai (`confirm`, `assign`, `move-to-picking`)
5. `POST /ops/shipments/{order_id}/label`
6. `POST /ops/shipments/{shipment_id}/mark-shipped`
7. Frontend: Inbox + Shipping Desk baziniai ekranai
8. Audit event modelis ir logavimas

Priėmimo kriterijai:

- operatorius be Django admin pilnai apdoroja naują užsakymą iki „shipped“
- 95% veiksmų atliekami <= 3 click
- operatorius randa užsakymų partiją pagal prekę+tiekėją+gamintoją <= 5 s su 10k+ aktyvių užsakymų

## Sprint 2 (Mokėjimai + bulk, 2 savaitės)

Tikslas: sumažinti rankinį darbą ir klaidas.

Užduotys:

1. Payments Desk sąrašas + detalė
2. `reconcile`, `mark-paid`, `refund` endpointai
3. Bulk label generation
4. Bulk order actions
5. Job status UI (`queued/running/failed/success`)

Priėmimo kriterijai:

- bulk lipdukų generavimas 100 užsakymų partijai
- aiškūs klaidų kodai ir retry workflow

## Sprint 3 (Inventory + Catalog Ops + stabilizacija, 2 savaitės)

Tikslas: pilnas kasdienės operacijos ciklas.

Užduotys:

1. Inventory alerts + adjust endpointai
2. Catalog Ops quick actions (active/price/visibility)
3. Saved filters, shortcut'ai, table performance tuning
4. KPI mini-dashboard (SLA, pending, failed jobs)
5. E2E testai kritiniams srautams

Priėmimo kriterijai:

- vadybininkai nenaudoja Django admin kasdienėms užduotims
- kritiniai srautai padengti e2e testais

---

## 5) UX principai (privalomi)

1. Action-first ekranai (ne model-first)
2. Vienodi status badge ir spalvų kodai per visą panelę
3. Kiekvienas write veiksmas turi aiškų rezultatą + klaidos tekstą
4. Bulk-first (filtras + pažymėti + vykdyti)
5. Pagrindiniai klaviatūros shortcut'ai (`J/K`, `Enter`, `Ctrl+P`)
6. „Undo“ langas nerizikingiems veiksmams (kur įmanoma)
7. Lokalės/laiko zonos atvaizdavimas pagal shop ir naudotoją

---

## 6) Įgyvendinimo pastabos backend komandai

- Nedubliuoti business logikos fronte; visos taisyklės backend service sluoksnyje.
- Naudoti idempotency ten, kur veiksmas gali būti kartojamas (label, mark-shipped, refund).
- Asinchroninius veiksmus vykdyti per job queue; UI turi matyti progresą.
- Django admin palikti tik techniniams vartotojams ir konfigūracijai.
- Kritinei paieškai pagal prekę/tiekėją/gamintoją pridėti indeksus:
  - order line ryšiams (`order_id`, `variant_id`, `warehouse_id`)
  - produkto paieškai (`sku`, `slug`, `name` trigram/full-text pagal DB strategiją)
  - gamintojui (`brand_id` + paieškos indeksas brand pavadinimui)
  - sandėliui/tiekėjui (`warehouse.code`)
- Jei naudojamas PostgreSQL:
  - naudoti `GIN`/`pg_trgm` tekstinei paieškai,
  - filtruoti per subquery/CTE pagal order line, o ne per N+1 joins frontend'e.
