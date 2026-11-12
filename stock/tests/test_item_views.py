"""Inventory row semantics across stock, attention, zero-stock and rotation (#27/#28)."""

import hashlib
from io import BytesIO
import uuid

from django.test import TestCase
from PIL import Image

from stock import services
from stock.models import Product, ProductImage, User
from .test_stock import addition


class ItemViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("item-view-fixture")
        self.client.force_login(self.user)
        buffer = BytesIO()
        Image.new("RGB", (20, 30), "green").save(buffer, "JPEG")
        data = buffer.getvalue()
        self.image = ProductImage.objects.create(digest=hashlib.sha256(data).hexdigest(), data=data)

    def add(self, **kwargs):
        result = services.add(self.user, addition(**kwargs))
        result.product.refresh_from_db()
        return result

    def test_rotation_keeps_per_batch_quantities_order_precision_and_images(self):
        late = self.add(name="Shared", quantity=5, effective_date="2030-02-28", precision="month")
        early = self.add(
            name="Shared",
            quantity=2,
            effective_date="2020-01-01",
            precision="day",
            date_type="use_by",
        )
        middle = self.add(name="Middle", quantity=3, effective_date="2029-01-31", precision="month")
        late.product.image = self.image
        late.product.save(update_fields=["image"])
        response = self.client.get("/rotation/")
        self.assertEqual(
            [b.pk for b in response.context["dated"]],
            [early.change.batch_id, middle.change.batch_id, late.change.batch_id],
        )
        self.assertContains(response, f"/images/{self.image.pk}/", count=2)
        self.assertContains(response, "placeholder.svg")
        self.assertContains(response, "Use by 01/01/2020")
        self.assertContains(response, "Best before 02/2030")
        self.assertContains(response, "<strong>2</strong>", html=True)
        self.assertContains(response, "<strong>5</strong>", html=True)
        self.assertNotContains(response, "<strong>7</strong>", html=True)
        self.assertContains(response, f"/batches/{early.change.batch_id}/adjust/")
        self.assertNotContains(response, f"/products/{late.product_id}/use/")

    def test_undated_rotation_has_image_and_batch_specific_date_correction(self):
        result = self.add()
        result.product.image = self.image
        result.product.save()
        response = self.client.get("/rotation/")
        self.assertContains(response, f"/images/{self.image.pk}/")
        self.assertContains(response, f"/batches/{result.change.batch_id}/edit/")
        self.assertContains(response, "Best before unknown")
        self.assertEqual(list(response.context["dated"]), [])
        self.assertEqual([b.pk for b in response.context["undated"]], [result.change.batch_id])

    def test_attention_quick_actions_keep_view_and_zero_stock_offers_replenishment(self):
        result = self.add(name="Attention item")
        path = f"/products/{result.product_id}/use/"
        response = self.client.post(
            path,
            {
                "token": uuid.uuid4(),
                "revision": result.product.revision,
                "return_to": "/attention/",
                "confirm_last": "1",
            },
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [("/attention/", 302)])
        self.assertNotContains(response, "Used one.")
        self.assertContains(response, "No stock remaining")
        self.assertContains(response, f"/add/?product={result.product_id}")
        self.assertNotContains(response, path)
        self.assertNotContains(response, f"/products/{result.product_id}/increase/")
        self.assertContains(self.client.get("/out-of-stock/"), f"/add/?product={result.product_id}")

    def test_using_only_undated_batch_can_resolve_attention_then_undo_restores_it(self):
        result = self.add(
            name="Only date missing", size_value=400, size_unit="g", category="Household"
        )
        product = result.product
        product.image = self.image
        product.save()
        self.assertEqual(product.missing, "date")
        response = self.client.post(
            f"/products/{product.pk}/use/",
            {
                "token": uuid.uuid4(),
                "revision": product.revision,
                "return_to": "/attention/",
                "confirm_last": "1",
            },
            follow=True,
        )
        self.assertContains(response, "Nothing needs attention")
        self.assertNotContains(response, product.name)
        from stock.models import StockChange

        change = StockChange.objects.filter(reason="consume").get()
        self.client.post(
            f"/changes/{change.pk}/undo/", {"token": uuid.uuid4(), "return_to": "/attention/"}
        )
        self.assertContains(self.client.get("/attention/"), product.name)

    def test_attention_and_out_of_stock_keep_photos_and_missing_detail_links(self):
        result = self.add(name="Saved image item", quantity=0)
        result.product.image = self.image
        result.product.save()
        for path in ("/attention/", "/out-of-stock/"):
            response = self.client.get(path)
            self.assertContains(response, f"/images/{self.image.pk}/")
            self.assertContains(
                response, f"/products/{result.product_id}/edit/?focus=size_value#id_size_value"
            )
            self.assertContains(response, "data-category-explorer")
            self.assertNotContains(response, 'class="card"')
        complete = self.add(
            name="Complete item",
            size_value=400,
            size_unit="g",
            category="Household",
            effective_date="2030-01-31",
            precision="month",
        ).product
        complete.image = self.image
        complete.save()
        self.assertNotContains(self.client.get("/attention/"), complete.name)

    def test_attention_delete_shortcut_is_only_for_zero_stock(self):
        empty = self.add(name="Empty item", quantity=0).product
        stocked = self.add(name="Stocked item", quantity=1).product
        path = f"/products/{empty.pk}/delete/"
        response = self.client.get("/attention/")
        self.assertContains(response, f"{path}?return_to=/attention/")
        self.assertContains(response, 'aria-label="Delete Empty item permanently"')
        self.assertContains(response, "icons/24/delete-item.png")
        self.assertNotContains(response, f"/products/{stocked.pk}/delete/")
        self.assertNotContains(self.client.get("/out-of-stock/"), path)
        response = self.client.get(path, {"return_to": "/attention/"})
        self.assertContains(response, "You have 0 items of this in stock.")
        self.assertContains(response, 'href="/attention/"')
        self.client.get(response.context["cancel_to"])
        self.assertTrue(Product.objects.filter(pk=empty.pk).exists())
        data = {"token": uuid.uuid4(), "revision": empty.revision, "return_to": "/attention/"}
        for _ in range(2):
            self.assertRedirects(self.client.post(path, data), "/attention/")
        self.assertFalse(Product.objects.filter(pk=empty.pk).exists())
        self.assertNotContains(self.client.get("/out-of-stock/"), empty.name)
        self.assertEqual(
            self.client.get("/catalogue/search/", {"q": empty.name}).json()["products"], []
        )

    def test_attention_delete_retains_return_path_after_stale_confirmation(self):
        product = self.add(quantity=0).product
        path = f"/products/{product.pk}/delete/"
        self.client.get(path, {"return_to": "/attention/"})
        services.add(self.user, addition(quantity=3))
        response = self.client.post(
            path, {"token": uuid.uuid4(), "revision": product.revision, "return_to": "/attention/"}
        )
        self.assertContains(response, "You have 3 items of this in stock.")
        self.assertContains(response, 'href="/attention/"')
        self.assertContains(response, 'name="return_to" value="/attention/"')
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_deletion_return_path_cannot_leave_the_stock_views(self):
        product = self.add(quantity=0).product
        path = f"/products/{product.pk}/delete/"
        for destination in ("https://example.org/", "//example.org/", "/categories/", "http://["):
            response = self.client.get(path, {"return_to": destination})
            self.assertEqual(response.context["cancel_to"], f"/products/{product.pk}/")
            self.assertEqual(response.context["return_to"], "/")
        self.assertRedirects(
            self.client.post(
                path,
                {
                    "token": uuid.uuid4(),
                    "revision": product.revision,
                    "return_to": "https://example.org/",
                },
            ),
            "/",
        )

    def test_empty_views_have_specific_explanations(self):
        self.assertContains(self.client.get("/attention/"), "Nothing needs attention")
        self.assertContains(self.client.get("/out-of-stock/"), "No out-of-stock items")
        self.assertContains(self.client.get("/rotation/"), "No dated stock")
        self.assertContains(self.client.get("/rotation/"), "No undated stock")
