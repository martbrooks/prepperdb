import datetime
import hashlib
from io import BytesIO
from pathlib import Path
import socket
import tempfile
import uuid
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from stock import images, lookup, network, services
from stock.models import (
    Barcode,
    Batch,
    LookupCache,
    LookupResult,
    Product,
    ProductImage,
    ProviderBudget,
    User,
)

CODE = "5449000000996"
PRODUCT = {
    "code": CODE,
    "product_name": "Soup",
    "brands": "Sample",
    "quantity": "6 x 400 g",
    "packaging_tags": ["en:tin"],
    "categories_tags": ["en:canned-foods"],
}


def payload(product=None):
    import json

    return json.dumps({"status": "success", "product": product or PRODUCT}).encode()


def jpeg(colour="white"):
    stream = BytesIO()
    Image.new("RGB", (20, 20), colour).save(stream, "JPEG")
    return images.sanitise(stream.getvalue())


class LookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("lookup-test", "synthetic-lookup-password-9187")

    def setUp(self):
        self.client.force_login(self.user)

    def test_known_code_checksums_and_upc_aliases(self):
        for value in (CODE, "036000291452", "0036000291452", "96385074"):
            self.assertEqual(lookup.gtin(value), value)
        for value in ("5449000000997", "123", "http://localhost/x", "000000000000X"):
            self.assertIsNone(lookup.gtin(value))
        self.assertIn("036000291452", lookup.equivalents("0036000291452"))

    @patch("stock.lookup.fetch", return_value=payload())
    def test_positive_cache_and_confirmation_save_multipack(self, fetch):
        response = self.client.post("/catalogue/lookup/", {"barcode": CODE})
        result = LookupResult.objects.get()
        self.assertRedirects(response, "/add/?lookup=" + str(result.pk))
        form = self.client.get(response.url).context["form"]
        self.assertEqual(form.initial["name"], "Sample Soup")
        self.assertEqual(form.initial["size_value"], "400")
        self.assertEqual(form.initial["items_per_pack"], 6)
        data = {
            "token": str(uuid.uuid4()),
            "lookup": str(result.pk),
            "barcode": CODE,
            "name": "Corrected soup",
            "quantity": 2,
            "items_per_pack": 6,
            "size_value": 400,
            "size_unit": "g",
            "packaging": "tin",
            "category": "Food & drink / Tinned products",
            "date_type": "best_before",
            "date": "02/2028",
        }
        self.assertEqual(self.client.post("/add/", data).status_code, 302)
        product = Product.objects.get()
        self.assertEqual(Batch.objects.get().quantity, 12)
        self.assertEqual(product.name, "Corrected soup")
        self.assertIn("/product/" + CODE, product.source_url)
        self.assertEqual(Barcode.objects.get().items_per_pack, 6)
        # A saved match must not call the provider or overwrite user corrections.
        response = self.client.post("/catalogue/lookup/", {"barcode": CODE})
        self.assertIn("product=" + str(product.pk), response.url)
        self.assertEqual(self.client.get(response.url).context["form"].initial["items_per_pack"], 6)
        self.assertEqual(fetch.call_count, 1)

    def test_saved_upc_found_without_external_request(self):
        product = Product.objects.create(family=uuid.uuid4(), name="Saved item")
        Barcode.objects.create(product=product, code="036000291452", items_per_pack=4)
        with patch("stock.lookup.fetch", side_effect=AssertionError("Network should not run")):
            response = self.client.post("/catalogue/lookup/", {"barcode": "0036000291452"})
        self.assertIn("product=" + str(product.pk), response.url)
        self.assertIn("barcode=036000291452", response.url)

    @patch("stock.lookup.fetch", side_effect=network.ProviderError(404))
    def test_unknown_barcode_is_cached_and_preserved_for_manual_entry(self, fetch):
        for _ in range(2):
            response = self.client.post("/catalogue/lookup/", {"barcode": CODE}, follow=True)
            self.assertEqual(response.context["form"].initial["barcode"], CODE)
            self.assertContains(response, "No online match")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(LookupCache.objects.get().status, "not_found")

    def test_invalid_code_still_allows_manual_saved_association(self):
        with patch("stock.lookup.fetch", side_effect=AssertionError):
            response = self.client.post("/catalogue/lookup/", {"barcode": "HOUSE-001"}, follow=True)
            self.assertEqual(response.context["form"].initial["barcode"], "HOUSE-001")
            self.client.post(
                "/add/",
                {
                    "token": str(uuid.uuid4()),
                    "name": "Emergency item",
                    "barcode": "HOUSE-001",
                    "quantity": 1,
                    "items_per_pack": 1,
                    "date_type": "best_before",
                },
            )
            saved = self.client.post("/catalogue/lookup/", {"barcode": "HOUSE-001"})
            self.assertIn("product=", saved.url)

    def test_lookup_requires_login_csrf_and_post(self):
        self.assertEqual(Client().post("/catalogue/lookup/", {"barcode": CODE}).status_code, 302)
        self.assertEqual(self.client.get("/catalogue/lookup/").status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post("/catalogue/lookup/", {"barcode": CODE}).status_code, 403)

    def test_limits_cooldown_and_recovery(self):
        for status in (429, 503):
            ProviderBudget.objects.all().delete()
            LookupCache.objects.all().delete()
            with patch(
                "stock.lookup.fetch", side_effect=network.ProviderError(status, 120)
            ) as fetch:
                self.assertEqual(lookup.lookup(CODE).status, "limited")
                self.assertEqual(lookup.lookup("036000291452").status, "limited")
                self.assertEqual(fetch.call_count, 1)
            ProviderBudget.objects.update(cooldown=timezone.now() - datetime.timedelta(seconds=1))
            LookupCache.objects.update(expires=timezone.now() - datetime.timedelta(seconds=1))
            with patch("stock.lookup.fetch", return_value=payload()):
                self.assertEqual(lookup.lookup(CODE).status, "found")

    def test_budget_is_global_and_pending_query_is_not_duplicated(self):
        self.assertTrue(all(lookup.reserve("barcode") for _ in range(10)))
        self.assertFalse(lookup.reserve("barcode"))
        cached = LookupCache.objects.create(
            key="barcode:" + CODE.zfill(14),
            status="pending",
            expires=timezone.now() + datetime.timedelta(seconds=10),
        )
        with patch("stock.lookup.fetch", side_effect=AssertionError):
            self.assertEqual(lookup.lookup(CODE).pk, cached.pk)

    def test_explicit_name_search_is_bounded_and_cache_reused(self):
        import json

        with patch(
            "stock.lookup.fetch", return_value=json.dumps({"hits": [PRODUCT]}).encode()
        ) as fetch:
            for _ in range(2):
                response = self.client.post("/catalogue/lookup/", {"query": "sample soup"})
                self.assertContains(response, "Sample Soup")
            self.assertEqual(fetch.call_count, 1)
            self.assertIn("page_size=6", fetch.call_args.args[0])

    def test_provider_fields_and_ambiguous_size_are_not_blindly_used(self):
        value = lookup.details({**PRODUCT, "quantity": "4 pack of tins"})
        self.assertIsNone(value["size_value"])
        self.assertEqual(value["size_unit"], "")
        self.assertEqual(value["items_per_pack"], 1)
        self.assertEqual(lookup.details(PRODUCT)["category"], "Food & drink / Tinned products")
        self.assertIsNone(lookup.details({"code": CODE}))

    def test_barcode_edit_preserves_pack_metadata_and_alias_conflict_rolls_back(self):
        product = Product.objects.create(family=uuid.uuid4(), name="Original")
        Barcode.objects.create(product=product, code="036000291452", items_per_pack=6)
        services.barcode(product, "036000291452")
        self.assertEqual(Barcode.objects.get().items_per_pack, 6)
        other = Product.objects.create(family=uuid.uuid4(), name="Other")
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            services.barcode(other, "0036000291452")


class ImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("image-test")
        data = jpeg()
        cls.image = ProductImage.objects.create(digest=hashlib.sha256(data).hexdigest(), data=data)

    def test_reencode_resize_and_strip_metadata(self):
        raw = BytesIO()
        exif = Image.Exif()
        exif[0x010E] = "Private camera metadata"
        Image.new("RGB", (1600, 1200), "blue").save(raw, "JPEG", exif=exif)
        with Image.open(BytesIO(images.sanitise(raw.getvalue()))) as result:
            self.assertLessEqual(max(result.size), 800)
            self.assertFalse(result.getexif())
            self.assertEqual(result.format, "JPEG")
        for invalid in (b'<svg onload="alert(1)"></svg>', b"x" * (2 * 1024 * 1024 + 1)):
            with self.assertRaises((ValueError, OSError)):
                images.sanitise(invalid)

    def test_decoded_pixel_limit(self):
        raw = BytesIO()
        Image.new("RGB", (4000, 4000)).save(raw, "PNG")
        with self.assertRaises(ValueError):
            images.sanitise(raw.getvalue())

    def test_authenticated_regeneration_and_anonymous_boundary(self):
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=Path(root)):
            path = "/images/" + self.image.pk + "/"
            self.assertEqual(Client().get(path).status_code, 302)
            self.assertFalse((Path(root) / "products").exists())
            self.client.force_login(self.user)
            self.assertRedirects(
                self.client.get(path),
                "/static/products/" + self.image.pk + ".jpg",
                fetch_redirect_response=False,
            )
            cache = Path(root) / "products" / (self.image.pk + ".jpg")
            self.assertEqual(cache.read_bytes(), bytes(self.image.data))
            cache.unlink()
            self.client.get(path)
            self.assertTrue(cache.exists())

    def test_concurrent_atomic_materialisation(self):
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=Path(root)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                urls = list(pool.map(lambda _: images.materialise(self.image), range(2)))
            self.assertEqual(urls[0], urls[1])
            self.assertEqual(len(list((Path(root) / "products").iterdir())), 1)

    def test_shared_image_survives_one_product_deletion_and_cache_gc(self):
        first = Product.objects.create(family=uuid.uuid4(), name="First", image=self.image)
        second = Product.objects.create(family=uuid.uuid4(), name="Second", image=self.image)
        first.delete()
        with patch("stock.lookup.fetch", side_effect=network.ProviderError(404)):
            lookup.lookup(CODE)
        self.assertTrue(ProductImage.objects.filter(pk=second.image_id).exists())

    def test_symlink_directory_escape_is_rejected(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as outside,
            override_settings(STATIC_ROOT=Path(root)),
        ):
            (Path(root) / "products").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                images.materialise(self.image)

    def test_remote_image_import_is_local_and_records_attribution(self):
        cache = LookupCache.objects.create(
            key="test", status="found", expires=timezone.now() + datetime.timedelta(days=1)
        )
        item = LookupResult.objects.create(
            cache=cache,
            code=CODE,
            details={
                **lookup.details(PRODUCT),
                "image_url": "https://images.openfoodfacts.org/images/products/a.jpg",
            },
        )
        with patch("stock.images.fetch", return_value=jpeg("blue")) as fetch:
            result = images.import_result_image(item)
            images.import_result_image(result)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result.image.licence, "CC BY-SA 3.0")
        self.assertIn("contributors", result.image.attribution)


