"""Removal limits and independent stocktake corrections (#29)."""

import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase

from stock import services
from stock.models import Batch, Operation, StockChange, User
from .test_stock import addition


class AdjustmentBoundsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("adjustment-bounds-fixture")
        self.client.force_login(self.user)
        self.result = services.add(self.user, addition(quantity=2))
        self.batch = Batch.objects.get(pk=self.result.change.batch_id)
        self.path = f"/batches/{self.batch.pk}/adjust/"

    def data(self, action, amount, **extra):
        return {
            "token": uuid.uuid4(),
            "revision": self.batch.revision,
            "action": action,
            "amount": amount,
            **extra,
        }

    def test_excessive_removals_show_batch_specific_field_error_without_writes(self):
        for action in ("consume", "discard"):
            response = self.client.post(self.path, self.data(action, 14))
            self.assertFormError(
                response.context["form"], "amount", "This batch has only 2 items. Enter 2 or fewer."
            )
            self.assertNotContains(response, "2,147,483,647")
            self.batch.refresh_from_db()
            self.assertEqual(self.batch.quantity, 2)
            self.assertEqual(StockChange.objects.count(), 1)
            self.assertEqual(Operation.objects.count(), 1)
            with self.assertRaisesMessage(ValidationError, "only 2 items"):
                services.adjust(
                    self.user, uuid.uuid4(), self.batch.pk, action, 14, self.batch.revision
                )

    def test_exact_discard_is_recorded_once_and_can_be_undone(self):
        data = self.data("discard", 2)
        self.assertEqual(self.client.post(self.path, data).status_code, 302)
        self.assertEqual(self.client.post(self.path, data).status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 0)
        change = StockChange.objects.get(reason="discard")
        self.assertEqual((change.before, change.after), (2, 0))
        services.undo(self.user, uuid.uuid4(), change.pk)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 2)

    def test_set_quantity_can_increase_or_clear_stock_without_javascript(self):
        self.assertEqual(self.client.post(self.path, self.data("set", 14)).status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 14)
        self.assertEqual(self.client.post(self.path, self.data("set", 0)).status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 0)
        form = self.client.get(self.path).context["form"]
        self.assertEqual(form.initial["action"], "set")
        self.assertEqual(form.initial["amount"], 0)
        response = self.client.post(self.path, self.data("discard", 1))
        self.assertFormError(
            response.context["form"],
            "amount",
            "This batch is empty. Choose Set quantity to record a correction.",
        )

    def test_zero_negative_and_stale_removals_do_not_change_stock(self):
        for action in ("consume", "discard"):
            self.assertFormError(
                self.client.post(self.path, self.data(action, 0)).context["form"],
                "amount",
                "Enter at least one item.",
            )
            self.assertTrue(
                self.client.post(self.path, self.data(action, -1)).context["form"].errors
            )
        self.assertEqual(
            self.client.post(self.path, self.data("discard", 1, revision=0)).status_code, 200
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 2)
        self.assertEqual(StockChange.objects.count(), 1)
