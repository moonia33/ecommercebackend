from __future__ import annotations

import http.client
import html
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Brand, Category, InventoryItem, Product, ProductImage, TaxClass, Variant, Warehouse
from catalog.richtext import normalize_richtext_to_markdown


DEFAULT_URL = "https://zaliojibanga.lt/integrations/services/products.php?key=3fWgWWXyTa9OCXG8"
DEFAULT_WAREHOUSE_CODE = "zalioji_banga"
DEFAULT_TAX_CLASS_CODE = "standard"
MAX_IMAGES_PER_PRODUCT = 5
FEED_MAX_RETRIES = 3


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    s = (value or "").strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return None


def _money_2dp(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _cdata_html(el: ET.Element | None) -> str:
    # Values in feed are often CDATA with escaped HTML like &lt;p&gt;...
    raw = _text(el)
    if not raw:
        return ""
    return html.unescape(raw).strip()


def _split_category_path(value: str) -> list[str]:
    # Feed uses "A / B / C". Be tolerant to spaces.
    parts = [p.strip() for p in (value or "").split("/")]
    return [p for p in parts if p]


def _unique_slug_for_model(model, base: str, *, max_length: int = 200) -> str:
    base = (base or "").strip("-")
    if not base:
        base = "item"

    base = base[:max_length]
    candidate = base
    suffix = 2
    while model.objects.filter(slug=candidate).exists():
        tail = f"-{suffix}"
        candidate = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _stable_suffix(value: str, *, length: int = 6) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _download_image(url: str, *, timeout: int = 30) -> tuple[str, bytes] | None:
    """Download image bytes for storing in ImageField.

    Returns (filename, content) or None on failure.
    """
    u = (url or "").strip()
    if not u:
        return None

    try:
        parsed = urlparse(u)
        name = (parsed.path.rsplit("/", 1)[-1] or "image")
        # Strip query leftovers (just in case)
        name = name.split("?", 1)[0].split("#", 1)[0]
        if "." not in name:
            name = f"{name}.jpg"

        req = Request(u, headers={"User-Agent": "django_ecommerce/zb-import"})
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) >= 400:
                return None
            content = resp.read()
            if not content:
                return None
        return (name, content)
    except Exception:
        return None


@dataclass(frozen=True)
class ZBItem:
    sku: str
    barcode: str
    name: str
    brand_name: str
    category_path: list[str]
    cost_net: Decimal | None
    price_net: Decimal | None
    summary_html: str
    description_html: str
    image_urls: list[str]


def _iter_items(xml_stream) -> Iterable[ZBItem]:
    # Stream-parse large XML feeds.
    context = ET.iterparse(xml_stream, events=("end",))
    for event, elem in context:
        if elem.tag != "item":
            continue

        sku = _text(elem.find("code"))
        barcode = _text(elem.find("ean"))
        name = _cdata_html(elem.find("name"))
        brand_name = _cdata_html(elem.find("brand"))
        category_raw = _cdata_html(elem.find("category"))

        cost = _parse_decimal(_text(elem.find("price")))
        rrp = _parse_decimal(_text(elem.find("rrp")))

        summary_html = _cdata_html(elem.find("summary"))
        description_html = _cdata_html(elem.find("description"))

        image_urls: list[str] = []
        images_el = elem.find("images")
        if images_el is not None:
            for img_el in images_el.findall("image"):
                u = _cdata_html(img_el)
                if u:
                    image_urls.append(u)

        # free memory
        elem.clear()

        category_path = _split_category_path(category_raw)

        if not sku or not name:
            continue

        yield ZBItem(
            sku=sku,
            barcode=barcode,
            name=name,
            brand_name=brand_name,
            category_path=category_path,
            cost_net=cost,
            price_net=rrp,
            summary_html=summary_html,
            description_html=description_html,
            image_urls=image_urls,
        )


