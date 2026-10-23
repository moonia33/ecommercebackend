"""ProductImage trynimas kartu su storage failais.

post_delete signalas trina failus per-eilutes storage.delete() — ~HTTP
kvietimas per faila. Dideliems trynimams (import reconcile, prune komandos)
signalas atjungiamas ir failai trinami S3 delete_objects batch'ais po 1000.
Non-S3 storage'e (local dev) paprastas queryset.delete() — signalai suteikia
per-eilutes valyma patys.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from django.core.files.storage import default_storage
from django.db.models.signals import post_delete

from catalog.models import ProductImage
from catalog.signals import product_image_post_delete

IMAGE_FILE_FIELDS = (
    "image",
    "image_avif",
    "image_webp",
    "listing_avif",
    "listing_webp",
    "listing_avif_2x",
    "listing_webp_2x",
    "detail_avif",
    "detail_webp",
)


def image_file_names(rows: Iterable[ProductImage]) -> list[str]:
    names: list[str] = []
    for r in rows:
        for f in IMAGE_FILE_FIELDS:
            v = getattr(r, f, None)
            if v and getattr(v, "name", ""):
                names.append(v.name)
    return names


def bulk_delete_product_images(ids: Iterable[int], *, workers: int = 8) -> int:
    """Istrina ProductImage eilutes + failus. Grazina istrintu eiluciu skaiciu."""
    ids = list(ids)
    if not ids:
        return 0

    storage = default_storage
    is_s3 = bool(
        getattr(storage, "bucket_name", None)
        and getattr(storage, "connection", None) is not None
    )
    if not is_s3:
        return ProductImage.objects.filter(id__in=ids).delete()[0]

    names = image_file_names(
        ProductImage.objects.filter(id__in=ids).iterator(chunk_size=2000)
    )

    post_delete.disconnect(product_image_post_delete, sender=ProductImage)
    try:
        deleted = 0
        for i in range(0, len(ids), 5000):
            chunk = ids[i : i + 5000]
            deleted += ProductImage.objects.filter(id__in=chunk).delete()[0]
    finally:
        post_delete.connect(product_image_post_delete, sender=ProductImage)

    client = storage.connection.meta.client
    bucket = storage.bucket_name

    def delete_batch(batch):
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )

    batches = [names[i : i + 1000] for i in range(0, len(names), 1000)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(delete_batch, batches))

    return deleted
