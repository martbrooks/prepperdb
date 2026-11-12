import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone
from .models import Barcode, LookupCache, LookupResult, ProductImage, ProviderBudget
from .network import fetch, ProviderError
from .sizes import known_size

FIELDS = "code,product_name,product_name_en,brands,quantity,product_quantity,product_quantity_unit,packaging_tags,categories_tags,image_front_small_url"
MESSAGES = {
    "found": "Check the product details, then add your quantity and date.",
    "not_found": "No online match. Enter a name below; this barcode will be remembered.",
    "unavailable": "Online lookup is unavailable. You can continue manually.",
    "limited": "Online lookup is busy. Try again shortly or continue manually.",
    "pending": "A lookup is already running. Try again shortly or continue manually.",
}


def gtin(code):
    code = re.sub(r"[\s-]", "", code)
    if not re.fullmatch(r"(?:\d{8}|\d{12}|\d{13}|\d{14})", code):
        return None
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(code[:-1])))
    return code if (-total) % 10 == int(code[-1]) else None


def equivalents(code):
    valid = gtin(code)
    if not valid:
        return [code]
    padded = valid.zfill(14)
    return list(
        dict.fromkeys(
            [code] + [padded[-n:] for n in (8, 12, 13, 14) if padded[:-n].strip("0") == ""]
        )
    )


def local_match(code):
    matches = list(Barcode.objects.select_related("product").filter(code__in=equivalents(code)))
    if len({item.product_id for item in matches}) > 1:
        return None  # Conflicting saved aliases must not pick an arbitrary product.
    return matches[0] if matches else None


def reserve(kind):
    now = timezone.now()
    with transaction.atomic():
        budget, _ = ProviderBudget.objects.get_or_create(kind=kind, defaults={"window": now})
        if budget.cooldown and budget.cooldown > now:
            return False
        if now - budget.window >= datetime.timedelta(seconds=60):
            budget.window = now
            budget.count = 0
        limit = {"barcode": 10, "search": 5, "image": 10}[kind]
        if budget.count >= limit:
            return False
        budget.count += 1
        budget.save()
        return True


def cool_down(kind, seconds=60):
    ProviderBudget.objects.filter(kind=kind).update(
        cooldown=timezone.now() + datetime.timedelta(seconds=seconds)
    )


def details(product):
    if not isinstance(product, dict):
        return None
    code = str(product.get("code", ""))
    if not gtin(code):
        return None
    name = str(product.get("product_name_en") or product.get("product_name") or "").strip()
    if not name:
        return None
    brands = product.get("brands", "")
    if isinstance(brands, list):
        brands = brands[0] if brands else ""
    brand = str(brands).split(",")[0].strip()
    if brand and len(brand) < 50 and brand.casefold().split()[0] not in name.casefold():
        name = brand + " " + name
    quantity = str(product.get("quantity", "")).strip()
    # Parse only unambiguous individual sizes and Nx individual-size packs.
    match = re.fullmatch(
        r"(?:(\d{1,4})\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kilograms?|kg|grams?|gms?|g|millilitres?|ml|litres?|l)(?:\s*[e℮])?",
        quantity,
        re.I,
    )
    value = None
    unit = ""
    pack = 1
    pack_prefix = re.match(r"^(\d{1,4})\s*[x×]", quantity)
    if pack_prefix and int(pack_prefix[1]) > 0:
        pack = int(pack_prefix[1])
    if match:
        try:
            size = Decimal(match[2].replace(",", "."))
            if 0 < size < Decimal("10000000") and size.as_tuple().exponent >= -3:
                value = str(size)
                label = match[3].lower()
                unit = (
                    "kg"
                    if label.startswith(("kg", "kilo"))
                    else "g"
                    if label.startswith("g")
                    else "ml"
                    if label.startswith("m")
                    else "l"
                )
        except InvalidOperation:
            pass
    if value is None and pack == 1 and not quantity:
        try:
            size = Decimal(str(product.get("product_quantity", "")))
            label = str(product.get("product_quantity_unit", "")).lower()
            if (
                label in ("g", "kg", "ml", "l")
                and size.is_finite()
                and 0 < size < Decimal("10000000")
                and size.as_tuple().exponent >= -3
            ):
                value, unit = str(size), label
        except InvalidOperation:
            pass
    hint = known_size([code]) if value is None else {}
    if hint:
        value, unit = hint["size_value"], hint["size_unit"]
    tags = set(
        product.get("packaging_tags", []) if isinstance(product.get("packaging_tags"), list) else []
    )
    packaging = next(
        (
            name
            for key, name in [
                ("en:tin", "tin"),
                ("en:can", "tin"),
                ("en:metal-can", "tin"),
                ("en:pouch", "pouch"),
                ("en:bottle", "bottle"),
                ("en:plastic-bottle", "bottle"),
                ("en:glass-jar", "jar"),
            ]
            if key in tags
        ),
        hint.get("packaging", ""),
    )
    categories = " ".join(
        str(tag) for tag in (product.get("categories_tags") or []) if isinstance(tag, str)
    )
    category = "Food & drink"
    if any(word in categories for word in ("canned", "tinned", "preserved-vegetables")):
        category += " / Tinned products"
    elif any(word in categories for word in ("beverages", "drinks")):
        category += " / Drinks"
    elif any(word in categories for word in ("pasta", "rice", "flour", "cereals", "dry-legumes")):
        category += " / Dry products"
    return {
        "code": code,
        "name": name[:200],
        "size_value": value,
        "size_unit": unit,
        "items_per_pack": pack,
        "packaging": packaging,
        "category": category,
        "source_url": "https://world.openfoodfacts.org/product/" + code,
        "image_url": str(product.get("image_front_small_url", ""))[:2048],
        "quantity_label": quantity[:100],
    }


