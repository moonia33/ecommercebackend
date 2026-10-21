"""Išvalo 'orphan' atributų eilutes po importų/mapping pakeitimų.

Trinami TIK nereferencuojami objektai:
- FeatureValue be ProductFeatureValue/VariantFeatureValue nuorodų;
- Feature be likusių values ir be assignments (PFV/VFV);
- OptionValue be VariantOptionValue nuorodų;
- OptionType be likusių values ir be ProductOptionType.
Papildomai: kodai su '_' kai egzistuoja '-' dvynis (bandoma trinti;
referencuojamas dvynis lieka – PROTECT blokuoja, skaičiuojama 'skipped').

Default --dry-run (tik ataskaita); --apply realiai trina.
"""

from django.core.management.base import BaseCommand
from django.db.models import ProtectedError

from catalog.models import (
    Feature,
    FeatureValue,
    OptionType,
    OptionValue,
    ProductFeatureValue,
    ProductOptionType,
    VariantFeatureValue,
    VariantOptionValue,
)


def _dash_twin(code: str) -> str:
    return code.replace("_", "-")


def _feature_value_candidates() -> list[tuple[FeatureValue, str]]:
    out: list[tuple[FeatureValue, str]] = []
    for v in FeatureValue.objects.all().iterator():
        referenced = (
            ProductFeatureValue.objects.filter(feature_value_id=v.id).exists()
            or VariantFeatureValue.objects.filter(feature_value_id=v.id).exists()
        )
        if not referenced:
            out.append((v, "unreferenced"))
            continue
        if "_" in v.value and FeatureValue.objects.filter(
            feature_id=v.feature_id, value=_dash_twin(v.value)
        ).exists():
            out.append((v, "underscore_twin"))
    return out


def _feature_candidates() -> list[tuple[Feature, str]]:
    out: list[tuple[Feature, str]] = []
    for f in Feature.objects.all().iterator():
        if (
            not f.values.exists()
            and not f.product_values.exists()
            and not f.variant_values.exists()
        ):
            out.append((f, "empty"))
            continue
        if "_" in f.code and Feature.objects.filter(
            code=_dash_twin(f.code)
        ).exists():
            out.append((f, "underscore_twin"))
    return out


def _option_value_candidates() -> list[tuple[OptionValue, str]]:
    out: list[tuple[OptionValue, str]] = []
    for v in OptionValue.objects.all().iterator():
        referenced = VariantOptionValue.objects.filter(
            option_value_id=v.id
        ).exists()
        if not referenced:
            out.append((v, "unreferenced"))
            continue
        if "_" in v.code and OptionValue.objects.filter(
            option_type_id=v.option_type_id, code=_dash_twin(v.code)
        ).exists():
            out.append((v, "underscore_twin"))
    return out


def _option_type_candidates() -> list[tuple[OptionType, str]]:
    out: list[tuple[OptionType, str]] = []
    for t in OptionType.objects.all().iterator():
        if not t.values.exists() and not t.products.exists():
            out.append((t, "empty"))
            continue
        if "_" in t.code and OptionType.objects.filter(
            code=_dash_twin(t.code)
        ).exists():
            out.append((t, "underscore_twin"))
    return out


class Command(BaseCommand):
    help = (
        "Trina nebereferencuojamas Feature/FeatureValue/OptionType/OptionValue "
        "eilutes. Default: --dry-run (tik ataskaita), --apply realiai trina."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Tik ataskaita (default).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Realybėje ištrinti kandidatus.",
        )

    def handle(self, *args, **options):
        apply_mode = bool(options["apply"])
        stages = [
            ("FeatureValue", _feature_value_candidates),
            ("Feature", _feature_candidates),
            ("OptionValue", _option_value_candidates),
            ("OptionType", _option_type_candidates),
        ]
        self.stdout.write(
            "MODE: apply" if apply_mode else "MODE: dry-run (nieko netrinama)"
        )
        for model_name, collector in stages:
            total = {
                "FeatureValue": FeatureValue,
                "Feature": Feature,
                "OptionValue": OptionValue,
                "OptionType": OptionType,
            }[model_name].objects.count()
            candidates = collector()
            deleted = skipped = 0
            for obj, reason in candidates:
                if apply_mode:
                    try:
                        obj.delete()
                        deleted += 1
                    except ProtectedError:
                        skipped += 1
                else:
                    deleted += 1
            kept = total - deleted - skipped
            verb = "deleted" if apply_mode else "would delete"
            self.stdout.write(
                f"{model_name}: {verb} {deleted}"
                + (f", skipped {skipped} (referenced/protected)" if skipped else "")
                + f", kept {kept} (of {total})"
            )
            if options.get("verbosity", 1) > 1:
                for obj, reason in candidates:
                    self.stdout.write(f"  - {obj} [{reason}]")
