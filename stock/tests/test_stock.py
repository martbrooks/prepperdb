import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from stock import forms, services
from stock.models import Barcode, Batch, Operation, Product, StockChange, User


def addition(**overrides):
    data = dict(
        token=uuid.uuid4(),
        name="Beans",
        size_value=None,
        size_unit="",
        packaging="",
        category="",
        barcode="",
        variant="",
        date_type="best_before",
        effective_date="",
        precision="",
        quantity=1,
        items_per_pack=1,
    )
    data.update(overrides)
    return data


class StockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("tester", "test-only-password-9876")
        cls.other = User.objects.create_user("other", "test-only-password-9876")

    def test_minimal_entry_is_accepted_and_missing_fields_are_visible(self):
        form = forms.AddForm(
            {
                "token": str(uuid.uuid4()),
                "name": "Beans",
                "quantity": "1",
                "items_per_pack": "1",
                "date_type": "best_before",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        result = services.add(self.user, form.cleaned_data)
        self.assertEqual(result.change.after, 1)
        self.assertIn("date", result.product.missing)
        self.assertIn("size", result.product.missing)
        self.assertIn("image", result.product.missing)
        self.client.force_login(self.user)
        for path in ["/", "/attention/", f"/products/{result.product_id}/"]:
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_date_normalisation_and_precision(self):
        self.assertEqual(forms.normalise_date("02/2028", "best_before"), ("2028-02-29", "month"))
        self.assertEqual(forms.normalise_date("02/2027", "best_before"), ("2027-02-28", "month"))
        self.assertEqual(forms.normalise_date("28/02/2027", "best_before"), ("2027-02-28", "day"))
        self.assertEqual(forms.normalise_date("", "use_by"), ("", ""))
        for date, kind in [
            ("02/2028", "use_by"),
            ("31/02/2028", "best_before"),
            ("13/2028", "best_before"),
            ("2028-02-29", "use_by"),
        ]:
            with self.assertRaises(ValidationError):
                forms.normalise_date(date, kind)

    def test_batch_identity_merging_missing_values_types_and_precision(self):
        first = services.add(self.user, addition())
        services.add(self.user, addition(quantity=2))
        services.add(self.user, addition(date_type="use_by"))
        services.add(self.user, addition(effective_date="2028-02-29", precision="month"))
        services.add(self.user, addition(effective_date="2028-02-29", precision="day"))
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Batch.objects.count(), 4)
        self.assertEqual(Batch.objects.get(pk=first.change.batch_id).quantity, 3)

    def test_multipack_and_replay_add_exactly_once(self):
        data = addition(quantity=2, items_per_pack=6, barcode="123456")
        result = services.add(self.user, data)
        services.add(self.user, data)
        self.assertEqual(Batch.objects.get().quantity, 12)
        self.assertEqual(StockChange.objects.count(), 1)
        self.assertEqual(Barcode.objects.get().product_id, result.product_id)

    def test_use_one_earliest_dated_then_undated_and_undo(self):
        result = services.add(self.user, addition(quantity=4))
        services.add(self.user, addition(effective_date="2020-01-01", precision="day"))
        product = Product.objects.get(pk=result.product_id)
        token = uuid.uuid4()
        used = services.consume_one(self.user, token, product.pk, product.revision)
        services.consume_one(self.user, token, product.pk, product.revision)
        self.assertEqual(used.change.batch.date, "2020-01-01")
        self.assertEqual(used.change.after, 0)
        services.undo(self.user, uuid.uuid4(), used.change_id)
        self.assertEqual(Batch.objects.get(pk=used.change.batch_id).quantity, 1)
        with self.assertRaises(ValidationError):
            services.undo(self.user, uuid.uuid4(), used.change_id)

    def test_undo_rejects_stale_change_and_other_user(self):
        result = services.add(self.user, addition(quantity=4))
        with self.assertRaises(ValidationError):
            services.undo(self.other, uuid.uuid4(), result.change_id)
        services.adjust(self.user, uuid.uuid4(), result.change.batch_id, "consume", 1, 1)
        with self.assertRaises(ValidationError):
            services.undo(self.user, uuid.uuid4(), result.change_id)

    def test_adjustments_constraints_and_stale_revision(self):
        result = services.add(self.user, addition(quantity=4))
        batch_id = result.change.batch_id
        with self.assertRaises(ValidationError):
            services.adjust(self.user, uuid.uuid4(), batch_id, "consume", 5, 1)
        self.assertEqual(Batch.objects.get().quantity, 4)
        discarded = services.adjust(self.user, uuid.uuid4(), batch_id, "discard", 2, 1)
        self.assertEqual(discarded.change.reason, "discard")
        with self.assertRaises(ValidationError):
            services.adjust(self.user, uuid.uuid4(), batch_id, "set", 8, 1)
        token = uuid.uuid4()
        services.adjust(self.user, token, batch_id, "set", 0, 2)
        services.adjust(self.user, token, batch_id, "set", 0, 2)
        self.assertTrue(Product.objects.filter(pk=result.product_id).exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Batch.objects.filter(pk=batch_id).update(quantity=-1)

    def test_undo_addition_retains_catalogue(self):
        result = services.add(self.user, addition())
        services.undo(self.user, uuid.uuid4(), result.change_id)
        self.assertEqual(Batch.objects.get().quantity, 0)
        self.assertTrue(Product.objects.filter(pk=result.product_id).exists())

    def test_operation_token_cannot_be_repurposed_by_other_user_or_action(self):
        data = addition()
        services.add(self.user, data)
        with self.assertRaises(ValidationError):
            services.add(self.other, data)
        with self.assertRaises(ValidationError):
            services.consume_one(self.user, data["token"], 1, 1)

    def test_barcode_conflict_rolls_back_product_creation(self):
        services.add(self.user, addition(barcode="123"))
        with self.assertRaises(ValidationError):
            services.add(self.user, addition(name="Different product", barcode="123"))
        self.assertEqual(Product.objects.count(), 1)

    def test_sizes_are_tied_to_family_and_packaging(self):
        first = services.add(self.user, addition(packaging="tin", size_value=200, size_unit="g"))
        selected = first.product
        second = services.add(
            self.user, addition(name="Ignored", size_value=400, size_unit="g"), selected
        )
        third = services.add(
            self.user, addition(name="Beans", packaging="pouch", size_value=1000, size_unit="g")
        )
        form = forms.AddForm(product=selected)
        choices = dict(form.fields["variant"].choices)
        self.assertIn(str(second.product_id), choices)
        self.assertNotIn(str(third.product_id), choices)
        self.assertEqual(second.product.family, selected.family)

    def test_date_correction_merges_matching_batches_without_losing_quantity(self):
        first = services.add(self.user, addition(quantity=2))
        second = services.add(
            self.user, addition(quantity=3, effective_date="2028-02-29", precision="month")
        )
        services.edit_date(
            self.user,
            uuid.uuid4(),
            first.change.batch_id,
            {
                "revision": 1,
                "date_type": "best_before",
                "effective_date": "2028-02-29",
                "precision": "month",
            },
        )
        self.assertEqual(Batch.objects.get(pk=second.change.batch_id).quantity, 5)
        self.assertEqual(Batch.objects.get(pk=first.change.batch_id).quantity, 0)
        with self.assertRaises(ValidationError):
            services.undo(self.user, uuid.uuid4(), first.change_id)

    def test_deletion_total_cancellation_and_stale_confirmation(self):
        result = services.add(self.user, addition(quantity=6))
        self.client.force_login(self.user)
        path = f"/products/{result.product_id}/delete/"
        response = self.client.get(path)
        self.assertContains(response, "You have 6 items of this in stock.")
        self.client.get(f"/products/{result.product_id}/")  # cancellation is just navigation
        self.assertTrue(Product.objects.filter(pk=result.product_id).exists())
        services.add(self.user, addition(quantity=2))
        response = self.client.post(path, {"token": str(uuid.uuid4()), "revision": 1})
        self.assertContains(response, "You have 8 items of this in stock.")
        self.assertTrue(Product.objects.filter(pk=result.product_id).exists())
        token = uuid.uuid4()
        services.delete(self.user, token, result.product_id, 2)
        services.delete(self.user, token, result.product_id, 2)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Batch.objects.count(), 0)
        self.assertEqual(Barcode.objects.count(), 0)

    def test_replay_after_delete_does_not_resurrect_product(self):
        data = addition()
        result = services.add(self.user, data)
        services.delete(self.user, uuid.uuid4(), result.product_id, 1)
        services.add(self.user, data)
        self.assertEqual(Product.objects.count(), 0)

    def test_local_flows_have_no_internet_dependency_and_escape_output(self):
        self.client.force_login(self.user)
        with patch("socket.create_connection", side_effect=AssertionError("No network allowed")):
            self.client.post(
                "/add/",
                {
                    "token": str(uuid.uuid4()),
                    "name": "<script>Beans</script>",
                    "quantity": 2,
                    "items_per_pack": 1,
                    "date_type": "best_before",
                },
            )
            for path in [
                "/",
                "/attention/",
                "/rotation/",
                "/out-of-stock/",
                "/add/",
                "/products/1/",
                "/products/1/edit/",
                "/batches/1/edit/",
                "/batches/1/adjust/",
            ]:
                self.assertEqual(self.client.get(path).status_code, 200, path)
            self.assertContains(self.client.get("/"), "&lt;script&gt;Beans&lt;/script&gt;")
            self.assertNotContains(self.client.get("/"), "<script>Beans</script>")

    def test_out_of_stock_retains_search_and_replenishment(self):
        result = services.add(self.user, addition(barcode="123456"))
        services.adjust(self.user, uuid.uuid4(), result.change.batch_id, "consume", 1, 1)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/out-of-stock/"), "Beans")
        response = self.client.get("/catalogue/search/?q=123456").json()
        self.assertEqual(response["products"][0]["quantity"], 0)
        services.add(self.user, addition(variant=str(result.product_id)), result.product)
        self.assertEqual(Batch.objects.get().quantity, 1)

    def test_views_sort_all_dated_stock_and_separate_undated(self):
        services.add(
            self.user, addition(name="Later", effective_date="2030-01-01", precision="day")
        )
        services.add(self.user, addition(name="Past", effective_date="2020-01-01", precision="day"))
        services.add(self.user, addition(name="Unknown"))
        self.client.force_login(self.user)
        response = self.client.get("/rotation/")
        self.assertEqual(
            [batch.product.name for batch in response.context["dated"]], ["Past", "Later"]
        )
        self.assertEqual([batch.product.name for batch in response.context["undated"]], ["Unknown"])

    def test_invalid_quantities_and_partial_sizes_are_rejected_server_side(self):
        for invalid in ("-1", "0", "1.5", "1000001", "not-a-number"):
            form = forms.AddForm(
                {
                    "token": str(uuid.uuid4()),
                    "name": "Beans",
                    "quantity": invalid,
                    "items_per_pack": 1,
                    "date_type": "best_before",
                }
            )
            self.assertFalse(form.is_valid(), invalid)
        form = forms.AddForm(
            {
                "token": str(uuid.uuid4()),
                "name": "Beans",
                "quantity": 1,
                "items_per_pack": 1,
                "date_type": "best_before",
                "size_value": "200",
            }
        )
        self.assertFalse(form.is_valid())

    def test_corrections_persist_through_replenishment(self):
        result = services.add(self.user, addition())
        data = dict(
            name="Corrected beans",
            size_value=400,
            size_unit="g",
            packaging="tin",
            category="Household",
            barcode="987654",
            revision=1,
        )
        services.edit_product(self.user, uuid.uuid4(), result.product_id, data)
        selected = Product.objects.get(pk=result.product_id)
        services.add(self.user, addition(variant=str(selected.pk)), selected)
        selected.refresh_from_db()
        self.assertEqual(str(selected.category), "Household")
        self.assertEqual(selected.name, "Corrected beans")
        self.assertEqual(selected.size_value, 400)
        self.assertEqual(Barcode.objects.get().code, "987654")

    def test_undo_replay_has_one_effect(self):
        result = services.add(self.user, addition(quantity=4))
        token = uuid.uuid4()
        services.undo(self.user, token, result.change_id)
        services.undo(self.user, token, result.change_id)
        self.assertEqual(Batch.objects.get().quantity, 0)
        self.assertEqual(StockChange.objects.filter(reason="undo").count(), 1)

    def test_database_rejects_size_unit_without_value(self):
        result = services.add(self.user, addition())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=result.product_id).update(size_unit="g")

    def test_invalid_selected_product_is_a_safe_missing_record(self):
        self.client.force_login(self.user)
        for value in ("bad", "-1", "1.5", "999999999999999999999999999"):
            self.assertEqual(self.client.get("/add/", {"product": value}).status_code, 404)

    def test_full_date_relative_wording_across_month_boundary(self):
        import datetime

        batch = Batch(date_type="best_before", date="2028-05-01", precision="day")
        with patch("django.utils.timezone.localdate", return_value=datetime.date(2028, 4, 30)):
            self.assertEqual(batch.rotation_label, "Best before in 1 day")


class ConcurrentStockTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester")

    def run_threads(self, function):
        def worker(index):
            close_old_connections()
            try:
                return function(index)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_simultaneous_new_additions_merge_without_lost_increments(self):
        self.run_threads(lambda index: services.add(self.user, addition(quantity=index + 1)))
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Batch.objects.count(), 1)
        self.assertEqual(Batch.objects.get().quantity, 3)

    def test_simultaneous_replay_only_applies_once(self):
        data = addition(quantity=3)
        self.run_threads(lambda index: services.add(self.user, data))
        self.assertEqual(Batch.objects.get().quantity, 3)
        self.assertEqual(Operation.objects.count(), 1)

    def test_simultaneous_quick_increases_reject_stale_count(self):
        product = services.add(self.user, addition(quantity=2)).product
        product.refresh_from_db()

        def increase(index):
            try:
                services.increase_one(self.user, uuid.uuid4(), product.pk, product.revision)
                return True
            except ValidationError:
                return False

        self.assertEqual(sorted(self.run_threads(increase)), [False, True])
        self.assertEqual(Batch.objects.get().quantity, 3)

    def test_simultaneous_undo_only_one_succeeds(self):
        result = services.add(self.user, addition(quantity=3))

        def undo(index):
            try:
                services.undo(self.user, uuid.uuid4(), result.change_id)
                return "ok"
            except ValidationError:
                return "conflict"

        self.assertCountEqual(self.run_threads(undo), ["ok", "conflict"])
        self.assertEqual(Batch.objects.get().quantity, 0)
