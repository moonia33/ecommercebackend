from __future__ import annotations

from io import BytesIO

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def ingest_product_image_task(self, product_image_id: int):
    """Download `image_url` into `image` (object storage) -> triggers rendition task."""
    import urllib.request
    from urllib.parse import urlparse

    from .models import ProductImage

    try:
        instance = ProductImage.objects.get(pk=product_image_id)
    except ProductImage.DoesNotExist:
        return

    if instance.image:
        if instance.ingest_status != ProductImage.INGEST_DONE:
            instance.ingest_status = ProductImage.INGEST_DONE
            instance.ingest_error = ""
            instance.save(update_fields=["ingest_status", "ingest_error"])
        return

    url = (instance.image_url or "").strip()
    if not url:
        return

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        instance.ingest_status = ProductImage.INGEST_ERROR
        instance.ingest_error = f"unsupported url scheme '{parsed.scheme}'"
        instance.save(update_fields=["ingest_status", "ingest_error"])
        return

    max_bytes = int(getattr(settings, "IMAGE_INGEST_MAX_BYTES", 8 * 1024 * 1024))
    timeout = int(getattr(settings, "IMAGE_INGEST_TIMEOUT", 20))

    # Tiekeju URL'uose buna tarpu/ne-ASCII simboliu kelio dalyje
    # (pvz. shimanoshop-eu.com 'Aero Float_1.jpg') — encoduojam path.
    from urllib.parse import urlunsplit, quote

    url = urlunsplit(
        (parsed.scheme, parsed.netloc, quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"), parsed.query, parsed.fragment)
    )

    def _open(u):
        req = urllib.request.Request(u, headers={"User-Agent": "catalog-image-ingest/1.0"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as exc:
            # Kai kuriuose hostuose OpenSSL neprivercia grandines (pvz. naujas
            # Let's Encrypt ECDSA 'Root YE' kelias). Fallback tik whitelisto
            # hostams — nuotraukos, ne autentifikuota API.
            insecure_hosts = getattr(
                settings, "IMAGE_INGEST_INSECURE_TLS_HOSTS", {"team.kordashop.net"}
            )
            if (
                parsed.netloc.lower() in insecure_hosts
                and "CERTIFICATE_VERIFY_FAILED" in str(getattr(exc, "reason", exc))
            ):
                import ssl

                return urllib.request.urlopen(
                    req, timeout=timeout, context=ssl._create_unverified_context()
                )
            raise

    try:
        with _open(url) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            allowed = getattr(
                settings, "IMAGE_INGEST_CONTENT_TYPES",
                {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/svg+xml"},
            )
            if content_type and content_type not in allowed:
                raise ValueError(f"unsupported content-type '{content_type}'")
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ValueError(f"file too large ({declared} bytes, max {max_bytes})")
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"file too large (> {max_bytes} bytes)")

        if len(data) < 32:
            raise ValueError("response too small to be an image")

        ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        ext_map = {
            "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
            "image/gif": "gif", "image/avif": "avif", "image/svg+xml": "svg",
        }
        if ext not in ("jpg", "jpeg", "png", "webp", "gif", "avif", "svg"):
            ext = ext_map.get(content_type, "jpg")

        filename = f"ingested_{instance.id}.{ext}"
        instance.ingest_status = ProductImage.INGEST_DONE
        instance.ingest_error = ""
        # image.save() writes to storage (R2) and save() dispatches rendition task
        instance.image.save(filename, ContentFile(data), save=False)
        instance.save(update_fields=["image", "ingest_status", "ingest_error"])
    except Exception as exc:
        instance.ingest_status = ProductImage.INGEST_ERROR
        instance.ingest_error = str(exc)[:500]
        instance.save(update_fields=["ingest_status", "ingest_error"])
        if getattr(self.request, "called_directly", False):
            return  # invoked inline (e.g. --sync command) — no retry machinery
        if int(getattr(self.request, "retries", 0) or 0) < self.max_retries:
            raise self.retry(exc=exc)


@shared_task
def process_product_image_task(product_image_id: int):
    from .models import ProductImage

    try:
        instance = ProductImage.objects.get(pk=product_image_id)
    except ProductImage.DoesNotExist:
        return

    if not instance.image:
        return

    try:
        from PIL import Image
        import pillow_avif  # noqa: F401

        def _trim_whitespace(im: Image.Image, *, tol: int = 18):
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            if w < 10 or h < 10:
                return im
            px = im.load()
            corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
            bg = (
                sum(c[0] for c in corners) // 4,
                sum(c[1] for c in corners) // 4,
                sum(c[2] for c in corners) // 4,
            )

            def is_fg(rgb):
                return (
                    abs(int(rgb[0]) - int(bg[0])) > tol
                    or abs(int(rgb[1]) - int(bg[1])) > tol
                    or abs(int(rgb[2]) - int(bg[2])) > tol
                )

            x0, y0 = w, h
            x1, y1 = 0, 0
            any_fg = False
            step_x = 1 if w <= 800 else max(1, w // 800)
            step_y = 1 if h <= 800 else max(1, h // 800)
            for y in range(0, h, step_y):
                for x in range(0, w, step_x):
                    if is_fg(px[x, y]):
                        any_fg = True
                        if x < x0:
                            x0 = x
                        if y < y0:
                            y0 = y
                        if x > x1:
                            x1 = x
                        if y > y1:
                            y1 = y
            if not any_fg:
                return im
            pad = 2
            x0 = max(0, x0 - pad)
            y0 = max(0, y0 - pad)
            x1 = min(w - 1, x1 + pad)
            y1 = min(h - 1, y1 + pad)
            if x1 <= x0 or y1 <= y0:
                return im
            return im.crop((x0, y0, x1 + 1, y1 + 1))

        def _fit_pad_square(im: Image.Image, *, edge: int, bg=(255, 255, 255)):
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            if w <= 0 or h <= 0:
                return im
            scale = min(edge / w, edge / h)
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            resized = im.resize((new_w, new_h), resample=Image.LANCZOS)
            canvas = Image.new("RGB", (edge, edge), color=bg)
            left = (edge - new_w) // 2
            top = (edge - new_h) // 2
            canvas.paste(resized, (left, top))
            return canvas

        instance.image.open()
        img = Image.open(instance.image)
        if img.mode in ("P", "RGBA"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        base_name = instance.image.name.rsplit("/", 1)[-1]
        stem = base_name.rsplit(".", 1)[0] if "." in base_name else base_name

        def _save(rendition, fmt, field, name, **kw):
            buf = BytesIO()
            rendition.save(buf, format=fmt, **kw)
            buf.seek(0)
            getattr(instance, field).save(name, ContentFile(buf.read()), save=False)

        # Proporcingos rendition'os: thumbnail (m), PDP (detail). Niekada
        # ne-upscale'inam — jei saltinis mazesnis, thumbnail() palieka.
        for edge, prefix, field_a, field_w in (
            (int(getattr(settings, "MEDIUM_SIZE", 300) or 300), "m", "image_avif", "image_webp"),
            (int(getattr(settings, "DETAIL_IMAGE_SIZE", 800) or 800), "d", "detail_avif", "detail_webp"),
        ):
            rendition = img.copy()
            if max(rendition.size) > edge:
                rendition.thumbnail((edge, edge))
            _save(rendition, "AVIF", field_a, f"{stem}_{prefix}{edge}.avif", quality=60)
            _save(rendition, "WEBP", field_w, f"{stem}_{prefix}{edge}.webp", quality=75, method=6)

        # Kvadratines (1:1) listing rendition'os: trim whitespace + fit/pad.
        listing_tol = int(getattr(settings, "LISTING_TRIM_TOLERANCE", 18) or 18)
        trimmed = _trim_whitespace(img.copy(), tol=listing_tol)
        for edge, field_a, field_w in (
            (
                int(getattr(settings, "LISTING_IMAGE_SIZE", 300) or 300),
                "listing_avif",
                "listing_webp",
            ),
            (
                int(getattr(settings, "LISTING_IMAGE_SIZE_2X", 600) or 600),
                "listing_avif_2x",
                "listing_webp_2x",
            ),
        ):
            square = _fit_pad_square(trimmed.copy(), edge=edge, bg=(255, 255, 255))
            _save(square, "AVIF", field_a, f"{stem}_sq{edge}.avif", quality=60)
            _save(square, "WEBP", field_w, f"{stem}_sq{edge}.webp", quality=78, method=6)

        instance._skip_image_processing = True
        instance.save(
            update_fields=[
                "image_avif",
                "image_webp",
                "detail_avif",
                "detail_webp",
                "listing_avif",
                "listing_webp",
                "listing_avif_2x",
                "listing_webp_2x",
            ]
        )
    except Exception:
        pass


@shared_task
def ingest_pending_images_task():
    """Cron job: nuotraukų ingest backlog'as (post-import)."""
    from django.core.management import call_command

    call_command("ingest_pending_images", workers=8)


@shared_task
def regenerate_image_renditions_task():
    """Cron job: dogeneruoti trūkstamas image rendition'as."""
    from django.core.management import call_command

    call_command("regenerate_image_renditions", only_missing=True, workers=8)


@shared_task
def notify_back_in_stock_task(inventory_item_id: int):
    from .models import BackInStockSubscription, InventoryItem
    from notifications.services import send_templated_email

    try:
        instance = InventoryItem.objects.select_related("variant__product").get(pk=inventory_item_id)
    except InventoryItem.DoesNotExist:
        return

    variant = instance.variant
    product = getattr(variant, "product", None)
    channel = "outlet" if instance.offer_visibility == InventoryItem.OfferVisibility.OUTLET else "normal"

    q = Q()
    if variant is not None:
        q |= Q(variant=variant)
    if product is not None:
        q |= Q(product=product)
    if not q:
        return

    qs = BackInStockSubscription.objects.filter(
        is_active=True,
        notified_at__isnull=True,
        channel=channel,
    ).filter(q)

    now = timezone.now()
    for sub in qs.distinct().iterator():
        result = send_templated_email(
            template_key="catalog_back_in_stock",
            to_email=sub.email,
            context={
                "product_name": getattr(product, "name", "") if product else "",
                "product_slug": getattr(product, "slug", "") if product else "",
                "product_sku": getattr(product, "sku", "") if product else "",
                "variant_sku": getattr(variant, "sku", "") if variant else "",
                "channel": channel,
            },
            language_code=(getattr(sub, "language_code", "") or None),
        )
        if result.ok:
            sub.notified_at = now
            sub.is_active = False
            sub.save(update_fields=["notified_at", "is_active"])
