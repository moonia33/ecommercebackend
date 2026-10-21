# Atributų (options + features) normalizavimas — auditas ir planas

Statusas: **planas, neimplementuota**. Auditas atliktas 2026-10-20 iš realių
normalyzerio (fox/korda/shimano offers, Moss ROZMER dump'as) ir shop DB duomenų.

## 1. Auditas

### Shop DB (po importo)

| Lygmuo | Kiekis | Problema |
|---|---|---|
| `OptionType` | 41 | 12 porų `foo_bar`/`foo-bar` — `_` variantai našlaičiai (0 variantų), likę iš laikų prieš kodų normalizavimą |
| `Feature` | 101 (47 naudojami) | visi `is_filterable=True` → kiekvienas feature yra facetas visose kategorijose |
| `variantas` option | 578 reikšmių / 904 variantai | catch-all „liekana" iš Korda/Shimano adapterių (`Specialist 270 XH S`, `10ft 4lb`) |
| `variantas` feature | 1203 reikšmių / 1584 produktai | ta pati liekana demote'inta į feature, kai nevarijuoja |

### Dublių kilmė — registras nėra privalomas

`apps/catalog/attributes.py::_CANONICAL` yra kanoninis žodynas, bet adapteriai
gali emit'inti **bet kokį** kodą į `attributes["axes"|"features"]`, o eksportas
jį praleidžia nekeisdamas:

| Tiekėjas | Registre nesantys kodai |
|---|---|
| shimano | `length`, `length_imperial`, `section`, `strength`, `strength_lb`, `reel_size`, `rod_type`, `action`, `egi`, `variantas` |
| korda | `variantas` |
| fox | — (0 nemapintų label'ų; adapteris emit'ina kanoninius kodus) |

Pasekmė shop'e:

| Sąvoka | Kodai |
|---|---|
| valo atsparumas | `strength` (kg), `strength-lb`, `line-test-kg`, `line-test-lbs`, `line-rating-lbs` |
| ilgis | `length` (valai: 1000 m; spiningai: 4 m), `rod-length-m`, `rod-length-ft`, `length-imperial`, `line-length`, `unit-length` |
| sekcijos | `section`, `sections` |
| skersmuo | `diameter` (Fox „Ø" — graibšto lankas 25 cm **arba** 5 mm), `line-diameter` (ir ant boilių), `tip-diameter` — **ne sinonimai**, bet `Ø` be konteksto klasifikuojamas klaidingai |

### Reikšmės — string'ai be vienetų

`FeatureValue.value` yra slug'as iš teksto → `0.26`, `0,26 mm`, `0-26` — trys
skirtingos reikšmės. `line-diameter: 0.38` (be vieneto), `casting-weight: 10-40`,
`weight: 227000 g` (mastelio klaida). Range facetų nėra, tik checkbox'ai.

### Moss — vienas `<ROZMER>` visoms prekėms

4804 offer'iai, 2415 skirtingų reikšmių, **290 formų** (skaičiai → `#`,
žodžiai → `W`). Top 30 formų dengia 74 %:

| % | Forma | Pvz. | Reiškia |
|---|---|---|---|
| 13 | `#mm #kg #m` | `0,045mm 1,7kg 135m` | valas: skersmuo / atsparumas / ilgis |
| 9 | `#cm/W` | `10,5 cm/PERCHY` | vobleris: ilgis / spalva |
| 7 | `W` | `Aluminium`, `Citrus` | spalva / skonis / medžiaga |
| 5 | `#g` | `0,1g` | svoris |
| 4 | `#g-#/#` | `10g-1/0` | džigas: svoris / kabliuko dydis |
| 4 | `W. #` | `roz. 1000` | dydis (ritė) |
| 3 | `#cm`, `#x#cm` | `100cm`, `100x100 cm` | matmenys |
| 2 | `#cm/#g/# W` | `170cm/150g/2 składy` | spiningas: ilgis / metimo svoris / sekcijos |
| 1 | `#cm/#W/# W` | `270cm/2,75lbs/2 składy` | karpinė: ilgis / testcurve / sekcijos |
| 1 | `#g/#cm/W./W` | `12,5g/9cm/Susp./MIRROR` | vobleris: svoris / ilgis / tipas / spalva |
| 3 | `S`/`M`/`L`/`XL`, `roz. 41-46` | | dydis (apranga) |

Tas pats token'as reiškia skirtingą atributą pagal kategoriją (`#cm` =
voblerio ilgis arba meškerės ilgis; `#kg` = valo atsparumas arba testcurve).
Korda `variantas` (743: `#lb`, `#m`, `# pcs`, `#mm`, `Barbed #`) ir Shimano
(2300: `#'#''`, `#pc+tips`, `#oz`) — ta pati problema mažesniu mastu.

