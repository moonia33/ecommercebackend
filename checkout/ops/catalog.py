from __future__ import annotations

from django.core.exceptions import ValidationError

from .shared import *


def _bump_listing_and_categories_cache():
    """Bulk `.update()` apeina signalus — rankiniu būdu invaliduojame
    listing'ų ir kategorijų response cache'us."""
    from django.core.cache import cache

    for key in ("catalog:listing:ver", "catalog:categories:ver"):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 2)


def _translation_map(related) -> dict[str, str]:
    out: dict[str, str] = {}
    for tr in related.all():
        code = (getattr(tr, "language_code", "") or "").strip().lower()
        if not code:
            continue
        val = (getattr(tr, "name", "") or getattr(tr, "label", "") or getattr(tr, "value", "") or "")
        if val:
            out[code] = val
    return out


def _serialize_variant(variant: Variant, product_option_types: list[ProductOptionType]) -> OpsCatalogVariantOut:
    assigned: dict[int, OptionValue] = {}
    for vo in variant.option_values.all():
        assigned[int(vo.option_type_id)] = vo.option_value

    options: list[OpsCatalogVariantOptionOut] = []
    for pot in product_option_types:
        ot = pot.option_type
        ov = assigned.get(int(ot.id))
        options.append(
            OpsCatalogVariantOptionOut(
                option_type_id=int(ot.id),
                option_type_code=(ot.code or ""),
                option_type_name=(ot.name or ot.code or ""),
                option_value_id=(int(ov.id) if ov is not None else None),
                label=((ov.label or "") if ov is not None else ""),
            )
        )
    # Options not covered by ProductOptionType (legacy data) are appended too.
    covered = {o.option_type_id for o in options}
    for vo in variant.option_values.all():
        if int(vo.option_type_id) in covered:
            continue
        options.append(
            OpsCatalogVariantOptionOut(
                option_type_id=int(vo.option_type_id),
                option_type_code=(vo.option_type.code or ""),
                option_type_name=(vo.option_type.name or vo.option_type.code or ""),
                option_value_id=int(vo.option_value_id),
                label=(vo.option_value.label or ""),
            )
        )

    return OpsCatalogVariantOut(
        id=int(variant.id),
        sku=(variant.sku or ""),
        barcode=(variant.barcode or ""),
        name=(variant.name or ""),
        price_eur=_to_money_str(variant.price_eur),
        cost_eur=(_to_money_str(variant.cost_eur) if variant.cost_eur is not None else ""),
        is_active=bool(variant.is_active),
        weight_g=int(variant.weight_g or 0),
        length_cm=int(variant.length_cm or 0),
        width_cm=int(variant.width_cm or 0),
        height_cm=int(variant.height_cm or 0),
        options=options,
    )