class Command(BaseCommand):
    help = "Importuoja Zalioji banga produktus (tik trūkstamus) iš XML feed."\


    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            help="XML feed URL (jei nenurodyta – naudoja settings.ZB_PRODUCTS_FEED_URL arba default).",
            default=None,
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nieko nekeičia DB, tik parodo suvestinę.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maksimalus naujų (trūkstamų) prekių skaičius importui.",
        )

    def handle(self, *args, **options):
        url = options.get("url") or getattr(
            settings, "ZB_PRODUCTS_FEED_URL", "") or DEFAULT_URL
        dry_run: bool = bool(options.get("dry_run"))
        limit_opt = options.get("limit")
        limit: int | None = int(limit_opt) if limit_opt is not None else None
        if limit is not None and limit < 1:
            raise CommandError("--limit turi būti teigiamas skaičius")

        tax_class = TaxClass.objects.filter(
            code=DEFAULT_TAX_CLASS_CODE).first()
        if not tax_class:
            raise CommandError(
                f"TaxClass '{DEFAULT_TAX_CLASS_CODE}' nerastas. Pirma susikonfigūruok mokesčius.")

        warehouse = Warehouse.objects.filter(
            code=DEFAULT_WAREHOUSE_CODE).first()
        if not warehouse:
            raise CommandError(
                f"Warehouse '{DEFAULT_WAREHOUSE_CODE}' nerastas (spec sako, kad jau turi būti sukurtas)."
            )

        existing_skus = set(Product.objects.values_list("sku", flat=True))

        # Cache lookups to reduce DB queries during import.
        # - brands: by normalized name
        # - categories: by (parent_id, normalized name)
        brand_cache: dict[str, Brand] = {}
        category_cache: dict[tuple[int | None, str], Category] = {}

        created_products = 0
        created_brands = 0
        created_categories = 0
        created_variants = 0
        created_images = 0

        self.stdout.write(f"Skaitau XML: {url}")

        req = Request(
            url,
            headers={
                "User-Agent": "django_ecommerce/zb-import",
                # Be conservative: avoid compression/chunking edge cases on flaky servers.
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )

        attempt = 0
        while True:
            try:
                with urlopen(req, timeout=60) as resp:
                    if getattr(resp, "status", 200) >= 400:
                        raise CommandError(
                            f"HTTP klaida: {getattr(resp, 'status', 'unknown')}")

                    for item in _iter_items(resp):
                        if limit is not None and created_products >= limit:
                            break
                        if item.sku in existing_skus:
                            continue

                        # Reikalavimas: skipinti prekes be nuotraukos.
                        if not item.image_urls:
                            continue

                        if item.price_net is None:
                            # Be pardavimo kainos negalim sukurti nei produkto, nei varianto.
                            continue

                        if dry_run:
                            created_products += 1
                            # Avoid double-counting if the feed connection drops and we retry.
                            existing_skus.add(item.sku)
                            continue

                        # Download images BEFORE DB transaction (network can be slow).
                        image_payloads: list[tuple[str, str, bytes]] = []
                        seen_urls: set[str] = set()
                        for img_url in item.image_urls:
                            if img_url in seen_urls:
                                continue
                            seen_urls.add(img_url)
                            dl = _download_image(img_url)
                            if not dl:
                                continue
                            filename, content = dl
                            image_payloads.append((img_url, filename, content))
                            if len(image_payloads) >= MAX_IMAGES_PER_PRODUCT:
                                break

                        # If all URLs failed, treat as "no image" and skip.
                        if not image_payloads:
                            continue

                        with transaction.atomic():
                            brand = None
                            if item.brand_name:
                                brand_name = (item.brand_name or "").strip()
                                if brand_name:
                                    brand_key = brand_name.casefold()
                                    brand = brand_cache.get(brand_key)
                                    if brand is None:
                                        brand = Brand.objects.filter(
                                            name__iexact=brand_name).first()
                                        if brand is None:
                                            base = (slugify(brand_name)
                                                    or "brand")[:200]
                                            slug = base
                                            # Keep slug short; only add stable suffix if the base already exists.
                                            if Brand.objects.filter(slug=slug).exists():
                                                suffix = _stable_suffix(
                                                    brand_key)
                                                slug = f"{base[: 200 - 1 - len(suffix)]}-{suffix}"
                                            slug = _unique_slug_for_model(
                                                Brand, slug, max_length=200)
                                            brand = Brand.objects.create(
                                                name=brand_name,
                                                slug=slug,
                                                is_active=True,
                                            )
                                            created_brands += 1
                                        brand_cache[brand_key] = brand

                            category = None
                            if item.category_path:
                                parent = None
                                for raw_seg in item.category_path:
                                    seg = (raw_seg or "").strip()
                                    if not seg:
                                        continue

                                    cache_key = (
                                        parent.pk if parent else None, seg.casefold())
                                    cached = category_cache.get(cache_key)
                                    if cached is not None:
                                        parent = cached
                                        continue

                                    # Idempotency: prefer existing category by (parent, name).
                                    existing = Category.objects.filter(
                                        parent=parent, name__iexact=seg).first()
                                    if existing is not None:
                                        parent = existing
                                        category_cache[cache_key] = parent
                                        continue

                                    # Slug in this project is globally unique, so use a short segment-based slug.
                                    # Add a stable suffix only when the base collides.
                                    base = (slugify(seg) or "category")
                                    base = base[:80]
                                    slug = base
                                    if Category.objects.filter(slug=slug).exists():
                                        suffix = _stable_suffix(
                                            f"{parent.pk if parent else 'root'}:{seg.casefold()}")
                                        slug = f"{base[: 200 - 1 - len(suffix)]}-{suffix}"
                                    slug = _unique_slug_for_model(
                                        Category, slug, max_length=200)

                                    parent = Category.objects.create(
                                        name=seg,
                                        slug=slug,
                                        parent=parent,
                                        is_active=True,
                                    )
                                    category_cache[cache_key] = parent
                                    created_categories += 1
                                category = parent

                            combined_html = (item.summary_html +
                                             "\n\n" + item.description_html).strip()
                            normalized = normalize_richtext_to_markdown(
                                combined_html, input_format="html")
                            description_md = normalized.markdown

                            seo_desc = normalize_richtext_to_markdown(
                                item.summary_html, input_format="html").markdown
                            seo_desc = (seo_desc or "").replace(
                                "\n", " ").strip()
                            if len(seo_desc) > 320:
                                seo_desc = seo_desc[:320].rstrip()

                            product_slug_base = (
                                slugify(item.name) or "product")
                            product_slug = _unique_slug_for_model(
                                Product,
                                f"{product_slug_base}-{item.sku}"[:255],
                                max_length=255,
                            )

                            price_net = _money_2dp(item.price_net)
                            cost_net = _money_2dp(
                                item.cost_net) if item.cost_net is not None else None

                            product = Product.objects.create(
                                sku=item.sku,
                                name=item.name,
                                slug=product_slug,
                                description=description_md,
                                brand=brand,
                                category=category,
                                tax_class=tax_class,
                                is_active=True,
                                seo_description=seo_desc,
                            )
                            created_products += 1
                            existing_skus.add(item.sku)

                            variant = Variant.objects.create(
                                product=product,
                                sku=item.sku,
                                barcode=item.barcode,
                                name="",
                                price_eur=price_net,
                                cost_eur=cost_net,
                                is_active=True,
                            )
                            created_variants += 1

                            # Spec: likučiai bus atskiru URL vėliau, todėl qty čia nenaudojam.
                            InventoryItem.objects.get_or_create(
                                variant=variant,
                                warehouse=warehouse,
                                defaults={"qty_on_hand": 0,
                                          "qty_reserved": 0, "cost_eur": cost_net},
                            )

                            for idx, (img_url, filename, content) in enumerate(image_payloads):
                                img = ProductImage(
                                    product=product,
                                    image_url=img_url,
                                    alt_text="",
                                    sort_order=idx,
                                )
                                # Saving to ImageField triggers storage upload (local/S3)
                                # and our ProductImage.save() generates AVIF + WEBP renditions.
                                img.image.save(
                                    filename, ContentFile(content), save=True)
                                created_images += 1

                break
            except (http.client.IncompleteRead, TimeoutError, URLError, OSError) as exc:
                attempt += 1
                if attempt >= FEED_MAX_RETRIES:
                    raise CommandError(
                        f"Nepavyko perskaityti feed (bandymai={attempt}). Paskutinė klaida: {exc}"
                    )
                self.stderr.write(
                    self.style.WARNING(
                        f"Feed ryšys nutrūko ({exc}). Kartojam {attempt}/{FEED_MAX_RETRIES}..."
                    )
                )
                time.sleep(min(2 ** attempt, 8))
                continue

        self.stdout.write(
            self.style.SUCCESS(
                "Importas baigtas. "
                f"products={created_products}, variants={created_variants}, "
                f"brands={created_brands}, categories={created_categories}, images={created_images}"
                + (" (dry-run)" if dry_run else "")
            )
        )
