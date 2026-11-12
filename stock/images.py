import datetime
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import warnings

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import LookupResult, ProductImage
from .network import ProviderError, fetch
from .lookup import reserve, cool_down


def sanitise(data, *, max_bytes=2 * 1024 * 1024, max_pixels=12000000):
    if len(data) > max_bytes:
        raise ValueError("Image too large.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as source:
            if (
                source.format not in ("JPEG", "PNG", "WEBP")
                or source.width * source.height > max_pixels
            ):
                raise ValueError("Unsupported image.")
            source.load()
            converted = ImageOps.exif_transpose(source).convert("RGB")
            converted.thumbnail((800, 800))
            clean = Image.new("RGB", converted.size, "white")
            clean.paste(converted)
            output = BytesIO()
            clean.save(output, format="JPEG", quality=85)
    return output.getvalue()


def import_result_image(result):
    now = timezone.now()
    if result.image_id or not result.details.get("image_url"):
        return result
    with transaction.atomic():
        current = LookupResult.objects.get(pk=result.pk)
        if current.image_id or (
            current.image_attempted and current.image_attempted > now - datetime.timedelta(hours=1)
        ):
            return current
        current.image_attempted = now
        current.save(update_fields=["image_attempted"])
        if not reserve("image"):
            return current
    try:
        data = sanitise(
            fetch(
                current.details["image_url"],
                hosts={"images.openfoodfacts.org"},
                max_bytes=2 * 1024 * 1024,
                timeout=4,
                image=True,
            )
        )
    except ProviderError as error:
        if error.status in (429, 503):
            cool_down("image", error.retry_after)
        return current
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return current
    with transaction.atomic():
        image, _ = ProductImage.objects.get_or_create(
            digest=hashlib.sha256(data).hexdigest(),
            defaults={
                "data": data,
                "source_url": current.details["image_url"],
                "attribution": "Open Food Facts contributors; resized and re-encoded",
                "licence": "CC BY-SA 3.0",
            },
        )
        LookupResult.objects.filter(pk=current.pk).update(image=image)
        current.image = image
    return current


def materialise(image):
    root = Path(settings.STATIC_ROOT).resolve()
    directory = root / "products"
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(root):
        raise ValueError("Unsafe image cache path.")
    directory.chmod(0o755)
    destination = directory / (image.digest + ".jpg")
    if destination.is_symlink():
        raise ValueError("Unsafe image cache file.")
    if not destination.is_file():
        fd, name = tempfile.mkstemp(prefix=".image-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(bytes(image.data))
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o644)
            os.replace(name, destination)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return settings.STATIC_URL + "products/" + image.digest + ".jpg"


def edit_photo(data, *, zoom=1, horizontal=50, vertical=50, rotation=0):
    """Fit, rotate and crop to an 800px square without changing proportions."""
    import math

    if not math.isfinite(float(zoom)) or not 1 <= zoom <= 4:
        raise ValueError("Choose a zoom between 1 and 4.")
    if not 0 <= horizontal <= 100 or not 0 <= vertical <= 100 or rotation not in (0, 90, 180, 270):
        raise ValueError("Invalid photo position or rotation.")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Image too large.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as source:
            if (
                source.format not in ("JPEG", "PNG", "WEBP")
                or source.width * source.height > 12500000
            ):
                raise ValueError("Unsupported image.")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    if rotation:
        image = image.rotate(-rotation, expand=True)
    # Crop in source coordinates to avoid allocating a huge zoomed bitmap.
    size = max(image.size) / float(zoom)
    left = (image.width - size) * horizontal / 100
    top = (image.height - size) * vertical / 100
    image = image.transform(
        (800, 800),
        Image.Transform.EXTENT,
        (left, top, left + size, top + size),
        resample=Image.Resampling.BICUBIC,
    )
    clean = Image.new("RGB", (800, 800), "white")
    clean.paste(image, mask=image.getchannel("A"))
    output = BytesIO()
    clean.save(output, format="JPEG", quality=85)
    return output.getvalue()


def discard_unused(digest):
    """Remove only orphaned bytes; shared product/lookup images remain valid."""
    if not digest:
        return
    count, _ = ProductImage.objects.filter(
        pk=digest, products__isnull=True, lookup_results__isnull=True
    ).delete()
    if count:

        def remove_cache():
            root = Path(settings.STATIC_ROOT).resolve()
            directory = root / "products"
            if directory.resolve().is_relative_to(root):
                try:
                    (directory / (digest + ".jpg")).unlink(missing_ok=True)
                except OSError:
                    pass  # Disposable cache; authoritative bytes are already removed.

        transaction.on_commit(remove_cache)
