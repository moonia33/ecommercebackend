from __future__ import annotations

import io
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.jwt_utils import issue_access_token
from accounts.models import User
from api.models import Shop, ShopLanguage
from catalog.models import Category, Product, TaxClass

from .models import BlogPost, BlogPostTranslation, BlogSlugRedirect, CmsPage
from .richtext import doc_plain_text, doc_to_html, doc_to_segments


def _doc(*nodes):
    return {"type": "doc", "content": list(nodes)}


def _p(text, marks=None):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return {"type": "paragraph", "content": [node]}


class RichTextTests(TestCase):
    def test_renders_and_escapes(self):
        html = doc_to_html(_doc(
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            _p("<script>alert(1)</script>", [{"type": "bold"}]),
            _p("bad", [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]),
            _p("ok", [{"type": "link", "attrs": {"href": "https://example.com"}}]),
            {"type": "image", "attrs": {"src": "javascript:x", "alt": "a"}},
            {"type": "image", "attrs": {"src": "/media/cms/a.png", "alt": "\" onerror=\"x"}},
        ))
        self.assertIn("<h2>Title</h2>", html)
        self.assertIn("<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>", html)
        self.assertNotIn("javascript:", html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('src="/media/cms/a.png"', html)
        self.assertNotIn('onerror="', html)

    def test_table_and_lists(self):
        html = doc_to_html(_doc(
            {"type": "bulletList", "content": [{"type": "listItem", "content": [_p("a")]}]},
            {"type": "table", "content": [{"type": "tableRow", "content": [
                {"type": "tableHeader", "attrs": {"colspan": 2}, "content": [_p("h")]},
            ]}]},
        ))
        self.assertIn("<ul><li><p>a</p></li></ul>", html)
        self.assertIn('<th colspan="2"><p>h</p></th>', html)

    def test_product_node_splits_segments(self):
        segs = doc_to_segments(_doc(
            _p("before"),
            {"type": "productCards", "attrs": {"productIds": [3, "3", 5, "x"], "title": "Tinka"}},
            _p("after"),
        ))
        self.assertEqual([s["type"] for s in segs], ["html", "products", "html"])
        self.assertEqual(segs[1]["product_ids"], [3, 5])
        self.assertEqual(doc_plain_text(_doc(_p("a b"), _p("c"))), "a b c")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CmsOpsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop = Shop.objects.create(
            code="cms", name="CMS Shop", primary_domain="cms.local", default_language_code="lt",
            default_country_code="LT", currency="EUR", is_active=True)
        ShopLanguage.objects.create(shop=self.shop, language_code="lt", is_active=True, is_default=True)
        self.staff = User.objects.create_user(email="cms-staff@example.com", password="x", is_staff=True)
        self.user = User.objects.create_user(email="cms-user@example.com", password="x")
        tax, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        cat = Category.objects.create(name="C", slug="c-cms", is_active=True)
        self.product = Product.objects.create(
            sku="CMS-1", name="Ritė", slug="rite-cms", tax_class=tax, category=cat, is_active=True)

    def _login(self, user):
        self.client.cookies["access_token"] = issue_access_token(user_id=user.id)

    def _post(self, url, data):
        return self.client.post(url, data, content_type="application/json")

    def _patch(self, url, data):
        return self.client.patch(url, data, content_type="application/json")

    def _get(self, url, params=None):
        return self.client.get(url, params or {})

    def test_requires_staff(self):
        self.assertEqual(self._get("/api/v1/ops/cms/pages").status_code, 401)
        self._login(self.user)
        self.assertEqual(self._get("/api/v1/ops/cms/pages").status_code, 403)
        self.assertEqual(self._post("/api/v1/ops/cms/blog/posts", {}).status_code, 403)

    def test_page_with_editor_body_is_rendered_as_html(self):
        self._login(self.staff)
        res = self._post("/api/v1/ops/cms/pages", {
            "slug": "test-puslapis", "shop_id": None, "is_active": True,
            "translations": [{"language_code": "lt", "title": "Apie mus", "body_json": _doc(_p("Sveiki"))}],
        })
        self.assertEqual(res.status_code, 200, res.content)
        page_id = res.json()["id"]
        self.assertTrue(res.json()["uses_editor"])
        dup = self._post("/api/v1/ops/cms/pages", {"slug": "test-puslapis", "translations": []})
        self.assertEqual(dup.status_code, 400)

        public = self._get("/api/v1/cms/pages/test-puslapis", {"language_code": "lt"}).json()
        self.assertEqual(public["title"], "Apie mus")
        self.assertEqual(public["content_blocks"], [{"type": "html", "payload": {"html": "<p>Sveiki</p>"}}])

        bad = self._patch(f"/api/v1/ops/cms/pages/{page_id}", {
            "slug": "test-puslapis", "translations": [{"language_code": "lt", "body_json": {"x": 1}}]})
        self.assertEqual(bad.status_code, 400)

    def test_legacy_markdown_page_unchanged(self):
        page = CmsPage.objects.create(slug="senas", is_active=True)
        page.translations.create(language_code="lt", title="Senas", body_markdown="**hi**")
        public = self._get("/api/v1/cms/pages/senas", {"language_code": "lt"}).json()
        self.assertEqual(public["content_blocks"][0]["type"], "rich_text")

    def _create_post(self, **extra):
        payload = {
            "status": "draft", "related_product_ids": [self.product.id],
            "translations": [{
                "language_code": "lt", "title": "Kaip pasirinkti ritę", "excerpt": "Trumpai",
                "body_json": _doc(_p("Tekstas"), {"type": "productCards", "attrs": {"productIds": [self.product.id]}}),
            }],
        }
        payload.update(extra)
        return self._post("/api/v1/ops/cms/blog/posts", payload)

    def test_blog_lifecycle(self):
        self._login(self.staff)
        res = self._create_post()
        self.assertEqual(res.status_code, 200, res.content)
        post = res.json()
        self.assertEqual(post["state"], "draft")
        self.assertEqual(post["slug"], "kaip-pasirinkti-rite")
        self.assertEqual(post["related_products"][0]["sku"], "CMS-1")

        self.assertEqual(self._get("/api/v1/cms/blog/posts").json()["total_count"], 0)
        self.assertEqual(self._get("/api/v1/cms/blog/posts/kaip-pasirinkti-rite").status_code, 404)

        future = (timezone.now() + timedelta(days=2)).isoformat()
        body = {"status": "published", "published_at": future, "related_product_ids": [self.product.id], "translations": [
            {"language_code": "lt", "title": "Kaip pasirinkti ritę", "slug": "kaip-pasirinkti-rite",
             "body_json": _doc(_p("Tekstas"))}]}
        res = self._patch(f"/api/v1/ops/cms/blog/posts/{post['id']}", body)
        self.assertEqual(res.json()["state"], "scheduled")
        self.assertEqual(self._get("/api/v1/cms/blog/posts").json()["total_count"], 0)

        body["published_at"] = None
        body["translations"][0]["slug"] = "rites-pasirinkimas"
        res = self._patch(f"/api/v1/ops/cms/blog/posts/{post['id']}", body)
        self.assertEqual(res.json()["state"], "published")
        self.assertTrue(BlogSlugRedirect.objects.filter(old_slug="kaip-pasirinkti-rite").exists())

        listing = self._get("/api/v1/cms/blog/posts", {"product": self.product.id}).json()
        self.assertEqual(listing["total_count"], 1)
        self.assertEqual(listing["results"][0]["slug"], "rites-pasirinkimas")

        old = self._get("/api/v1/cms/blog/posts/kaip-pasirinkti-rite")
        self.assertEqual(old.status_code, 200, old.content)
        old = old.json()
        self.assertEqual(old["redirect_to_slug"], "rites-pasirinkimas")
        detail = self._get("/api/v1/cms/blog/posts/rites-pasirinkimas", {"language_code": "lt"}).json()
        self.assertEqual(detail["redirect_to_slug"], "")
        self.assertEqual(detail["content_blocks"][0]["type"], "html")
        self.assertEqual(self._get("/api/v1/cms/blog/sitemap").json()[0]["slug"], "rites-pasirinkimas")

        self.assertEqual(self.client.delete(
            f"/api/v1/ops/cms/blog/posts/{post['id']}").status_code, 400)

    def test_blog_slug_clash_and_publish_needs_title(self):
        self._login(self.staff)
        self.assertEqual(self._create_post().status_code, 200)
        self.assertEqual(self._create_post().status_code, 400)
        res = self._post("/api/v1/ops/cms/blog/posts", {"status": "published", "translations": []})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(BlogPostTranslation.objects.count(), 1)
        self.assertEqual(BlogPost.objects.count(), 1)

    def test_media_upload_validates_images(self):
        self._login(self.staff)
        fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        res = self.client.post("/api/v1/ops/cms/media", {"file": fake})
        self.assertEqual(res.status_code, 400)
        svg = SimpleUploadedFile("x.svg", b"<svg/>", content_type="image/svg+xml")
        res = self.client.post("/api/v1/ops/cms/media", {"file": svg})
        self.assertEqual(res.status_code, 400)

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(buf, format="PNG")
        png = SimpleUploadedFile("x.png", buf.getvalue(), content_type="image/png")
        res = self.client.post("/api/v1/ops/cms/media", {"file": png})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["width"], res.json()["height"]), (4, 3))