def _serialize_product_detail(product: Product) -> OpsCatalogProductDetailOut:
    product_option_types = list(product.option_types.select_related("option_type").all())

    variants = [
        _serialize_variant(v, product_option_types)
        for v in product.variants.prefetch_related("option_values__option_type", "option_values__option_value")
    ]

    offers: list[OpsCatalogOfferOut] = []
    for v in product.variants.all():
        for item in v.inventory_items.all():
            warehouse = getattr(item, "warehouse", None)
            offers.append(
                OpsCatalogOfferOut(
                    inventory_item_id=int(item.id),
                    variant_id=int(v.id),
                    variant_sku=(v.sku or ""),
                    warehouse_code=(getattr(warehouse, "code", "") or ""),
                    warehouse_name=(getattr(warehouse, "name", "") or ""),
                    qty_on_hand=int(item.qty_on_hand),
                    qty_reserved=int(item.qty_reserved),
                    qty_available=int(item.qty_available),
                    offer_visibility=(item.offer_visibility or ""),
                    offer_price_override_eur=(
                        _to_money_str(item.offer_price_override_eur)
                        if item.offer_price_override_eur is not None
                        else ""
                    ),
                )
            )

    translations = [
        OpsProductTranslationOut(
            language_code=(t.language_code or ""),
            name=(t.name or ""),
            slug=(t.slug or ""),
            description=(t.description or ""),
            seo_title=(t.seo_title or ""),
            seo_description=(t.seo_description or ""),
            seo_keywords=(t.seo_keywords or ""),
        )
        for t in product.translations.all()
    ]

    features = [
        OpsProductFeatureOut(
            id=int(pfv.id),
            feature_id=int(pfv.feature_id),
            feature_code=(pfv.feature.code or ""),
            feature_name=(pfv.feature.name or pfv.feature.code or ""),
            feature_value_id=int(pfv.feature_value_id),
            value=(pfv.feature_value.value or ""),
        )
        for pfv in product.feature_values.select_related("feature", "feature_value").all()
    ]

    images = [
        OpsProductImageOut(
            id=int(img.id),
            url=(img.url or ""),
            alt_text=(img.alt_text or ""),
            sort_order=int(img.sort_order or 0),
            variant_skus=[(v.sku or "") for v in img.variants.all()],
        )
        for img in product.images.prefetch_related("variants")
    ]

    videos = [
        OpsProductVideoOut(
            id=int(vid.id),
            url=(vid.url or ""),
            video_type=(vid.video_type or "product"),
            language_code=(vid.language_code or ""),
            sort_order=int(vid.sort_order or 0),
        )
        for vid in product.videos.all()
    ]

    return OpsCatalogProductDetailOut(
        id=int(product.id),
        sku=(product.sku or ""),
        name=(product.name or ""),
        slug=(product.slug or ""),
        is_active=bool(product.is_active),
        brand_name=(getattr(product.brand, "name", "") or "") if product.brand_id else "",
        category_name=(getattr(product.category, "name", "") or "") if product.category_id else "",
        variants_count=len(variants),
        description=(product.description or ""),
        thumbnail_url=_product_thumbnail_url(product),
        seo_title=(product.seo_title or ""),
        seo_description=(product.seo_description or ""),
        seo_keywords=(product.seo_keywords or ""),
        additional_info=(product.additional_info or {}),
        brand_id=(int(product.brand_id) if product.brand_id else None),
        category_id=(int(product.category_id) if product.category_id else None),
        group_id=(int(product.group_id) if product.group_id else None),
        tax_class_id=(int(product.tax_class_id) if product.tax_class_id else None),
        option_type_ids=[int(pot.option_type_id) for pot in product_option_types],
        translations=translations,
        features=features,
        images=images,
        videos=videos,
        variants=variants,
        offers=offers,
    )


def _get_product_or_404(product_id: int) -> Product:
    product = (
        Product.objects.select_related("brand", "category", "group", "tax_class")
        .prefetch_related(
            "variants__inventory_items__warehouse",
            "variants__option_values__option_type",
            "variants__option_values__option_value",
            "option_types__option_type",
            "translations",
            "feature_values__feature",
            "feature_values__feature_value",
            "images__variants",
            "videos",
        )
        .filter(id=product_id)
        .first()
    )
    if product is None:
        raise HttpError(404, "Prekė nerasta")
    return product


def _validate_fk_or_400(model, obj_id: int | None, label: str):
    if obj_id is None:
        return None
    obj = model.objects.filter(id=obj_id).first()
    if obj is None:
        raise HttpError(400, f"{label} su ID {obj_id} neegzistuoja")
    return obj


