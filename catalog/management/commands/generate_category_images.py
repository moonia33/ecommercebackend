import base64
import json
from io import BytesIO
from pathlib import Path

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from catalog.models.taxonomy import Category

DEFAULT_PROMPTS_FILE = Path(__file__).resolve().parents[2] / "data" / "category_image_prompts.json"
IMAGES_ENDPOINT = "https://api.openai.com/v1/images/generations"
MAX_B64_BYTES = 12 * 1024 * 1024


def _load_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CommandError(f"Prompts failas nerastas: {path}")
    except json.JSONDecodeError as e:
        raise CommandError(f"Neteisingas JSON ({path}): {e}")


def _generate_image(
    *,
    prompt: str,
    model: str,
    size: str,
    background: str,
    output_format: str,
    svg_model: str = "",
) -> bytes:
    key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not key:
        raise CommandError("OPENAI_API_KEY nenustatytas (.env).")
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    if background and background != "auto":
        payload["background"] = background
    if output_format:
        # 'svg' palaiko tik naujesni gpt-image modeliai (pvz. 2.6-sunburst);
        # API reikalauja atskiro svg.model parametro (SVG rašo tekstinis modelis).
        payload["output_format"] = output_format
        if output_format == "svg":
            payload["svg"] = {"model": svg_model or "gpt-5.2"}
    resp = requests.post(
        IMAGES_ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if resp.status_code != 200:
        raise CommandError(f"OpenAI images klaida {resp.status_code}: {resp.text[:500]}")
    data = resp.json().get("data") or []
    if not data:
        raise CommandError("OpenAI images: tuščias 'data' atsakyme.")
    item = data[0]
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        dl = requests.get(item["url"], timeout=120)
        dl.raise_for_status()
        raw = dl.content
    else:
        raise CommandError("OpenAI images: nei b64_json, nei url atsakyme.")
    if len(raw) > MAX_B64_BYTES:
        raise CommandError(f"Per didelis paveikslas ({len(raw)} B).")
    return raw


def _postprocess(raw: bytes, *, max_px: int, out_format: str, quality: int, keep_alpha: bool) -> bytes:
    from PIL import Image

    img = Image.open(BytesIO(raw))
    if keep_alpha:
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = BytesIO()
    if out_format == "webp":
        img.save(buf, "WEBP", quality=quality, method=6)
    elif out_format == "png":
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _postprocess_svg(raw: bytes, *, color: str = "", stroke_scale: float = 0.0) -> bytes:
    """SVG normalizavimas — spalva ir linijų storis keičiami be modelio."""
    import re

    text = raw.decode("utf-8", errors="replace")
    if color:
        text = re.sub(r'stroke="#[0-9a-fA-F]{3,8}"', f'stroke="{color}"', text)
        text = re.sub(r'fill="#[0-9a-fA-F]{3,8}"', f'fill="{color}"', text)
    if stroke_scale and 0 < stroke_scale < 1:
        def _shrink(m):
            try:
                v = float(m.group(1))
                return f'stroke-width="{max(1.0, v * stroke_scale):g}"'
            except ValueError:
                return m.group(0)
        text = re.sub(r'stroke-width="([0-9.]+)"', _shrink, text)
    return text.encode("utf-8")


class Command(BaseCommand):
    help = (
        "Generuoja kategorijų paveikslus (menu_icon / hero_image) per OpenAI "
        "Images API pagal catalog/data/category_image_prompts.json."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind", default="menu_icon",
            help="Paveikslo tipas iš prompts failo 'kinds' (default: menu_icon)",
        )
        parser.add_argument(
            "--slug", action="append", default=[],
            help="Tik šios kategorijos (kartoti kiekvienam slug). Tuščia = visos.",
        )
        parser.add_argument(
            "--model", default=getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-1"),
            help="OpenAI modelis (default: OPENAI_IMAGE_MODEL)",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Regeneruoti net jei laukas jau užpildytas",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Tik parodyti planą — be API iškvietimų ir DB rašymo",
        )
        parser.add_argument(
            "--missing", action="store_true",
            help="Išvardinti kategorijas be aiškaus prompt'o (fallback bus naudojamas) ir baigti",
        )
        parser.add_argument(
            "--prompts-file", default=str(DEFAULT_PROMPTS_FILE),
            help="Alternatyvus prompts JSON kelias",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Max generuojamų paveikslų (0 = be limito)",
        )

    def handle(self, *args, **options):
        cfg = _load_config(Path(options["prompts_file"]))
        kind = options["kind"]
        kinds = cfg.get("kinds") or {}
        if kind not in kinds:
            raise CommandError(f"Nežinomas kind '{kind}'. Galimi: {', '.join(kinds)}")
        kcfg = kinds[kind]
        field = kcfg.get("field")
        if not field or not hasattr(Category, field):
            raise CommandError(f"kind '{kind}': nežinomas Category laukas '{field}'.")

        prompts = (cfg.get("prompts") or {}).get(kind) or {}
        fallback = kcfg.get("fallback_prompt") or ""

        qs = Category.objects.filter(is_active=True).order_by("tree_id", "lft")
        if options["slug"]:
            qs = qs.filter(slug__in=options["slug"])
            found = set(qs.values_list("slug", flat=True))
            missing_slugs = set(options["slug"]) - found
            if missing_slugs:
                raise CommandError(f"Kategorijos nerastos: {', '.join(sorted(missing_slugs))}")

        if options["missing"]:
            for c in qs:
                if c.slug not in prompts:
                    self.stdout.write(f"  {c.slug}  ({c.name})")
            return

        keep_alpha = kcfg.get("background") == "transparent"
        todo = []
        for c in qs:
            current = getattr(c, field)
            has_value = bool(current or getattr(c, f"{field}_url", ""))
            if has_value and not options["force"]:
                continue
            prompt = prompts.get(c.slug) or (
                fallback.format(name=c.name) if fallback else ""
            )
            if not prompt:
                continue
            todo.append((c, prompt, c.slug not in prompts))
        if options["limit"]:
            todo = todo[: options["limit"]]

        self.stdout.write(
            f"kind={kind} model={options['model']} — generuosiu: {len(todo)} "
            f"(fallback: {sum(1 for _, _, fb in todo if fb)})"
        )
        if options["dry_run"]:
            for c, prompt, fb in todo:
                tag = " [fallback]" if fb else ""
                self.stdout.write(f"  {c.slug}{tag}: {prompt}")
            return

        out_format = kcfg.get("output_format") or "webp"
        done = failed = 0
        for c, prompt, fb in todo:
            full_prompt = f"{kcfg['style']} {prompt}"
            try:
                raw = _generate_image(
                    prompt=full_prompt,
                    model=options["model"],
                    size=kcfg.get("size") or settings.OPENAI_IMAGE_SIZE,
                    background=kcfg.get("background") or "auto",
                    output_format=out_format,
                    svg_model=kcfg.get("svg_model") or "",
                )
                if out_format == "svg":
                    out = _postprocess_svg(
                        raw,
                        color=kcfg.get("color") or "",
                        stroke_scale=float(kcfg.get("stroke_scale") or 0.0),
                    )
                else:
                    out = _postprocess(
                        raw,
                        max_px=int(kcfg.get("max_px") or 1024),
                        out_format=out_format,
                        quality=int(kcfg.get("quality") or 85),
                        keep_alpha=keep_alpha,
                    )
                getattr(c, field).save(f"{c.slug}.{out_format}", ContentFile(out), save=True)
                done += 1
                self.stdout.write(f"  OK {c.slug}" + (" [fallback]" if fb else ""))
            except Exception as e:
                failed += 1
                self.stderr.write(f"  FAIL {c.slug}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Baigta: {done} OK, {failed} klaidų."))