### Facetai

`product_facets` renka visus `is_filterable` features; nėra per-kategorijos
konfigūracijos. Option-ašis ir feature su tuo pačiu kodu (pvz. `weight` kaip
option 173 reikšmės **ir** kaip feature 596 reikšmės) facetuose nėra viena tapatybė.

## 2. Principai

1. **Vienas žodynas, privalomas eksporto riboje.** Normalyzeris yra vienintelė
   vieta, kur žinoma tiekėjo semantika — ten ir normalizuojama. Shop'as gauna
   tik registro kodus. Kodas ne registre → nepraleidžiamas, o fiksuojamas
   peržiūrai.
2. **Reikšmė = skaičius + vienetas, ne tekstas.** Parsinama normalyzeryje,
   konvertuojama į kanoninį vienetą; tekstas lieka tik display'ui.
3. **Kontekstas = kategorija.** Ir label→kodas, ir composite token→atributas
   sprendžiami per kategorijos profilį, ne globaliai.
4. **Neišparsinta liekana ≠ atributas.** Eina į `variant_label` (varianto
   pavadinimo dalis), niekada į option/feature/facetą. `variantas` kodas išnyksta.
5. **Facetas yra kuruotas per kategoriją**, o ne „visi features".
6. Automatizuojama: parsinimas, vienetai, dublių/nemapintų ataskaita, kandidatų
   siūlymas. **Ne**automatizuojama: galutinis merge sprendimas (`diameter` ≠
   `line-diameter` ≠ `tip-diameter` — pavadinimo panašumas klaidina).

## 3. Planas

### Fazė 1 — registras privalomas (normalyzer)

- `_CANONICAL` įrašas: `(code, kind, labels, datatype, unit)`;
  `datatype ∈ {number, range, enum, text, bool}`; kodai `-` forma.
- Merge/alias lentelė (kodas → kanoninis, su konversija):
  - `strength`, `strength_lb`, `line_test_kg`, `line_test_lbs`, `line-rating-lbs`
    → `line-strength` (kg; lb×0.4536). Display `9 kg / 20 lb`.
  - `length_imperial` → `rod-length-ft`; `section` → `sections`;
    `reel_size` → `reel-size`; `rod_type`, `action`, `egi` → pridėti į registrą.
  - `length`: kontekstinė (žr. žemiau).
  - `variantas` → **ne atributas**; eksportuojama kaip `variant_label`.
- Kontekstinės taisyklės `(code|label, category_match) → canonical`:
  `length`+rods → `rod-length-m`; `length`+lines → `line-length`;
  `Ø`+lines → `line-diameter`; `Ø`+nets → `hoop-diameter`; `Ø` kitur → `diameter`.
- Eksporto vartai `offer_axis_values`/`_features`: kodas ne registre →
  `UnmappedAttribute(supplier, code, label, sample_values, count, category)`
  upsert + praleidžiama. Admin sąrašas = „ką dar reikia sumapinti" ataskaita.
- Testai: kiekvienam alias'ui, kontekstinei taisyklei, vartams.

### Fazė 2 — skaitinės reikšmės ir vienetai (normalyzer + shop)

- `parse_value(text, datatype, unit) → {num, num_max, unit, display}`:
  `0,26 mm`/`0.26`/`0.26mm` → `num=0.26 unit=mm`; `10-40 g` → `num=10 num_max=40`;
  `20 lb` → `9.07 kg`; `6'6"` → `1.98 m`; `3 oz` → `85 g`. Mastelio sanity
  (`weight > 50 kg` prekei → warning į ataskaitą).
