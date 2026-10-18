from celery import shared_task


@shared_task
def sync_product_document_task(product_id: int) -> int:
    from .services import index_products

    return index_products([int(product_id)])


@shared_task
def sync_products_task(product_ids: list[int]) -> int:
    from .services import index_products

    return index_products(product_ids)


@shared_task
def delete_product_document_task(product_id: int) -> None:
    from .services import delete_product_document

    delete_product_document(int(product_id))


@shared_task
def reindex_products_task() -> dict:
    from .services import full_reindex

    return full_reindex()


@shared_task
def sync_search_curation_task() -> dict:
    """Reconcile admin-managed synonyms + overrides into Typesense.

    Run on admin changes and nightly — drops expired promo overrides."""
    from .services import sync_curation

    return sync_curation()
