import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from .models import Barcode, Batch, Category, LookupResult, Operation, Product, StockChange


def classify(name, packaging="", parent=""):
    from .categories import resolve

    if isinstance(parent, Category):
        return resolve(parent)
    if parent in ("uncategorised", "Uncategorised"):
        return None
    if parent and parent not in ("Food & drink", "Food & drink / Drinks"):
        return resolve(parent)
    import re

    words = re.split(r"[\W_]+", name.casefold())
    key = None
    if parent == "Food & drink / Drinks" or any(
        w in words for w in ("water", "juice", "tea", "coffee", "cola", "lemonade", "drinks")
    ):
        key = (
            "canned_drinks"
            if packaging.casefold() == "tin"
            else "bottled_drinks"
            if packaging.casefold() == "bottle"
            else "drinks"
        )
    elif packaging.casefold() == "tin" or any(w in words for w in ("tinned", "canned")):
        key = "tinned"
    elif any(w in words for w in ("rice", "pasta", "oats", "flour", "lentils")):
        key = "dry"
    return Category.objects.filter(automatic_key=key).first() if key else resolve(parent)


def operation(user, token, kind, target):
    existing = Operation.objects.filter(token=token).first()
    if existing and (
        existing.user_id != user.pk or existing.kind != kind or existing.target != str(target)
    ):
        raise ValidationError(
            "This form was already used for another action. Reload and try again."
        )
    return existing


def record(user, token, kind, target, product, change=None):
    return Operation.objects.create(
        user=user, token=token, kind=kind, target=str(target), product=product, change=change
    )


def barcode(product, code, items_per_pack=None):
    if not code:
        return
    from .lookup import equivalents

    if Barcode.objects.filter(code__in=equivalents(code)).exclude(product=product).exists():
        raise ValidationError(
            "That barcode identifies another saved product. Select that product first."
        )
    entry, _ = Barcode.objects.get_or_create(
        code=code, defaults={"product": product, "items_per_pack": items_per_pack or 1}
    )
    if entry.product_id != product.pk:
        raise ValidationError(
            "That barcode belongs to another saved product. Select that product or correct its barcode first."
        )
    if items_per_pack is not None and entry.items_per_pack != items_per_pack:
        entry.items_per_pack = items_per_pack
        entry.save(update_fields=["items_per_pack"])


def prune_history():
    from datetime import timedelta
    from django.utils import timezone

    return StockChange.objects.filter(created__lt=timezone.now() - timedelta(days=32)).delete()


def change_quantity(batch, user, reason, after):
    if not 0 <= after <= 2147483647:
        raise ValidationError("Quantity must be between 0 and 2,147,483,647.")
    prune_history()
    batch.product.refresh_from_db()
    change = StockChange.objects.create(
        batch=batch,
        product_name=batch.product.name,
        product_details=f"{batch.product.size_label} {batch.product.packaging}".strip(),
        batch_label=batch.date_label,
        user=user,
        reason=reason,
        before=batch.quantity,
        after=after,
        revision=batch.revision + 1,
    )
    batch.quantity = after
    batch.revision += 1
    batch.save(update_fields=["quantity", "revision"])
    Product.objects.filter(pk=batch.product_id).update(revision=F("revision") + 1)
    return change