class NetworkTests(SimpleTestCase):
    def test_disallowed_urls_are_rejected_before_resolution(self):
        for url in (
            "http://images.openfoodfacts.org/images/products/x",
            "https://localhost/images/products/x",
            "https://images.openfoodfacts.org:444/images/products/x",
            "https://user@images.openfoodfacts.org/images/products/x",
            "https://images.openfoodfacts.org/admin",
        ):
            with patch("stock.network.DNS.submit", side_effect=AssertionError):
                with self.assertRaises(network.ProviderError):
                    network.fetch(url, hosts={"images.openfoodfacts.org"}, image=True)

    def test_private_and_mixed_dns_answers_are_rejected(self):
        for addresses in (["127.0.0.1"], ["169.254.169.254"], ["8.8.8.8", "10.0.0.1"]):
            future = MagicMock()
            future.result.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
            ]
            with (
                patch("stock.network.DNS.submit", return_value=future),
                patch("stock.network.socket.create_connection") as connect,
            ):
                with self.assertRaises(network.ProviderError):
                    network.fetch(
                        "https://images.openfoodfacts.org/images/products/x.jpg",
                        hosts={"images.openfoodfacts.org"},
                        image=True,
                    )
                connect.assert_not_called()

    def simulate(self, raw, max_bytes=1024):
        class FakeSocket:
            def settimeout(self, value):
                pass

            def sendall(self, data):
                pass

            def makefile(self, *args, **kwargs):
                return BytesIO(raw)

            def close(self):
                pass

            def shutdown(self, *args):
                pass

        future = MagicMock()
        future.result.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        context = MagicMock()
        context.wrap_socket.return_value = FakeSocket()
        with (
            patch("stock.network.DNS.submit", return_value=future),
            patch("stock.network.socket.create_connection") as connect,
            patch("stock.network.ssl.create_default_context", return_value=context),
        ):
            result = network.fetch(
                "https://images.openfoodfacts.org/images/products/x.jpg",
                hosts={"images.openfoodfacts.org"},
                image=True,
                max_bytes=max_bytes,
            )
            self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 443))
            self.assertEqual(
                context.wrap_socket.call_args.kwargs["server_hostname"], "images.openfoodfacts.org"
            )
            return result

    def test_connections_are_pinned_and_body_bounded(self):
        self.assertEqual(self.simulate(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"), b"abc")
        with self.assertRaises(network.ProviderError):
            self.simulate(b"HTTP/1.1 200 OK\r\nContent-Length: 2000\r\n\r\nabc")
        with self.assertRaises(network.ProviderError):
            self.simulate(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b"x" * 2000)

    def test_redirect_to_internal_host_is_rejected(self):
        with self.assertRaises(network.ProviderError):
            self.simulate(
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/secret\r\nContent-Length: 0\r\n\r\n"
            )
