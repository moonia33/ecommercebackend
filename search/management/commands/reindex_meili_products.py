from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from search.indexing import build_product_search_docs, meili_products_settings
from search.meili import MeiliClient, MeiliError


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--index", type=str, default="products_lt_v1")
        parser.add_argument("--site-id", action="append", default=None)
        parser.add_argument("--reset", action="store_true", default=False)
        parser.add_argument("--chunk-size", type=int, default=1000)

    def handle(self, *args, **options):
        index_uid = str(options.get("index") or "products_lt_v1").strip()
        if not index_uid:
            raise CommandError("--index is required")

        site_ids_raw = options.get("site_id")
        site_ids = [int(i) for i in site_ids_raw] if site_ids_raw else None

        reset = bool(options.get("reset"))
        chunk_size = int(options.get("chunk_size") or 1000)
        if chunk_size <= 0:
            chunk_size = 1000

        client = MeiliClient()
        if not client.cfg.host:
            raise CommandError("MEILI_HOST is not configured")

        try:
            client.health()
        except Exception as e:
            raise CommandError(f"Meilisearch not reachable: {e}")

        try:
            t = client.create_index(uid=index_uid, primary_key="id")
            task_uid = int(t.get("taskUid") or t.get("uid") or 0) or None
            if task_uid:
                st = client.wait_for_task(task_uid=task_uid, timeout_seconds=120)
                if str(st.get("status") or "") == "failed":
                    err = st.get("error") or {}
                    code = str((err or {}).get("code") or "")
                    if code != "index_already_exists":
                        raise CommandError(f"Meili task failed: {st}")

            t = client.update_settings(uid=index_uid, settings_payload=meili_products_settings())
            task_uid = int(t.get("taskUid") or t.get("uid") or 0) or None
            if task_uid:
                st = client.wait_for_task(task_uid=task_uid, timeout_seconds=120)
                if str(st.get("status") or "") == "failed":
                    raise CommandError(f"Meili task failed: {st}")

            if reset:
                t = client.delete_all_documents(uid=index_uid)
                task_uid = int(t.get("taskUid") or t.get("uid") or 0) or None
                if task_uid:
                    st = client.wait_for_task(task_uid=task_uid, timeout_seconds=120)
                    if str(st.get("status") or "") == "failed":
                        raise CommandError(f"Meili task failed: {st}")

            docs = build_product_search_docs(site_ids=site_ids)
            total = len(docs)
            if not total:
                self.stdout.write(self.style.WARNING("No documents to index"))
                return

            pos = 0
            while pos < total:
                batch = docs[pos : pos + chunk_size]
                t = client.add_documents(uid=index_uid, documents=batch)
                task_uid = int(t.get("taskUid") or t.get("uid") or 0) or None
                if task_uid:
                    st = client.wait_for_task(task_uid=task_uid, timeout_seconds=300)
                    if str(st.get("status") or "") == "failed":
                        raise CommandError(f"Meili task failed: {st}")
                pos += len(batch)
                self.stdout.write(f"Indexed {pos}/{total}")

            self.stdout.write(self.style.SUCCESS(f"Done. index={index_uid} documents={total}"))
        except MeiliError as e:
            raise CommandError(str(e))