@transaction.atomic
def add(user, data, selected=None):
    target = selected.pk if selected else "new"
    previous = operation(user, data["token"], "add", target)
    if previous:
        return previous
    if selected:
        selected = Product.objects.get(pk=selected.pk)
        if not data.get("variant") and selected.size_value is None and data.get("size_value"):
            # Completing an unknown size keeps its stock and barcode on the same
            # product. Choosing another size for a known product remains a variant.
            selected.size_value = data["size_value"]
            selected.size_unit = data["size_unit"]
            if not selected.packaging:
                selected.packaging = data.get("packaging", "")
            selected.full_clean()
            selected.save(update_fields=["size_value", "size_unit", "packaging"])
            data = {**data, "variant": str(selected.pk)}
    variant = data.get("variant")
    if selected and variant:
        product = Product.objects.get(
            pk=variant, family=selected.family, packaging=selected.packaging
        )
    else:
        identity = dict(
            name=selected.name if selected else data["name"],
            size_value=data.get("size_value"),
            size_unit=data.get("size_unit", ""),
            packaging=data.get("packaging", ""),
        )
        if selected:
            identity.update(family=selected.family, packaging=selected.packaging)
        product = Product.objects.filter(**identity).first()
        if not product:
            family = uuid.uuid4()
            if data.get("lookup") and identity["packaging"] and not selected:
                families = list(
                    Product.objects.filter(name=identity["name"], packaging=identity["packaging"])
                    .values_list("family", flat=True)
                    .distinct()
                )
                if len(families) == 1:
                    family = families[0]
            product = Product.objects.create(
                **identity,
                **({} if selected else {"family": family}),
                category=selected.category
                if selected
                else classify(identity["name"], identity["packaging"], data.get("category", "")),
            )
    barcode(product, data.get("barcode", ""), data["items_per_pack"])
    if data.get("lookup") and not product.source_url and product.revision == 0:
        imported = LookupResult.objects.filter(
            pk=data["lookup"], code=data.get("barcode", "")
        ).first()
        if imported:
            product.source_url = imported.details["source_url"]
            if not product.image_id:
                product.image_id = imported.image_id
            product.save(update_fields=["source_url", "image"])
    batch, _ = Batch.objects.get_or_create(
        product=product,
        date_type=data["date_type"],
        date=data["effective_date"],
        precision=data["precision"],
    )
    change = change_quantity(
        batch, user, "add", batch.quantity + data["quantity"] * data["items_per_pack"]
    )
    return record(user, data["token"], "add", target, product, change)


def stock_limit_message(quantity):
    if quantity == 0:
        return "This batch is empty. Choose Set quantity to record a correction."
    unit = "item" if quantity == 1 else "items"
    return f"This batch has only {quantity} {unit}. Enter {quantity} or fewer."


@transaction.atomic
def adjust(user, token, batch_id, action, amount, revision):
    previous = operation(user, token, "adjust", batch_id)
    if previous:
        return previous
    batch = Batch.objects.get(pk=batch_id)
    if batch.revision != revision:
        raise ValidationError(
            "Stock changed since this page opened. Reload and check the quantity."
        )
    if action not in ("consume", "set", "discard") or amount < (0 if action == "set" else 1):
        raise ValidationError("Invalid stock adjustment.")
    if action != "set" and amount > batch.quantity:
        raise ValidationError(stock_limit_message(batch.quantity), code="stock_limit")
    after = amount if action == "set" else batch.quantity - amount
    change = change_quantity(batch, user, action, after)
    return record(user, token, "adjust", batch_id, batch.product, change)


@transaction.atomic
def change_one(user, token, product_id, revision, increase=False):
    kind = "increase_one" if increase else "use_one"
    previous = operation(user, token, kind, product_id)
    if previous:
        return previous
    product = Product.objects.get(pk=product_id)
    if product.revision != revision:
        raise ValidationError("Stock changed. Reload and check the next batch before continuing.")
    batch = product.next_batch
    if not batch:
        raise ValidationError("This product is out of stock. Use Add stock to choose its date.")
    change = change_quantity(
        batch, user, "add" if increase else "consume", batch.quantity + (1 if increase else -1)
    )
    return record(user, token, kind, product_id, product, change)


def consume_one(user, token, product_id, revision):
    return change_one(user, token, product_id, revision)


def increase_one(user, token, product_id, revision):
    return change_one(user, token, product_id, revision, increase=True)


@transaction.atomic
def undo(user, token, change_id):
    previous = operation(user, token, "undo", change_id)
    if previous:
        return previous
    prune_history()
    change = StockChange.objects.select_related("batch").get(pk=change_id)
    if change.user_id != user.pk:
        raise ValidationError("Only your own stock changes can be undone.")
    batch = change.batch
    if (
        not batch
        or change.undone
        or change.reason in ("undo", "delete")
        or batch.revision != change.revision
    ):
        raise ValidationError(
            "This change cannot be undone because a later change was made or it was already undone."
        )
    reverse = change_quantity(batch, user, "undo", change.before)
    change.undone = True
    change.save(update_fields=["undone"])
    return record(user, token, "undo", change_id, batch.product, reverse)


