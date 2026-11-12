"""Stock layout/tree and direct one-item adjustment behaviour (#22/#23)."""

import uuid

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from stock import services
from stock.models import Batch, Category, Operation, Product, StockChange, User
from .test_stock import addition


class StockBrowserTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("stock-browser-fixture")
        cls.root = Category.objects.create(name="A parent")
        cls.child = Category.objects.create(name="A child", parent=cls.root)
        cls.empty = Category.objects.create(name="Empty branch", parent=cls.root)

    def setUp(self):
        self.client.force_login(self.user)

    def stocked(self, **kwargs):
        product = services.add(self.user, addition(**kwargs)).product
        product.refresh_from_db()
        return product

    def test_nested_groups_keep_parent_stock_descendants_and_fallback_once(self):
        self.stocked(name="At parent", category=self.root)
        self.stocked(name="At child", category=self.child)
        self.stocked(name="No category", category="uncategorised")
        self.stocked(name="Gone", category=self.empty, quantity=0)
        for layout in ("tiles", "list"):
            response = self.client.get("/", {"view": layout})
            tree = response.context["stock_tree"]
            self.assertEqual([g["name"] for g in tree], ["A parent", "Uncategorised"])
            self.assertEqual([p.name for p in tree[0]["products"]], ["At parent"])
            self.assertEqual([g["name"] for g in tree[0]["children"]], ["A child"])
            self.assertEqual([p.name for p in tree[0]["children"][0]["products"]], ["At child"])
            self.assertContains(response, 'class="stock-item"', count=3)
            self.assertNotContains(response, "Empty branch")
            self.assertContains(response, 'data-tree-storage="prepperdb-stock-collapsed"')

    def test_layout_persists_and_search_retains_only_matching_branches(self):
        self.stocked(name="Find & keep", category=self.child)
        self.stocked(name="Other", category=self.root)
        response = self.client.get("/", {"view": "list", "q": "Find & keep"})
        self.assertContains(response, "data-tree-transient")
        self.assertContains(response, 'value="Find &amp; keep"')
        self.assertNotContains(response, "Tiles")
        self.assertNotContains(response, "Other")
        self.assertEqual(response.context["stock_tree"][0]["products"], [])
        self.assertEqual(self.client.get("/").context["stock_layout"], "list")
        self.assertEqual(self.client.get("/?view=invalid").context["stock_layout"], "list")
        self.assertContains(self.client.get("/?q=absent"), "No matching stock")

    def test_increase_preserves_next_batch_identity_is_replay_safe_and_undoable(self):
        product = self.stocked(quantity=2, effective_date="2028-01-31", precision="month")
        self.stocked(quantity=3, effective_date="2029-02-28", precision="month")
        self.stocked(quantity=4)
        product.refresh_from_db()
        token = uuid.uuid4()
        result = services.increase_one(self.user, token, product.pk, product.revision)
        services.increase_one(self.user, token, product.pk, product.revision)
        batch = Batch.objects.get(pk=result.change.batch_id)
        self.assertEqual((batch.quantity, batch.date, batch.precision), (3, "2028-01-31", "month"))
        self.assertEqual(
            list(product.batches.order_by("date").values_list("quantity", flat=True)), [4, 3, 3]
        )
        with self.assertRaises(ValidationError):
            services.consume_one(self.user, token, product.pk, product.revision)
        with self.assertRaises(ValidationError):
            services.increase_one(self.user, uuid.uuid4(), product.pk, product.revision)
        services.undo(self.user, uuid.uuid4(), result.change_id)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 2)

    def test_increase_undated_use_by_batch_and_reject_empty_or_overflow(self):
        product = self.stocked(date_type="use_by")
        services.increase_one(self.user, uuid.uuid4(), product.pk, product.revision)
        batch = product.batches.get()
        self.assertEqual((batch.quantity, batch.date_type, batch.date), (2, "use_by", ""))
        batch.quantity = 2147483647
        batch.save()
        product.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.increase_one(self.user, uuid.uuid4(), product.pk, product.revision)
        batch.quantity = 0
        batch.save()
        with self.assertRaises(ValidationError):
            services.increase_one(self.user, uuid.uuid4(), product.pk, product.revision)
        self.assertEqual(Operation.objects.count(), 2)

    def test_quick_actions_return_to_layout_and_filter_and_undo_last_item(self):
        product = self.stocked(name="One item")
        path = "/?view=list&q=One"
        result = self.client.post(
            f"/products/{product.pk}/use/",
            {
                "token": uuid.uuid4(),
                "revision": product.revision,
                "return_to": path,
                "confirm_last": "1",
            },
        )
        self.assertRedirects(result, path)
        response = self.client.get(path)
        self.assertNotContains(response, "Undo this change")
        self.assertNotContains(response, "Used one.")
        self.assertContains(self.client.get("/history/"), "Undo this change")
        self.assertContains(response, "No matching stock")
        change_id = self.client.session["last_change"]
        result = self.client.post(
            f"/changes/{change_id}/undo/", {"token": uuid.uuid4(), "return_to": path}
        )
        self.assertRedirects(result, path)
        self.assertContains(self.client.get(path), 'class="stock-item"', count=1)
        product.refresh_from_db()
        result = self.client.post(
            f"/products/{product.pk}/increase/",
            {
                "token": uuid.uuid4(),
                "revision": product.revision,
                "return_to": path,
                "confirm_last": "1",
            },
        )
        self.assertRedirects(result, path)
        self.assertEqual(product.batches.get().quantity, 2)

    def test_quick_actions_require_authentication_post_csrf_and_revision(self):
        product = self.stocked()
        path = f"/products/{product.pk}/increase/"
        self.assertEqual(Client().post(path).status_code, 302)
        self.assertEqual(self.client.get(path).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(path).status_code, 403)
        self.assertEqual(self.client.post(path, {"token": uuid.uuid4()}).status_code, 409)
        self.assertEqual(product.batches.get().quantity, 1)
        self.assertEqual(StockChange.objects.count(), 1)

    def test_return_destination_cannot_redirect_away_from_stock(self):
        product = self.stocked()
        for destination in ("https://example.org/", "//example.org/", "/categories/", "http://["):
            product.refresh_from_db()
            result = self.client.post(
                f"/products/{product.pk}/increase/",
                {"token": uuid.uuid4(), "revision": product.revision, "return_to": destination},
            )
            self.assertRedirects(result, f"/products/{product.pk}/")
        self.assertEqual(Product.objects.count(), 1)
