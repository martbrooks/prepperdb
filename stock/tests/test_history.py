"""History retention, durable snapshots, access and undo boundaries (#25)."""

import datetime
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from stock import services
from stock.models import Operation, StockChange, User
from .test_stock import addition


class HistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("history-fixture")
        self.client.force_login(self.user)

    def add(self, **kwargs):
        return services.add(self.user, addition(**kwargs))

    def test_history_requires_login_and_disallows_post(self):
        self.assertEqual(Client().get("/history/").status_code, 302)
        self.assertEqual(self.client.post("/history/").status_code, 405)

    def test_snapshots_survive_product_edit_and_deletion(self):
        result = self.add(
            name="Original soup", quantity=3, effective_date="2028-02-29", precision="month"
        )
        product = result.product
        product.name = "Renamed soup"
        product.save()
        product.refresh_from_db()
        services.delete(self.user, uuid.uuid4(), product.pk, product.revision)
        response = self.client.get("/history/")
        self.assertContains(response, "Original soup")
        self.assertContains(response, "Renamed soup")
        self.assertContains(response, "Best before 02/2028")
        self.assertContains(response, "Item deleted: 3 → 0")
        self.assertNotContains(response, "Undo this change")
        self.assertEqual(StockChange.objects.filter(batch=None).count(), 2)

    def test_retention_boundary_and_replay_tombstones(self):
        now = timezone.now()
        old = self.add(name="Old").change
        boundary = self.add(name="Boundary").change
        recent = self.add(name="Recent").change
        StockChange.objects.filter(pk=old.pk).update(
            created=now - datetime.timedelta(days=32, seconds=1)
        )
        StockChange.objects.filter(pk=boundary.pk).update(created=now - datetime.timedelta(days=32))
        with patch("django.utils.timezone.now", return_value=now):
            call_command("prune_stock_history", stdout=StringIO())
        self.assertFalse(StockChange.objects.filter(pk=old.pk).exists())
        self.assertTrue(StockChange.objects.filter(pk=boundary.pk).exists())
        self.assertTrue(StockChange.objects.filter(pk=recent.pk).exists())
        self.assertEqual(Operation.objects.count(), 3)
        self.assertIsNone(Operation.objects.get(product__name="Old").change_id)

    def test_history_read_and_stock_write_prune_expired_records(self):
        for trigger in (lambda: self.client.get("/history/"), lambda: self.add(name="Next")):
            result = self.add(name=uuid.uuid4().hex)
            StockChange.objects.filter(pk=result.change_id).update(
                created=timezone.now() - datetime.timedelta(days=33)
            )
            trigger()
            self.assertFalse(StockChange.objects.filter(pk=result.change_id).exists())

    def test_undo_eligibility_and_history_redirect(self):
        result = self.add(quantity=3)
        product = result.product
        product.refresh_from_db()
        latest = services.consume_one(self.user, uuid.uuid4(), product.pk, product.revision)
        response = self.client.get("/history/")
        self.assertContains(response, "Undo this change", count=1)
        other = User.objects.create_user("other-fixture")
        self.client.force_login(other)
        self.assertNotContains(self.client.get("/history/"), "Undo this change")
        self.assertEqual(
            self.client.post(
                f"/changes/{latest.change_id}/undo/", {"token": uuid.uuid4()}
            ).status_code,
            409,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            f"/changes/{latest.change_id}/undo/",
            {"token": uuid.uuid4(), "return_to": "/history/?page=1"},
        )
        self.assertRedirects(response, "/history/?page=1")
        self.assertContains(self.client.get("/history/"), "Undone: 2 → 3")
        self.assertNotContains(self.client.get("/"), "Last change:")

    def test_quick_stock_change_is_silent_and_returns_fresh_revision(self):
        result = self.add(quantity=2)
        product = result.product
        product.refresh_from_db()
        response = self.client.post(
            f"/products/{product.pk}/increase/",
            {"token": uuid.uuid4(), "revision": product.revision, "return_to": "/?q=rice"},
            follow=True,
        )
        self.assertNotContains(response, "You can undo")
        self.assertNotContains(response, "Last change:")
        product.refresh_from_db()
        self.assertEqual(product.batches.get().quantity, 3)
        self.assertEqual(product.revision, 2)
