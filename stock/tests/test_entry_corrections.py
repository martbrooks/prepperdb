from io import BytesIO
import uuid

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from stock import forms, lookup, services
from stock.models import Barcode, Batch, Product, ProductImage, User
from .test_stock import addition


class EntryCorrectionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("corrections", "synthetic-correction-password-9187")

    def setUp(self):
        self.client.force_login(self.user)

    def test_date_selectors_keep_month_precision_and_require_full_use_by(self):
        data = {
            "token": uuid.uuid4(),
            "date_type": "best_before",
            "date_0": "",
            "date_1": "2",
            "date_2": "2028",
        }
        form = forms.DateForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            (form.cleaned_data["effective_date"], form.cleaned_data["precision"]),
            ("2028-02-29", "month"),
        )
        form = forms.DateForm({**data, "date_type": "use_by"})
        self.assertFalse(form.is_valid())
        form = forms.DateForm({**data, "date_type": "use_by", "date_0": "29"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["precision"], "day")
        self.assertFalse(forms.DateForm({**data, "date_0": "31"}).is_valid())
        self.assertEqual(forms.StockDateWidget().decompress("broken"), ["", "", ""])

    def test_corrected_date_hides_empty_source_without_destroying_history(self):
        result = services.add(self.user, addition(quantity=3))
        token = uuid.uuid4()
        data = {
            "revision": 1,
            "date_type": "best_before",
            "effective_date": "2028-02-29",
            "precision": "month",
        }
        services.edit_date(self.user, token, result.change.batch_id, data)
        services.edit_date(self.user, token, result.change.batch_id, data)
        self.assertEqual(Batch.objects.get(pk=result.change.batch_id).quantity, 0)
        response = self.client.get(f"/products/{result.product_id}/")
        self.assertEqual(len(response.context["batches"]), 1)
        self.assertNotContains(response, "Best before unknown")
        self.assertContains(response, "Best before 02/2028")
        with self.assertRaises(ValidationError):
            services.undo(self.user, uuid.uuid4(), result.change_id)

    def test_structured_quantity_and_confirmed_size_fallbacks(self):
        base = {"code": "5449000000996", "product_name": "Sample"}
        result = lookup.details({**base, "product_quantity": 330, "product_quantity_unit": "ml"})
        self.assertEqual((result["size_value"], result["size_unit"]), ("330", "ml"))
        ambiguous = lookup.details(
            {**base, "quantity": "6 pack", "product_quantity": 2400, "product_quantity_unit": "g"}
        )
        self.assertIsNone(ambiguous["size_value"])
        confirmed = lookup.details(
            {"code": "0071106061776", "product_name": "Stagg Classic Chilli Con Carne"}
        )
        self.assertEqual(
            (confirmed["size_value"], confirmed["size_unit"], confirmed["packaging"]),
            ("400", "g", "tin"),
        )

    def test_saved_unknown_size_is_suggested_and_completed_on_same_product(self):
        result = services.add(self.user, addition(name="Stagg", barcode="0071106061776"))
        response = self.client.get(f"/add/?product={result.product_id}&barcode=0071106061776")
        form = response.context["form"]
        self.assertEqual(form.initial["size_value"], "400")
        self.assertTrue(form.size_suggested)
        selected = result.product
        updated = services.add(
            self.user,
            addition(barcode="0071106061776", size_value=400, size_unit="g", packaging="tin"),
            selected,
        )
        self.assertEqual(updated.product_id, result.product_id)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Barcode.objects.get().product_id, result.product_id)
        self.assertEqual(updated.product.size_label, "400 g")
        form = forms.AddForm(product=updated.product)
        self.assertFalse(form.size_suggested)

    def test_missing_details_link_directly_to_editors(self):
        result = services.add(self.user, addition())
        response = self.client.get("/")
        self.assertContains(response, f"/products/{result.product_id}/photo/")
        self.assertContains(
            response, f"/products/{result.product_id}/edit/?focus=size_value#id_size_value"
        )
        self.assertContains(response, f"/batches/{result.change.batch_id}/edit/#id_date_1")

    def test_local_packaging_correction_prevents_incompatible_size_hint(self):
        product = services.add(
            self.user,
            addition(name="Corrected packaging", packaging="pouch", barcode="0071106061776"),
        ).product
        form = forms.AddForm(product=product)
        self.assertFalse(form.size_suggested)
        response = self.client.get(f"/products/{product.pk}/edit/")
        self.assertEqual(response.context["form"].initial["packaging"], "pouch")
        self.assertIsNone(response.context["form"].initial["size_value"])

    def test_custom_photo_validation_replacement_and_preservation(self):
        result = services.add(self.user, addition(barcode="custom-photo"))

        def data():
            product = Product.objects.get(pk=result.product_id)
            return {
                "token": uuid.uuid4(),
                "revision": product.revision,
                "name": product.name,
                "barcode": "custom-photo",
                "size_value": "",
                "size_unit": "",
                "packaging": "",
                "category": "",
            }

        path = f"/products/{result.product_id}/edit/"
        response = self.client.post(
            path,
            {
                **data(),
                "photo": SimpleUploadedFile(
                    "bad.svg", b'<svg onload="alert(1)"/>', content_type="image/svg+xml"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProductImage.objects.count(), 0)
        image = BytesIO()
        Image.new("RGB", (24, 24), "red").save(image, "PNG")
        response = self.client.post(
            path,
            {
                **data(),
                "photo": SimpleUploadedFile(
                    "photo.png", image.getvalue(), content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(pk=result.product_id)
        digest = product.image_id
        self.assertTrue(bytes(product.image.data).startswith(b"\xff\xd8"))
        self.assertEqual(product.image.source_url, "")
        self.client.post(path, data())
        product.refresh_from_db()
        self.assertEqual(product.image_id, digest)
        other = services.add(self.user, addition(name="Other item")).product
        other.image_id = digest
        other.save()
        replacement = BytesIO()
        Image.new("RGB", (24, 24), "blue").save(replacement, "PNG")
        self.client.post(
            path,
            {
                **data(),
                "photo": SimpleUploadedFile(
                    "replacement.png", replacement.getvalue(), content_type="image/png"
                ),
            },
        )
        product.refresh_from_db()
        other.refresh_from_db()
        self.assertNotEqual(product.image_id, digest)
        self.assertEqual(other.image_id, digest)
        self.assertTrue(ProductImage.objects.filter(pk=digest).exists())
        # Replenishment through a saved barcode must retain the custom image.
        replacement_digest = product.image_id
        services.add(self.user, addition(barcode="custom-photo"), product)
        product.refresh_from_db()
        self.assertEqual(product.image_id, replacement_digest)
