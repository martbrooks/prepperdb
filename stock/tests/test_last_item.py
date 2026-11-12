"""Last-item consumption confirmation, including ordinary form submissions (#31)."""

import uuid

from django.test import TestCase

from stock import services
from stock.models import Operation, StockChange, User
from .test_stock import addition


class LastItemTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("last-item-fixture")
        self.client.force_login(self.user)
        self.product = services.add(self.user, addition(name="Last tin & soup")).product
        self.product.refresh_from_db()
        self.path = f"/products/{self.product.pk}/use/"
        self.data = {
            "token": uuid.uuid4(),
            "revision": self.product.revision,
            "return_to": "/?q=Last",
        }

    def test_unconfirmed_submission_and_cancel_leave_stock_and_history_unchanged(self):
        operations = Operation.objects.count()
        changes = StockChange.objects.count()
        response = self.client.post(self.path, self.data)
        self.assertTemplateUsed(response, "confirm_last.html")
        self.assertContains(response, "Last tin &amp; soup")
        self.assertContains(response, 'href="/?q=Last"')
        self.assertEqual(str(response.context["form"]["token"].value()), str(self.data["token"]))
        self.client.get(response.context["return_to"])
        self.assertEqual(self.product.batches.get().quantity, 1)
        self.assertEqual(Operation.objects.count(), operations)
        self.assertEqual(StockChange.objects.count(), changes)
        for path in ("/", "/attention/", f"/products/{self.product.pk}/"):
            self.assertContains(self.client.get(path), 'data-last-item="Last tin &amp; soup"')

    def test_confirmed_submission_is_replay_safe_and_undoable(self):
        self.client.post(self.path, self.data)
        data = {**self.data, "confirm_last": "1"}
        for _ in range(2):
            self.assertRedirects(self.client.post(self.path, data), "/?q=Last")
        self.assertEqual(self.product.batches.get().quantity, 0)
        change = StockChange.objects.get(reason="consume")
        services.undo(self.user, uuid.uuid4(), change.pk)
        self.assertEqual(self.product.batches.get().quantity, 1)

    def test_confirmation_cannot_apply_to_changed_stock(self):
        self.client.post(self.path, self.data)
        services.increase_one(self.user, uuid.uuid4(), self.product.pk, self.product.revision)
        response = self.client.post(self.path, {**self.data, "confirm_last": "1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.product.batches.get().quantity, 2)
        self.assertFalse(StockChange.objects.filter(reason="consume").exists())

    def test_batch_exhaustion_does_not_warn_while_other_stock_remains(self):
        services.add(
            self.user,
            addition(name=self.product.name, effective_date="2028-01-31", precision="month"),
        )
        self.product.refresh_from_db()
        self.data["revision"] = self.product.revision
        self.assertNotContains(self.client.get("/"), "data-last-item")
        self.assertRedirects(self.client.post(self.path, self.data), "/?q=Last")
        self.assertEqual(sum(self.product.batches.values_list("quantity", flat=True)), 1)
        self.product.refresh_from_db()
        response = self.client.post(
            self.path, {**self.data, "token": uuid.uuid4(), "revision": self.product.revision}
        )
        self.assertTemplateUsed(response, "confirm_last.html")

    def test_confirmation_rejects_unsafe_return_destination(self):
        response = self.client.post(self.path, {**self.data, "return_to": "https://example.org/"})
        self.assertEqual(response.context["return_to"], f"/products/{self.product.pk}/")