- `value` slug'as išvedamas iš kanonizuotos reikšmės (`0.26 mm` → `0-26-mm`),
  todėl visi rašymo variantai sutampa į vieną `FeatureValue`/`OptionValue`.
- Payload: `features[].value_num|value_num_max|unit`, `variants[].options[].*`
  tas pats.
- Shop: `FeatureValue`/`OptionValue` + `value_num`, `value_num_max`, `unit`;
  Typesense `attr_<code>_num` (float) → range facetai; `FeatureValueGroup.auto_assign`
  naudoja `value_num` vietoj parsinimo iš teksto.

### Profiliai (atlikta, prieš F3)

Deklaratyvūs per-kategoriją variantų option profiliai
(`apps/catalog/profiles.py`): `_plan_variants` renkasi option ašis iš
`profile.axes` su all-or-nothing taisykle; neoption varijuojančios ašys
lieka `variants[].features`; `variants[].name` fallback'as — generuotas
"label₁ · label₂" kai `variantas` neteiktas. Auditas:
`variant_axes_audit`.

## Fazė 3 — composite splitter (Moss, Korda/Shimano liekanos)

- Tokenizer: skaldo per `/`, `,`, tarpą-prieš-vienetą; token tipas pagal
  vienetą/formą: `#mm`, `#kg|#lb(s)`, `#m`, `#g`, `#-#g`, `#cm`, `#x#cm`,
  `#/0`|`##`|`roz. #`|`BN/#`, `# składy|sk|pc|sections`, `S|M|L|XL|roz. #-#`,
  `Float.|Susp.|Sink.`, `#'#"`, `W`.
- **Kategorijos profilis** — token tipas → atributas:
  - valai: `mm→line-diameter, kg|lb→line-strength, m→line-length`
  - spiningai: `cm→rod-length-m, g|#-#g→casting-weight, składy→sections`
  - karpinės: `cm→rod-length-m, lb→testcurve, składy→sections`
  - vobleriai: `cm→lure-size, g→weight, Float./Susp.→action, W→color`
  - džigai/kabliukai: `g→weight, #/0|##→hook-size`
  - ritės: `roz. #→reel-size`; apranga: `S..XL|roz. #-#→size`
  - jaukai: `mm→diameter, g→weight, W→flavour`
  - ~10 profilių dengia ≥74 % Moss; Korda/Shimano formos tos pačios.
- Liekana po splitter'io → `variant_label`. Jei kategorija be profilio →
  visa reikšmė į `variant_label` (ne į `size`).
- Dry-run komanda: coverage % per tiekėją/kategoriją, neišparsintų sample'ai
  → iteruoti profilius iki ≥90 %. Testai — po vieną kiekvienai formai iš
  lentelės aukščiau.
- Priklausomybė: Moss feed'as šiuo metu normalyzeryje neužkrautas (0 offers);
  profilius galima rašyti iš `moss_rozmer_values.txt`, bet ingest testui reikia feed'o.

### Fazė 4 — shop: facetų konfigūracija ir atributo tapatybė

- `Feature.is_filterable` default `False`.
- `CategoryFacet(category, attribute_code, sort_order, widget ∈ {checkbox, range, swatch})`,
  paveldima žemyn medžiu; be įrašų → jokių facetų (ne „visi").
- Facetai ir Typesense laukai raktuojami **kodu**, nepriklausomai ar reikšmė
  atėjo iš `OptionValue` (ašis) ar `FeatureValue` (feature) — viena tapatybė.
- Admin: `CategoryFacet` inline prie kategorijos.

### Fazė 0 (po pirmo reimport'o su F1–F3) — valymas

- Ištrinti `OptionType`/`Feature` be priskyrimų (`_` našlaičiai, `variantas`,
  sumerge'inti kodai). Management command su `--dry-run`.
- Pilnas reindex (`full_reindex` jau valo stale doc'us).

Eiliškumas: F1 → F2 → F3 → reimport → F0 → F4. F1+F2 kartu duoda didžiausią
efektą dubliams; F3 — Moss; F4 — vartotojo matoma kokybė.
