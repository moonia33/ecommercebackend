"""Seni (PrestaShop / lietuviški) atributų kodai -> kanoniniai angliški.

Kanoniniai kodai sutampa su normalyzer'io `apps/catalog/attributes.py`
(kebab forma). Pavadinimai lokalizuojami per *Translation lenteles, ne
per kodą (BZ-26). Naudoja `merge_attribute_codes` komanda.
"""

LEGACY_ATTRIBUTE_CODES: dict[str, str] = {
    "boiliu-diametras": "diameter",
    "daliu-skaicius": "sections",
    "dydis": "size",
    "graibsto-modelis": "model",
    "grazto-diametras": "diameter",
    "kabliuku-dydis": "hook-size",
    "kedziu-ir-platformu-priedu-variantai": "model",
    "kvapas": "flavour",
    "masalo-dydis": "lure-size",
    "mato-modelis": "model",
    "meskeres-ilgis": "rod-length-m",
    "miegmaisiu-ir-gultu-modeliai": "model",
    "modelis": "model",
    "palapines-modelis": "model",
    "rites-modelis": "model",
    "seryklos-svoris": "weight",
    "spalva": "color",
    "spiningavimo-masalo-ilgis": "lure-size",
    "spiningavimo-masalo-svoris": "weight",
    "stovo-modelis": "model",
    "svareliu-svoris": "weight",
    "svoriai": "weight",
    "talpa": "volume",
    "uzmetimo-svoris": "casting-weight",
    "valo-diametras": "line-diameter",
    "valo-ilgis": "line-length",
    "valo-keliamoji-galia": "line-strength",
    "valties-modelis": "model",
}
