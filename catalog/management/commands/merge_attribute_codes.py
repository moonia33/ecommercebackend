"""Sujungia senus (PrestaShop/LT) atributų kodus į kanoninius EN (BZ-26).

Pvz. `kabliuku-dydis` -> `hook-size`, `spalva` -> `color`. Tiek Feature,
tiek OptionType pusėje:
- reikšmės perkeliamos į tikslinį kodą (ta pati reikšmė -> viena eilutė);
- PFV/VFV/VOV/ProductOptionType perrišami, dublikatai pašalinami;
- seno kodo vertimai (pvz. LT pavadinimas) kopijuojami, jei tikslinis
  tos kalbos neturi;
- CategoryFacet kodai perrašomi;
- tušti seni kodai ištrinami.

Variantas, kuris jau turi tikslinį option tipą su KITA reikšme,
neliečiamas ir rodomas ataskaitoje kaip konfliktas (rankinei peržiūrai).

Default – dry-run: viskas vykdoma transakcijoje ir atšaukiama, ataskaita
rodo tikslius kiekius. `--apply` – įrašo. Prieš `--apply`: DB backup.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.attribute_aliases import LEGACY_ATTRIBUTE_CODES
from catalog.models import (
    CategoryFacet,
    Feature,
    FeatureTranslation,
    FeatureValue,
    FeatureValueGroup,
    FeatureValueTranslation,
    OptionType,
    OptionTypeTranslation,
    OptionValue,
    OptionValueGroup,
    OptionValueTranslation,
    ProductFeatureValue,
    ProductOptionType,
    VariantFeatureValue,
    VariantOptionValue,
)


class _DryRun(Exception):
    pass


def _copy_missing_translations(model, fk: str, src, dst, text_field: str) -> None:
    have = set(model.objects.filter(**{fk: dst}).values_list("language_code", flat=True))
    for tr in model.objects.filter(**{fk: src}):
        if tr.language_code not in have and getattr(tr, text_field):
            model.objects.create(
                **{fk: dst, "language_code": tr.language_code, text_field: getattr(tr, text_field)}
            )


def _move_groups(model, fk: str, old, target, stats: Counter) -> None:
    for group in model.objects.filter(**{fk: old}):
        clash = model.objects.filter(**{fk: target, "code": group.code}).first()
        if clash is None:
            setattr(group, fk, target)
            group.save(update_fields=[fk])
            stats["value_groups_moved"] += 1
        else:
            clash.values.add(*group.values.all())
            group.delete()
            stats["value_groups_merged"] += 1


def _merge_feature(old: Feature, new_code: str, stats: Counter) -> None:
    target, created = Feature.objects.get_or_create(
        code=new_code,
        defaults={
            "name": old.name,
            "datatype": old.datatype,
            "unit": old.unit,
            "is_filterable": old.is_filterable,
            "allows_multiple": old.allows_multiple,
        },
    )
    stats["features_created"] += int(created)
    _copy_missing_translations(FeatureTranslation, "feature", old, target, "name")
    _move_groups(FeatureValueGroup, "feature", old, target, stats)
    for value in FeatureValue.objects.filter(feature=old):
        tval, vcreated = FeatureValue.objects.get_or_create(
            feature=target,
            value=value.value,
            defaults={
                "label": value.label,
                "value_num": value.value_num,
                "value_num_max": value.value_num_max,
                "unit": value.unit,
                "sort_order": value.sort_order,
            },
        )
        stats["feature_values_created" if vcreated else "feature_values_merged"] += 1
        _copy_missing_translations(
            FeatureValueTranslation, "feature_value", value, tval, "value"
        )
        for model, owner in ((ProductFeatureValue, "product_id"), (VariantFeatureValue, "variant_id")):
            rows = model.objects.filter(feature_value=value)
            dup_owners = set(
                model.objects.filter(
                    feature_value=tval,
                    **{f"{owner}__in": rows.values_list(owner, flat=True)},
                ).values_list(owner, flat=True)
            )
            stats[f"{model.__name__}_dropped_dup"] += rows.filter(
                **{f"{owner}__in": dup_owners}
            ).delete()[0]
            stats[f"{model.__name__}_moved"] += rows.update(feature=target, feature_value=tval)
        for group in value.groups.all():
            group.values.add(tval)
        value.delete()
    if not old.values.exists() and not old.product_values.exists() and not old.variant_values.exists():
        old.delete()
        stats["features_deleted"] += 1


def _merge_option_type(old: OptionType, new_code: str, stats: Counter, conflicts: list) -> None:
    target, created = OptionType.objects.get_or_create(
        code=new_code,
        defaults={
            "name": old.name,
            "datatype": old.datatype,
            "unit": old.unit,
            "display_type": old.display_type,
            "swatch_type": old.swatch_type,
        },
    )
    stats["option_types_created"] += int(created)
    _copy_missing_translations(OptionTypeTranslation, "option_type", old, target, "name")
    _move_groups(OptionValueGroup, "option_type", old, target, stats)
    for value in OptionValue.objects.filter(option_type=old):
        tval, vcreated = OptionValue.objects.get_or_create(
            option_type=target,
            code=value.code,
            defaults={
                "label": value.label,
                "value_num": value.value_num,
                "value_num_max": value.value_num_max,
                "unit": value.unit,
                "sort_order": value.sort_order,
            },
        )
        stats["option_values_created" if vcreated else "option_values_merged"] += 1
        _copy_missing_translations(
            OptionValueTranslation, "option_value", value, tval, "label"
        )
        for vov in VariantOptionValue.objects.filter(option_value=value).select_related("variant"):
            existing = VariantOptionValue.objects.filter(
                variant_id=vov.variant_id, option_type=target
            ).first()
            if existing is None:
                vov.option_type = target
                vov.option_value = tval
                vov.save(update_fields=["option_type", "option_value"])
                stats["variant_options_moved"] += 1
            elif existing.option_value_id == tval.id:
                vov.delete()
                stats["variant_options_dropped_dup"] += 1
            else:
                conflicts.append(
                    (old.code, new_code, vov.variant.sku, value.label, existing.option_value.label)
                )
        for group in value.groups.all():
            group.values.add(tval)
        if not VariantOptionValue.objects.filter(option_value=value).exists():
            value.delete()
    for pot in ProductOptionType.objects.filter(option_type=old):
        if VariantOptionValue.objects.filter(
            variant__product_id=pot.product_id, option_type=old
        ).exists():
            continue
        if ProductOptionType.objects.filter(product_id=pot.product_id, option_type=target).exists():
            pot.delete()
        else:
            pot.option_type = target
            pot.save(update_fields=["option_type"])
        stats["product_option_types_moved"] += 1
    if not old.values.exists() and not ProductOptionType.objects.filter(option_type=old).exists():
        old.delete()
        stats["option_types_deleted"] += 1


def _merge_facets(old_code: str, new_code: str, stats: Counter) -> None:
    for facet in CategoryFacet.objects.filter(code=old_code):
        if CategoryFacet.objects.filter(category_id=facet.category_id, code=new_code).exists():
            facet.delete()
            stats["facets_dropped_dup"] += 1
        else:
            facet.code = new_code
            facet.save(update_fields=["code"])
            stats["facets_renamed"] += 1


class Command(BaseCommand):
    help = "Sujungia senus LT atributų kodus į kanoninius EN (default dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Įrašyti (be jo – dry-run).")
        parser.add_argument("--code", action="append", default=[], help="Tik šis senas kodas (galima kartoti).")

    def handle(self, *args, **opts):
        mapping = LEGACY_ATTRIBUTE_CODES
        if opts["code"]:
            unknown = set(opts["code"]) - set(mapping)
            if unknown:
                raise CommandError(f"Nežinomi kodai: {', '.join(sorted(unknown))}")
            mapping = {c: mapping[c] for c in opts["code"]}
        per_code: dict[str, Counter] = {}
        conflicts: list = []
        try:
            with transaction.atomic():
                for old_code, new_code in mapping.items():
                    stats = Counter()
                    for feature in Feature.objects.filter(code=old_code):
                        _merge_feature(feature, new_code, stats)
                    for option_type in OptionType.objects.filter(code=old_code):
                        _merge_option_type(option_type, new_code, stats, conflicts)
                    _merge_facets(old_code, new_code, stats)
                    if stats:
                        per_code[old_code] = stats
                if not opts["apply"]:
                    raise _DryRun
        except _DryRun:
            pass
        mode = "APPLY" if opts["apply"] else "DRY-RUN (niekas neįrašyta)"
        self.stdout.write(f"== merge_attribute_codes: {mode}")
        for old_code, stats in per_code.items():
            body = ", ".join(f"{k}={v}" for k, v in sorted(stats.items()) if v)
            self.stdout.write(f"{old_code} -> {mapping[old_code]}: {body}")
        self.stdout.write(f"Konfliktai (variantas jau turi tikslinę ašį su kita reikšme): {len(conflicts)}")
        for old_code, new_code, sku, old_label, new_label in conflicts[:200]:
            self.stdout.write(f"  {sku}: {old_code}={old_label} vs {new_code}={new_label}")