def lookup(query, kind="barcode"):
    now = timezone.now()
    key = (
        "barcode:" + query.zfill(14)
        if kind == "barcode"
        else "search:" + hashlib.sha256(query.casefold().encode()).hexdigest()
    )
    with transaction.atomic():
        LookupCache.objects.filter(expires__lt=now - datetime.timedelta(days=1)).delete()
        # Bounded household cache; results and image references expire with entries.
        excess = list(LookupCache.objects.order_by("-expires").values_list("pk", flat=True)[200:])
        LookupCache.objects.filter(pk__in=excess).delete()
        ProductImage.objects.filter(products__isnull=True, lookup_results__isnull=True).delete()
        cached = LookupCache.objects.filter(key=key, expires__gt=now).first()
        if cached:
            return cached
        if not reserve(kind):
            cached, _ = LookupCache.objects.update_or_create(
                key=key,
                defaults={"status": "limited", "expires": now + datetime.timedelta(seconds=60)},
            )
            cached.results.all().delete()
            return cached
        cached, _ = LookupCache.objects.update_or_create(
            key=key, defaults={"status": "pending", "expires": now + datetime.timedelta(seconds=10)}
        )
        cached.results.all().delete()
    products = []
    status = "unavailable"
    try:
        if kind == "barcode":
            # OFF accepts its normalised 13-digit UPC representation.
            code = query.zfill(13) if len(query) == 12 else query
            if len(code) == 14 and code.startswith("0"):
                code = code[1:]
            url = (
                "https://world.openfoodfacts.org/api/v3/product/"
                + code
                + ".json?"
                + urlencode({"fields": FIELDS})
            )
        else:
            words = " ".join(re.findall(r"[\w'-]+", query)[:12])
            url = "https://search.openfoodfacts.org/search?" + urlencode(
                {
                    "q": words + ' countries_tags:"en:united-kingdom"',
                    "page_size": 6,
                    "fields": FIELDS,
                }
            )
        payload = json.loads(
            fetch(
                url,
                hosts={"world.openfoodfacts.org"}
                if kind == "barcode"
                else {"search.openfoodfacts.org"},
            )
        )
        if not isinstance(payload, dict):
            raise ValueError()
        raw = [payload.get("product")] if kind == "barcode" else payload.get("hits", [])
        if not isinstance(raw, list):
            raise ValueError
        products = [value for item in raw[:6] if (value := details(item))]
        if kind == "barcode":
            products = [p for p in products if p["code"].zfill(14) == query.zfill(14)]
        status = "found" if products else "not_found"
    except ProviderError as error:
        status = "not_found" if error.status == 404 else "unavailable"
        if error.status in (429, 503):
            cool_down(kind, error.retry_after)
            status = "limited"
    except (ValueError, TypeError, KeyError):
        pass
    ttl = (
        datetime.timedelta(days=7)
        if status == "found"
        else datetime.timedelta(hours=1)
        if status == "not_found"
        else datetime.timedelta(seconds=60)
    )
    with transaction.atomic():
        cached.status = status
        cached.expires = timezone.now() + ttl
        cached.save()
        seen = set()
        for product in products:
            if product["code"] not in seen:
                LookupResult.objects.create(cache=cached, code=product["code"], details=product)
                seen.add(product["code"])
    return cached
