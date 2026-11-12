"""Image editing correctness, security, sharing and deletion (#26)."""

from io import BytesIO
from pathlib import Path
import tempfile
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from stock import images, services
from stock.models import Product, ProductImage, User
from .test_stock import addition


def picture():
    image = Image.new("RGB", (200, 100), "red")
    image.paste("blue", (100, 0, 200, 100))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class PhotoPixelsTests(TestCase):
    def test_square_fit_preserves_full_photo_and_crop_position(self):
        image = Image.open(BytesIO(images.edit_photo(picture())))
        self.assertEqual(image.size, (800, 800))
        self.assertEqual(image.getpixel((400, 20)), (255, 255, 255))
        self.assertGreater(image.getpixel((50, 400))[0], 240)
        self.assertGreater(image.getpixel((750, 400))[2], 240)
        left = Image.open(BytesIO(images.edit_photo(picture(), zoom=2, horizontal=0)))
        right = Image.open(BytesIO(images.edit_photo(picture(), zoom=2, horizontal=100)))
        self.assertGreater(left.getpixel((400, 400))[0], 240)
        self.assertGreater(right.getpixel((400, 400))[2], 240)

    def test_rotation_transparency_exif_and_metadata(self):
        rotated = Image.open(BytesIO(images.edit_photo(picture(), rotation=90)))
        self.assertGreater(rotated.getpixel((400, 50))[0], 240)
        self.assertGreater(rotated.getpixel((400, 750))[2], 240)
        source = Image.new("RGBA", (50, 100), (0, 0, 0, 0))
        source.paste((0, 255, 0, 255), (10, 10, 40, 90))
        buffer = BytesIO()
        source.save(buffer, "PNG")
        clean = Image.open(BytesIO(images.edit_photo(buffer.getvalue())))
        self.assertEqual(clean.getpixel((1, 1)), (255, 255, 255))
        oriented = Image.new("RGB", (200, 100), "red")
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "Private metadata"
        buffer = BytesIO()
        oriented.save(buffer, "JPEG", exif=exif)
        clean = Image.open(BytesIO(images.edit_photo(buffer.getvalue())))
        self.assertEqual(clean.getpixel((20, 400)), (255, 255, 255))
        self.assertEqual(dict(clean.getexif()), {})

    def test_invalid_input_bounds_and_pixel_limit(self):
        for options in (
            {"zoom": 0},
            {"zoom": float("nan")},
            {"horizontal": 101},
            {"vertical": -1},
            {"rotation": 45},
        ):
            with self.assertRaises(ValueError):
                images.edit_photo(picture(), **options)
        with self.assertRaises(ValueError):
            images.edit_photo(b"x" * (10 * 1024 * 1024 + 1))
        buffer = BytesIO()
        Image.new("RGB", (4000, 4000)).save(buffer, "PNG")
        with self.assertRaises(ValueError):
            images.edit_photo(buffer.getvalue())


class PhotoManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("photo-fixture")
        self.client.force_login(self.user)
        self.product = services.add(
            self.user, addition(name="Photo item", quantity=3, barcode="photo-fixture")
        ).product
        self.product.refresh_from_db()
        self.path = f"/products/{self.product.pk}/photo/"
        self.remove = self.path + "remove/"

    def data(self, **kwargs):
        self.product.refresh_from_db()
        return {
            "token": uuid.uuid4(),
            "revision": self.product.revision,
            "zoom": "1.0",
            "horizontal": 50,
            "vertical": 50,
            "rotation": "0",
            **kwargs,
        }

    def upload(self, **kwargs):
        response = self.client.post(
            self.path,
            self.data(photo=SimpleUploadedFile("photo.png", picture(), "image/png"), **kwargs),
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        return self.product.image_id

    def test_authentication_csrf_methods_and_missing_photo(self):
        for path in (self.path, self.remove):
            self.assertEqual(Client().get(path).status_code, 302)
            self.assertEqual(self.client.put(path).status_code, 405)
            strict = Client(enforce_csrf_checks=True)
            strict.force_login(self.user)
            self.assertEqual(strict.post(path, {}).status_code, 403)
        self.assertContains(self.client.post(self.path, self.data()), "Choose a photo to add")
        self.assertIsNone(Product.objects.get(pk=self.product.pk).image_id)

    def test_upload_edit_replay_and_stale_form_leave_stock_unchanged(self):
        digest = self.upload()
        data = self.data(zoom="2.0", horizontal=100)
        self.assertEqual(self.client.post(self.path, data).status_code, 302)
        self.product.refresh_from_db()
        new = self.product.image_id
        self.assertNotEqual(digest, new)
        self.assertFalse(ProductImage.objects.filter(pk=digest).exists())
        self.assertEqual(self.client.post(self.path, data).status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_id, new)
        stale = {**data, "token": uuid.uuid4()}
        self.assertContains(self.client.post(self.path, stale), "This product changed")
        self.assertEqual(self.product.batches.get().quantity, 3)
        self.assertEqual(self.product.barcodes.get().code, "photo-fixture")

    def test_invalid_upload_or_controls_preserve_existing_photo(self):
        digest = self.upload()
        for data in (
            self.data(
                photo=SimpleUploadedFile("bad.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")
            ),
            self.data(zoom="NaN"),
            self.data(horizontal=101),
            self.data(rotation=45),
        ):
            self.assertEqual(self.client.post(self.path, data).status_code, 200)
            self.product.refresh_from_db()
            self.assertEqual(self.product.image_id, digest)

    def test_remove_confirmation_cancel_and_orphan_cache_cleanup(self):
        digest = self.upload()
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            images.materialise(self.product.image)
            cache = Path(root) / "products" / (digest + ".jpg")
            self.assertTrue(cache.exists())
            response = self.client.get(self.remove)
            self.assertContains(response, "Remove photo?")
            self.assertContains(response, "Cancel")
            self.product.refresh_from_db()
            self.assertEqual(self.product.image_id, digest)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(self.remove, self.data()).status_code, 302)
            self.product.refresh_from_db()
            self.assertIsNone(self.product.image_id)
            self.assertFalse(ProductImage.objects.filter(pk=digest).exists())
            self.assertFalse(cache.exists())
            self.assertEqual(self.client.get(f"/images/{digest}/").status_code, 404)
        self.assertEqual(self.product.batches.get().quantity, 3)
        self.assertContains(self.client.get(f"/products/{self.product.pk}/"), "No product image")
        self.assertContains(self.client.get("/"), "Add photo")

    def test_shared_image_and_cache_survive_edit_and_removal(self):
        digest = self.upload()
        other = services.add(self.user, addition(name="Shared photo item")).product
        other.image_id = digest
        other.save()
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            images.materialise(self.product.image)
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.remove, self.data())
            other.refresh_from_db()
            self.assertEqual(other.image_id, digest)
            self.assertTrue(ProductImage.objects.filter(pk=digest).exists())
            self.assertTrue((Path(root) / "products" / (digest + ".jpg")).exists())

    def test_provider_attribution_is_retained_when_editing(self):
        digest = self.upload()
        ProductImage.objects.filter(pk=digest).update(
            source_url="https://images.openfoodfacts.org/example.jpg",
            attribution="Open Food Facts contributors",
            licence="CC BY-SA 3.0",
        )
        self.client.post(self.path, self.data(zoom="2.0"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.licence, "CC BY-SA 3.0")
        self.assertIn("Open Food Facts", self.product.image.attribution)
        self.assertTrue(self.product.image.source_url)

    def test_removal_replay_cannot_remove_a_later_replacement(self):
        self.upload()
        data = self.data()
        self.assertEqual(self.client.post(self.remove, data).status_code, 302)
        digest = self.upload(zoom="2.0")
        self.assertEqual(self.client.post(self.remove, data).status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_id, digest)
        self.assertContains(
            self.client.post(self.remove, {**data, "token": uuid.uuid4()}), "This product changed"
        )

    def test_removal_keeps_images_referenced_by_provider_lookup_cache(self):
        from django.utils import timezone
        from stock.models import LookupCache, LookupResult

        digest = self.upload()
        cache = LookupCache.objects.create(
            key="image-reference", status="found", expires=timezone.now()
        )
        LookupResult.objects.create(cache=cache, code="fixture", details={}, image_id=digest)
        self.assertEqual(self.client.post(self.remove, self.data()).status_code, 302)
        self.assertTrue(ProductImage.objects.filter(pk=digest).exists())