@transaction.atomic
def edit_product(user, token, product_id, data):
    previous = operation(user, token, "edit", product_id)
    if previous:
        return previous
    product = Product.objects.get(pk=product_id)
    if product.revision != data["revision"]:
        raise ValidationError("This product changed. Reload before correcting it.")
    for field in ("name", "size_value", "size_unit", "packaging"):
        setattr(product, field, data[field])
    from .categories import resolve

    product.category = resolve(data.get("category"))
    product.revision += 1
    old_digest = product.image_id
    if data.get("photo"):
        import hashlib
        from .models import ProductImage

        product.image, _ = ProductImage.objects.get_or_create(
            digest=hashlib.sha256(data["photo"]).hexdigest(),
            defaults={"data": data["photo"], "attribution": "Custom household photo"},
        )
    product.full_clean()
    product.save()
    if old_digest != product.image_id:
        from .images import discard_unused

        discard_unused(old_digest)
    # An explicit blank removes associations; replacement remains transactional.
    product.barcodes.exclude(code=data.get("barcode", "")).delete()
    barcode(product, data.get("barcode", ""))
    return record(user, token, "edit", product_id, product)


@transaction.atomic
def edit_date(user, token, batch_id, data):
    previous = operation(user, token, "date", batch_id)
    if previous:
        return previous
    source = Batch.objects.get(pk=batch_id)
    if source.revision != data["revision"]:
        raise ValidationError("This batch changed. Reload before correcting its date.")
    target, _ = Batch.objects.get_or_create(
        product=source.product,
        date_type=data["date_type"],
        date=data["effective_date"],
        precision=data["precision"],
    )
    if target.pk != source.pk:
        if target.quantity + source.quantity > 2147483647:
            raise ValidationError("Merged quantity is too large.")
        target.quantity += source.quantity
        source.quantity = 0
        target.revision += 1
        source.revision += 1
        target.save()
        source.save()
    else:
        source.revision += 1
        source.save(update_fields=["revision"])
    Product.objects.filter(pk=source.product_id).update(revision=F("revision") + 1)
    return record(user, token, "date", batch_id, source.product)


@transaction.atomic
def delete(user, token, product_id, revision):
    previous = operation(user, token, "delete", product_id)
    if previous:
        return previous
    product = Product.objects.get(pk=product_id)
    if product.revision != revision:
        raise ValidationError("Stock changed. Check the updated total and confirm again.")
    result = record(user, token, "delete", product_id, product)
    for batch in product.batches.filter(quantity__gt=0):
        change_quantity(batch, user, "delete", 0)
    product.delete()
    result.product = None
    return result


@transaction.atomic
def set_photo(user, token, product_id, revision, data=None, source=None, remove=False):
    target = f"{product_id}:{'remove' if remove else 'save'}"
    previous = operation(user, token, "photo", target)
    if previous:
        return previous
    product = Product.objects.get(pk=product_id)
    if product.revision != revision:
        raise ValidationError("This product changed. Reload before changing its photo.")
    from . import images
    from .models import ProductImage
    import hashlib

    old_digest = product.image_id
    if remove:
        product.image = None
    else:
        product.image, _ = ProductImage.objects.get_or_create(
            digest=hashlib.sha256(data).hexdigest(),
            defaults={
                "data": data,
                "source_url": source.source_url if source else "",
                "attribution": (source.attribution + "; edited locally")[:300]
                if source and source.source_url
                else "Custom household photo",
                "licence": source.licence if source else "",
            },
        )
    product.revision += 1
    product.save(update_fields=["image", "revision"])
    result = record(user, token, "photo", target, product)
    if old_digest != product.image_id:
        images.discard_unused(old_digest)
    return result
