from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("catalog", "0018_content_blocks"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomePage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(default="home", max_length=64, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="HomeSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type", models.CharField(choices=[("hero", "Hero"), ("product_grid", "Product grid"), ("category_grid", "Category grid"), ("rich_text", "Rich text"), ("newsletter", "Newsletter")], max_length=32)),
                ("sort_order", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("home_page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="homebuilder.homepage")),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.CreateModel(
            name="HomePageTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("seo_title", models.CharField(blank=True, default="", max_length=255)),
                ("seo_description", models.TextField(blank=True, default="")),
                ("home_page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="translations", to="homebuilder.homepage")),
            ],
            options={"unique_together": {("home_page", "language_code")}},
        ),
        migrations.CreateModel(
            name="HomeSectionTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("home_section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="translations", to="homebuilder.homesection")),
            ],
            options={"unique_together": {("home_section", "language_code")}},
        ),
        migrations.CreateModel(
            name="HeroSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("home_section", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="hero", to="homebuilder.homesection")),
            ],
        ),
        migrations.CreateModel(
            name="HeroSlide",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sort_order", models.IntegerField(default=0)),
                ("image", models.ImageField(blank=True, null=True, upload_to="home/hero/")),
                ("image_url", models.URLField(blank=True, default="")),
                ("image_alt", models.CharField(blank=True, default="", max_length=255)),
                ("cta_url", models.CharField(blank=True, default="", max_length=500)),
                ("hero_section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="slides", to="homebuilder.herosection")),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.CreateModel(
            name="HeroSlideTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("subtitle", models.CharField(blank=True, default="", max_length=255)),
                ("cta_label", models.CharField(blank=True, default="", max_length=64)),
                ("hero_slide", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="translations", to="homebuilder.heroslide")),
            ],
            options={"unique_together": {("hero_slide", "language_code")}},
        ),
        migrations.CreateModel(
            name="ProductGridSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("limit", models.IntegerField(default=12)),
                ("stock_policy", models.CharField(choices=[("in_stock_first", "In stock first"), ("hide_oos", "Hide out of stock")], default="in_stock_first", max_length=32)),
                ("q", models.CharField(blank=True, default="", max_length=255)),
                ("feature", models.CharField(blank=True, default="", max_length=500)),
                ("option", models.CharField(blank=True, default="", max_length=500)),
                ("sort", models.CharField(blank=True, default="", max_length=32)),
                ("in_stock_only", models.BooleanField(default=False)),
                ("brand", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="catalog.brand")),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="catalog.category")),
                ("home_section", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="product_grid", to="homebuilder.homesection")),
                ("product_group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="catalog.productgroup")),
            ],
        ),
        migrations.CreateModel(
            name="ProductGridPinnedProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sort_order", models.IntegerField(default=0)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="catalog.product")),
                ("product_grid", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pinned", to="homebuilder.productgridsection")),
            ],
            options={"ordering": ["sort_order", "id"], "unique_together": {("product_grid", "product")}},
        ),
        migrations.CreateModel(
            name="CategoryGridSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("limit", models.IntegerField(default=12)),
                ("home_section", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="category_grid", to="homebuilder.homesection")),
                ("root_category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="catalog.category")),
            ],
        ),
        migrations.CreateModel(
            name="CategoryGridPinnedCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sort_order", models.IntegerField(default=0)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="catalog.category")),
                ("category_grid", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pinned", to="homebuilder.categorygridsection")),
            ],
            options={"ordering": ["sort_order", "id"], "unique_together": {("category_grid", "category")}},
        ),
        migrations.CreateModel(
            name="RichTextSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("home_section", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="rich_text", to="homebuilder.homesection")),
            ],
        ),
        migrations.CreateModel(
            name="RichTextSectionTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("markdown", models.TextField(blank=True, default="")),
                ("rich_text_section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="translations", to="homebuilder.richtextsection")),
            ],
            options={"unique_together": {("rich_text_section", "language_code")}},
        ),
        migrations.CreateModel(
            name="NewsletterSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("home_section", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="newsletter", to="homebuilder.homesection")),
            ],
        ),
        migrations.CreateModel(
            name="NewsletterSectionTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=8)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("subtitle", models.CharField(blank=True, default="", max_length=255)),
                ("cta_label", models.CharField(blank=True, default="", max_length=64)),
                ("newsletter_section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="translations", to="homebuilder.newslettersection")),
            ],
            options={"unique_together": {("newsletter_section", "language_code")}},
        ),
        migrations.AddIndex(
            model_name="homepagetranslation",
            index=models.Index(fields=["home_page", "language_code"], name="homebuilder_h_home_pa_5f8cb6_idx"),
        ),
        migrations.AddIndex(
            model_name="homesectiontranslation",
            index=models.Index(fields=["home_section", "language_code"], name="homebuilder_h_home_se_1657aa_idx"),
        ),
        migrations.AddIndex(
            model_name="heroslidetranslation",
            index=models.Index(fields=["hero_slide", "language_code"], name="homebuilder_h_hero_sl_0c9c5b_idx"),
        ),
        migrations.AddIndex(
            model_name="richtextsectiontranslation",
            index=models.Index(fields=["rich_text_section", "language_code"], name="homebuilder_h_rich_te_1bcb08_idx"),
        ),
        migrations.AddIndex(
            model_name="newslettersectiontranslation",
            index=models.Index(fields=["newsletter_section", "language_code"], name="homebuilder_h_news_le_43e0c9_idx"),
        ),
    ]