@router.get("/catalog/variants/search", response=list[OpsVariantPickerOut], auth=_auth)
def ops_catalog_variant_search(
    request,
    q: str = "",
    is_active: str = "",
    limit: int = 20,
):
    """Flat variant search for order-line picking: matches variant SKU,
    barcode, variant name, product name/SKU. Prices resolved for the
    request shop (retail channel)."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    limit = max(1, min(int(limit or 20), 50))

    qs = Variant.objects.select_related(
        "product", "product__brand", "product__tax_class",
    ).prefetch_related("product__images")
    q = q.strip()
    if q:
        qs = qs.filter(
            Q(sku__icontains=q)
            | Q(barcode__icontains=q)
            | Q(name__icontains=q)
            | Q(product__name__icontains=q)
            | Q(product__sku__icontains=q)
        )
    if is_active.strip().lower() in {"true", "false"}:
        qs = qs.filter(is_active=is_active.strip().lower() == "true")

    from catalog.shop_pricing import resolve_shop_variant_price
    from checkout.services import get_vat_rate_for, money_from_net
    from catalog.models import InventoryItem

    rows = list(qs.order_by("product__name", "id")[:limit])
    stock = {
        int(r["variant_id"]): int(r["avail"] or 0)
        for r in InventoryItem.objects.filter(
            variant_id__in=[v.id for v in rows],
        ).values("variant_id").annotate(
            avail=Sum(F("qty_on_hand") - F("qty_reserved"))
        )
    }
    out = []
    for v in rows:
        priced = resolve_shop_variant_price(
            variant=v, shop_id=shop_id, channel_type="retail")
        unit_net = getattr(priced, "unit_net", priced) or Decimal("0")
        product = getattr(v, "product", None)
        vat_rate = Decimal("0")
        if product is not None:
            vat_rate = get_vat_rate_for(
                country_code="LT",
                tax_class=getattr(product, "tax_class", None))
        money = money_from_net(
            currency="EUR", unit_net=unit_net, vat_rate=vat_rate, qty=1)
        out.append(OpsVariantPickerOut(
            variant_id=int(v.id),
            sku=v.sku or "",
            barcode=v.barcode or "",
            name=v.name or "",
            product_id=(int(product.id) if product is not None else None),
            product_name=(getattr(product, "name", "") or ""),
            product_slug=(getattr(product, "slug", "") or ""),
            thumbnail_url=_product_thumbnail_url(product),
            is_active=bool(v.is_active),
            unit_net=_to_money_str(money.net),
            unit_gross=_to_money_str(money.gross),
            stock_available=stock.get(int(v.id), 0),
        ))
    return out


@router.get("/catalog/products", response=OpsCatalogProductsOut, auth=_auth)
def ops_catalog_products(
    request,
    q: str = "",
    is_active: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = Product.objects.select_related("brand", "category").prefetch_related("images").annotate(variants_count=Count("variants", distinct=True))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(slug__icontains=q))
    if is_active.strip().lower() in {"true", "false"}:
        qs = qs.filter(is_active=is_active.strip().lower() == "true")

    total_count = qs.count()
    rows = list(qs.order_by("id")[offset : offset + page_size])
    out = [
        OpsCatalogProductOut(
            id=int(p.id),
            sku=(p.sku or ""),
            name=(p.name or ""),
            slug=(p.slug or ""),
            is_active=bool(p.is_active),
            brand_name=(getattr(p.brand, "name", "") or "") if p.brand_id else "",
            category_name=(getattr(p.category, "name", "") or "") if p.category_id else "",
            variants_count=int(getattr(p, "variants_count", 0)),
            thumbnail_url=_product_thumbnail_url(p),
        )
        for p in rows
    ]
    return OpsCatalogProductsOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.get("/catalog/lookups", response=OpsCatalogLookupsOut, auth=_auth)
def ops_catalog_lookups(request):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)

    languages = [OpsLanguageOut(code=c, name=n) for c, n in get_supported_languages()]
    brands = [
        OpsLookupOptionOut(id=int(b.id), name=(b.name or ""), code=(b.slug or ""))
        for b in Brand.objects.filter(is_active=True).order_by("name")
    ]
    categories = [
        OpsLookupOptionOut(
            id=int(c.id),
            name=(c.name or ""),
            code=(c.slug or ""),
            depth=int(getattr(c, "level", 0) or 0),
        )
        for c in Category.objects.filter(is_active=True)
    ]
    groups = [
        OpsLookupOptionOut(id=int(g.id), name=(g.name or ""), code=(g.code or ""))
        for g in ProductGroup.objects.filter(is_active=True).order_by("name")
    ]
    tax_classes = [
        OpsLookupOptionOut(id=int(t.id), name=(t.name or t.code or ""), code=(t.code or ""))
        for t in TaxClass.objects.filter(is_active=True)
    ]
    features = [
        OpsFeatureOptionOut(
            id=int(f.id),
            code=(f.code or ""),
            name=(f.name or f.code or ""),
            allows_multiple=bool(f.allows_multiple),
            is_filterable=bool(f.is_filterable),
            translations=_translation_map(f.translations),
            values=[
                OpsFeatureValueOptionOut(id=int(v.id), value=(v.value or ""))
                for v in f.values.filter(is_active=True)
            ],
        )
        for f in Feature.objects.filter(is_active=True).prefetch_related("values", "translations")
    ]
    option_types = [
        OpsOptionTypeOptionOut(
            id=int(ot.id),
            code=(ot.code or ""),
            name=(ot.name or ot.code or ""),
            translations=_translation_map(ot.translations),
            values=[
                OpsOptionValueOptionOut(
                    id=int(v.id),
                    code=(v.code or ""),
                    label=(v.label or ""),
                    translations=_translation_map(v.translations),
                )
                for v in ot.values.filter(is_active=True)
            ],
        )
        for ot in OptionType.objects.filter(is_active=True).prefetch_related("values__translations", "translations")
    ]

    return OpsCatalogLookupsOut(
        default_language=get_default_language_code(),
        languages=languages,
        brands=brands,
        categories=categories,
        groups=groups,
        tax_classes=tax_classes,
        features=features,
        option_types=option_types,
    )


@router.get("/catalog/products/{product_id}", response=OpsCatalogProductDetailOut, auth=_auth)
def ops_catalog_product_detail(request, product_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    return _serialize_product_detail(_get_product_or_404(product_id))


@router.patch("/catalog/products/{product_id}", response=OpsCatalogProductDetailOut, auth=_auth)
def ops_catalog_update_product(request, product_id: int, payload: OpsProductUpdateIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    product = _get_product_or_404(product_id)
    fields_set = payload.model_fields_set

    with transaction.atomic():
        if "name" in fields_set and payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise HttpError(400, "Pavadinimas negali būti tuščias")
            product.name = name
        if "slug" in fields_set and payload.slug is not None:
            slug = payload.slug.strip()
            if not slug:
                raise HttpError(400, "Slug negali būti tuščias")
            if Product.objects.filter(slug=slug).exclude(id=product.id).exists():
                raise HttpError(400, f"Slug „{slug}“ jau naudojamas kitos prekės")
            product.slug = slug
        if "description" in fields_set and payload.description is not None:
            product.description = payload.description
        if "seo_title" in fields_set and payload.seo_title is not None:
            product.seo_title = payload.seo_title
        if "seo_description" in fields_set and payload.seo_description is not None:
            product.seo_description = payload.seo_description
        if "seo_keywords" in fields_set and payload.seo_keywords is not None:
            product.seo_keywords = payload.seo_keywords
        if "additional_info" in fields_set and payload.additional_info is not None:
            product.additional_info = payload.additional_info
        if "is_active" in fields_set and payload.is_active is not None:
            product.is_active = bool(payload.is_active)

        if "brand_id" in fields_set:
            product.brand = _validate_fk_or_400(Brand, payload.brand_id, "Gamintojas")
        if "category_id" in fields_set:
            product.category = _validate_fk_or_400(Category, payload.category_id, "Kategorija")
        if "group_id" in fields_set:
            product.group = _validate_fk_or_400(ProductGroup, payload.group_id, "Prekių grupė")
        if "tax_class_id" in fields_set:
            product.tax_class = _validate_fk_or_400(TaxClass, payload.tax_class_id, "PVM klasė")

        product.save()

        if payload.translations is not None:
            for tr in payload.translations:
                code = (tr.language_code or "").strip().lower()
                if not code:
                    continue
                slug = (tr.slug or "").strip()
                if slug and (
                    ProductTranslation.objects.filter(language_code=code, slug=slug)
                    .exclude(product=product)
                    .exists()
                ):
                    raise HttpError(400, f"Slug „{slug}“ kalbai „{code}“ jau naudojamas")
                obj, _created = ProductTranslation.objects.get_or_create(
                    product=product, language_code=code
                )
                obj.name = tr.name or ""
                obj.slug = slug
                obj.description = tr.description or ""
                obj.seo_title = tr.seo_title or ""
                obj.seo_description = tr.seo_description or ""
                obj.seo_keywords = tr.seo_keywords or ""
                try:
                    obj.full_clean()
                except ValidationError as exc:
                    raise HttpError(400, "; ".join(exc.messages))
                obj.save()

        if payload.feature_value_ids is not None:
            wanted = list(dict.fromkeys(payload.feature_value_ids))
            values = list(
                FeatureValue.objects.filter(id__in=wanted).select_related("feature")
            )
            if len(values) != len(wanted):
                raise HttpError(400, "Viena ar kelios savybių reikšmės neegzistuoja")
            by_feature: dict[int, list[int]] = {}
            for v in values:
                by_feature.setdefault(int(v.feature_id), []).append(int(v.id))
            for fid, vids in by_feature.items():
                feat = next((v.feature for v in values if int(v.feature_id) == fid), None)
                if feat is not None and not feat.allows_multiple and len(vids) > 1:
                    raise HttpError(
                        400,
                        f"Savybė „{feat.name or feat.code}“ neleidžia kelių reikšmių",
                    )
            existing = {
                int(pfv.feature_value_id): pfv
                for pfv in ProductFeatureValue.objects.filter(product=product)
            }
            ProductFeatureValue.objects.filter(product=product).exclude(
                feature_value_id__in=wanted
            ).delete()
            to_create = [
                ProductFeatureValue(product=product, feature_id=v.feature_id, feature_value_id=v.id)
                for v in values
                if int(v.id) not in existing
            ]
            if to_create:
                ProductFeatureValue.objects.bulk_create(to_create)

    return _serialize_product_detail(_get_product_or_404(product_id))


@router.post("/catalog/products/{product_id}/toggle-active", response=OpsActionResultOut, auth=_auth)
def ops_catalog_toggle_product(request, product_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    product = Product.objects.filter(id=product_id).first()
    if product is None:
        raise HttpError(404, "Product not found")
    product.is_active = not bool(product.is_active)
    product.save(update_fields=["is_active", "updated_at"])
    return OpsActionResultOut(ok=True, message="Product activity toggled", updated_ids=[product.id], failed_ids=[])


@router.patch("/catalog/variants/{variant_id}", response=OpsCatalogVariantOut, auth=_auth)
def ops_catalog_update_variant(request, variant_id: int, payload: OpsVariantUpdateIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    variant = (
        Variant.objects.select_related("product")
        .filter(id=variant_id)
        .first()
    )
    if variant is None:
        raise HttpError(404, "Variantas nerastas")
    fields_set = payload.model_fields_set

    with transaction.atomic():
        if "name" in fields_set and payload.name is not None:
            variant.name = payload.name
        if "barcode" in fields_set:
            barcode = (payload.barcode or "").strip() or None
            if barcode and Variant.objects.filter(barcode=barcode).exclude(id=variant.id).exists():
                raise HttpError(400, f"Barkodas „{barcode}“ jau naudojamas kito varianto")
            variant.barcode = barcode
        if "price_eur" in fields_set and payload.price_eur is not None:
            if payload.price_eur < 0:
                raise HttpError(400, "Kaina negali būti neigiama")
            variant.price_eur = payload.price_eur
        if "cost_eur" in fields_set:
            if payload.cost_eur is not None and payload.cost_eur < 0:
                raise HttpError(400, "Savikaina negali būti neigiama")
            variant.cost_eur = payload.cost_eur
        if "is_active" in fields_set and payload.is_active is not None:
            variant.is_active = bool(payload.is_active)
        for dim in ("weight_g", "length_cm", "width_cm", "height_cm"):
            if dim in fields_set:
                val = getattr(payload, dim)
                if val is not None:
                    if val < 0:
                        raise HttpError(400, "Matmenys/svoris negali būti neigiami")
                    setattr(variant, dim, int(val))
        variant.save()

        if payload.options is not None:
            product = variant.product
            allowed_type_ids = set(
                product.option_types.values_list("option_type_id", flat=True)
            )
            for opt in payload.options:
                otid = int(opt.option_type_id)
                if otid not in allowed_type_ids:
                    ot = OptionType.objects.filter(id=otid).first()
                    if ot is None:
                        raise HttpError(400, f"Opcijos tipas #{otid} neegzistuoja")
                    # Variant has an option the product doesn't declare — link it.
                    ProductOptionType.objects.get_or_create(
                        product=product,
                        option_type=ot,
                        defaults={"sort_order": product.option_types.count()},
                    )
                    allowed_type_ids.add(otid)
                existing = VariantOptionValue.objects.filter(
                    variant=variant, option_type_id=otid
                ).first()
                if opt.option_value_id is None:
                    if existing is not None:
                        existing.delete()
                    continue
                ov = OptionValue.objects.filter(
                    id=opt.option_value_id, option_type_id=otid
                ).first()
                if ov is None:
                    raise HttpError(400, "Opcijos reikšmė nepriklauso šiam opcijos tipui")
                if existing is None:
                    VariantOptionValue.objects.create(
                        variant=variant, option_type_id=otid, option_value=ov
                    )
                else:
                    existing.option_value = ov
                    existing.save(update_fields=["option_value"])

    product_option_types = list(variant.product.option_types.select_related("option_type").all())
    variant = Variant.objects.prefetch_related(
        "option_values__option_type", "option_values__option_value"
    ).get(id=variant.id)
    return _serialize_variant(variant, product_option_types)


@router.post("/catalog/variants/{variant_id}/update-price", response=OpsActionResultOut, auth=_auth)
def ops_catalog_update_variant_price(request, variant_id: int, payload: OpsVariantPriceUpdateIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    variant = Variant.objects.filter(id=variant_id).first()
    if variant is None:
        raise HttpError(404, "Variant not found")
    if payload.price_eur < 0:
        raise HttpError(400, "price_eur must be non-negative")
    variant.price_eur = payload.price_eur
    variant.save(update_fields=["price_eur", "updated_at"])
    return OpsActionResultOut(ok=True, message="Variant price updated", updated_ids=[variant.id], failed_ids=[])


@router.post("/catalog/offers/{inventory_item_id}/set-visibility", response=OpsActionResultOut, auth=_auth)
def ops_catalog_set_offer_visibility(request, inventory_item_id: int, payload: OpsOfferVisibilityIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = InventoryItem.objects.filter(id=inventory_item_id).first()
    if inv is None:
        raise HttpError(404, "Inventory offer not found")
    visibility = (payload.visibility or "").strip().upper()
    if visibility not in {InventoryItem.OfferVisibility.NORMAL, InventoryItem.OfferVisibility.OUTLET}:
        raise HttpError(400, "visibility must be NORMAL or OUTLET")
    inv.offer_visibility = visibility
    inv.save(update_fields=["offer_visibility", "updated_at"])
    return OpsActionResultOut(ok=True, message="Offer visibility updated", updated_ids=[inv.id], failed_ids=[])


@router.post("/catalog/features/{feature_id}/values", response=OpsFeatureValueOptionOut, auth=_auth)
def ops_catalog_create_feature_value(request, feature_id: int, payload: OpsFeatureValueCreateIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    feature = Feature.objects.filter(id=feature_id).first()
    if feature is None:
        raise HttpError(404, "Savybė nerasta")
    value = (payload.value or "").strip()
    if not value:
        raise HttpError(400, "Reikšmė negali būti tuščia")
    existing = FeatureValue.objects.filter(feature=feature, value__iexact=value).first()
    if existing is not None:
        return OpsFeatureValueOptionOut(id=int(existing.id), value=(existing.value or ""))
    fv = FeatureValue.objects.create(feature=feature, value=value)
    return OpsFeatureValueOptionOut(id=int(fv.id), value=fv.value)


@router.post("/catalog/bulk-actions", response=OpsActionResultOut, auth=_auth)
def ops_catalog_bulk_actions(request, payload: OpsCatalogBulkActionIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    action = (payload.action or "").strip().lower()
    if action not in {"activate_products", "deactivate_products", "assign_category"}:
        raise HttpError(400, "Unsupported catalog bulk action")
    if not payload.product_ids:
        raise HttpError(400, "product_ids cannot be empty")

    if action == "assign_category":
        category = None
        if payload.category_id is not None:
            category = Category.objects.filter(id=int(payload.category_id)).first()
            if category is None:
                raise HttpError(404, "Category not found")
            if not category.is_active:
                raise HttpError(400, "Category is not active")
        # category_id=None -> nuimti kategoriją
        updated = list(Product.objects.filter(id__in=payload.product_ids).values_list("id", flat=True))
        Product.objects.filter(id__in=updated).update(category=category, updated_at=timezone.now())
        _bump_listing_and_categories_cache()
        return OpsActionResultOut(ok=True, message="Category assigned", updated_ids=[int(x) for x in updated], failed_ids=[])

    value = action == "activate_products"
    updated = list(Product.objects.filter(id__in=payload.product_ids).values_list("id", flat=True))
    Product.objects.filter(id__in=updated).update(is_active=value, updated_at=timezone.now())
    _bump_listing_and_categories_cache()
    return OpsActionResultOut(ok=True, message="Catalog bulk action applied", updated_ids=[int(x) for x in updated], failed_ids=[])
