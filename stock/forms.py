import calendar
import datetime
import re
import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from .models import Product
from .sizes import known_size


class StockDateWidget(forms.MultiWidget):
    template_name = "widgets/stock_date.html"

    def __init__(self, attrs=None):
        year = datetime.date.today().year
        super().__init__(
            [
                forms.Select(
                    attrs={"aria-label": "Day (optional for best before)"},
                    choices=[("", "Month only / unknown day")]
                    + [(str(d), str(d)) for d in range(1, 32)],
                ),
                forms.Select(
                    attrs={"aria-label": "Month"},
                    choices=[("", "Unknown month")]
                    + [(str(m), calendar.month_name[m]) for m in range(1, 13)],
                ),
                forms.Select(
                    attrs={"aria-label": "Year"},
                    choices=[("", "Unknown year")]
                    + [(str(y), str(y)) for y in range(year - 10, year + 31)],
                ),
            ],
            attrs,
        )

    def decompress(self, value):
        if not value:
            return ["", "", ""]
        try:
            parts = value.split("/")
            day, month, year = ("", *parts) if len(parts) == 2 else parts
            day, month = str(int(day)) if day else "", str(int(month))
        except (ValueError, TypeError):
            return ["", "", ""]
        if year not in dict(self.widgets[2].choices):
            self.widgets[2].choices = list(self.widgets[2].choices) + [(year, year)]
        return [day, month, year]

    def value_from_datadict(self, data, files, name):
        if name in data:  # Retain compatibility with existing clients and saved forms.
            return data[name]
        day, month, year = super().value_from_datadict(data, files, name)
        if not any((day, month, year)):
            return ""
        return (
            (str(day).zfill(2) + "/" if day else "")
            + str(month or "").zfill(2)
            + "/"
            + str(year or "")
        )


def normalise_date(value, date_type):
    value = value.strip()
    if not value:
        return "", ""
    try:
        if re.fullmatch(r"\d{2}/\d{4}", value):
            if date_type == "use_by":
                raise ValueError
            month, year = map(int, value.split("/"))
            day = calendar.monthrange(year, month)[1]
            return datetime.date(year, month, day).isoformat(), "month"
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            raise ValueError
        return datetime.datetime.strptime(value, "%d/%m/%Y").date().isoformat(), "day"
    except (ValueError, OverflowError):
        raise ValidationError(
            "Choose a valid date. Use by needs a day; best before can use just a month and year."
        )


class OperationForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class DateForm(OperationForm):
    date_type = forms.ChoiceField(
        choices=[("best_before", "Best before"), ("use_by", "Use by")], initial="best_before"
    )
    date = forms.CharField(
        required=False,
        max_length=10,
        label="Date (if known)",
        help_text="Choose month and year for best before; add a day for a full date. Use by needs a day. Leave all unknown if not known.",
        widget=StockDateWidget(),
    )

    def clean(self):
        data = super().clean()
        if "date_type" in data:
            try:
                data["effective_date"], data["precision"] = normalise_date(
                    data.get("date", ""), data["date_type"]
                )
            except ValidationError as error:
                self.add_error("date", error)
        return data


class CategoryChoiceField(forms.ChoiceField):
    def clean(self, value):
        from .categories import resolve

        if value in ("uncategorised", "Uncategorised"):
            return "uncategorised"
        if value and not str(value).isdigit():
            node = resolve(value)
            if node is None:
                raise ValidationError("Select an existing category or Uncategorised.")
            value = str(node.pk)
        value = super().clean(value)
        try:
            return resolve(value) if value else ""
        except ObjectDoesNotExist:
            raise ValidationError("That category was removed. Reload before saving.") from None


class ProductFields(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .categories import tree

        self.fields["category"].choices = [
            ("", "Choose automatically"),
            ("uncategorised", "Uncategorised"),
        ] + [(str(node.pk), node.path) for node in tree()]

    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    size_value = forms.DecimalField(
        required=False,
        min_value=Decimal("0.001"),
        max_digits=10,
        decimal_places=3,
        label="Individual item size",
    )
    size_unit = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Unknown"),
            ("g", "g"),
            ("kg", "kg"),
            ("ml", "ml"),
            ("l", "l"),
            ("items", "items"),
        ],
    )
    packaging = forms.CharField(
        required=False, max_length=40, help_text="For example: tin, pouch or bottle."
    )
    category = CategoryChoiceField(required=False)
    barcode = forms.RegexField(
        regex=r"^[A-Za-z0-9._-]+$",
        max_length=64,
        required=False,
        help_text="Optional. Type an unknown barcode to remember it.",
    )

    def clean(self):
        data = super().clean()
        if bool(data.get("size_value")) != bool(data.get("size_unit")):
            self.add_error("size_value", "Supply both a size and unit, or leave both unknown.")
        return data


class AddForm(DateForm, ProductFields):
    lookup = forms.UUIDField(required=False, widget=forms.HiddenInput)
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(), required=False, widget=forms.HiddenInput
    )
    variant = forms.ChoiceField(
        choices=[("", "Custom / complete size")], required=False, label="Known size"
    )
    quantity = forms.IntegerField(
        min_value=1, max_value=1000000, initial=1, label="Quantity (packs or individual items)"
    )
    items_per_pack = forms.IntegerField(
        min_value=1,
        max_value=1000000,
        initial=1,
        label="Items per pack",
        help_text="Leave as 1 for individual items. A six-pack adds 6 usable items.",
    )

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected = product
        self.size_suggested = False
        if product:
            variants = Product.objects.filter(family=product.family, packaging=product.packaging)
            self.fields["variant"].choices += [(str(p.pk), p.size_label) for p in variants]
            self.fields["name"].required = False
            self.fields["name"].widget.attrs["readonly"] = True
        if not self.is_bound and product:
            self.initial.update(
                product=product.pk,
                name=product.name,
                variant=str(product.pk),
                packaging=product.packaging,
                category=product.category_id or "uncategorised",
                size_value=product.size_value,
                size_unit=product.size_unit,
            )
            if product.size_value is None:
                hint = known_size(
                    product.barcodes.values_list("code", flat=True), product.packaging
                )
                if hint:
                    self.initial.update(**hint, variant="")
                    self.size_suggested = True
                    self.fields[
                        "size_value"
                    ].help_text = "Suggested known size — check the package before saving."

    def clean(self):
        data = super().clean()
        if not self.selected and not data.get("name"):
            self.add_error("name", "Enter a product name.")
        if self.selected and data.get("product") != self.selected:
            self.add_error("product", "The selected product changed. Reload and try again.")
        if data.get("quantity", 0) * data.get("items_per_pack", 0) > 1000000:
            self.add_error("quantity", "Add at most 1,000,000 individual items at once.")
        return data


class EditProductForm(OperationForm, ProductFields):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [
            (value, label) for value, label in self.fields["category"].choices if value
        ]

    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    photo = forms.FileField(
        required=False,
        label="Product photo",
        help_text="Optional JPG, PNG or WebP, up to 10 MB and 12.5 megapixels. Replaces the current photo.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            return None
        if photo.size > 10 * 1024 * 1024:
            raise ValidationError("Choose a photo under 10 MB.")
        from .images import edit_photo
        from PIL import Image

        try:
            return edit_photo(photo.read())
        except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValidationError(
                "Choose a readable JPG, PNG or WebP under 10 MB and 12.5 megapixels."
            ) from None


class AdjustForm(OperationForm):
    action = forms.ChoiceField(
        choices=[("consume", "Use several"), ("set", "Set quantity"), ("discard", "Discard")]
    )
    amount = forms.IntegerField(min_value=0, max_value=1000000, initial=1, label="Quantity")
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, available, **kwargs):
        super().__init__(*args, **kwargs)
        self.available = available
        self.fields["amount"].widget.attrs["data-available-stock"] = available
        self.fields["amount"].help_text = (
            f"This batch contains {available} items. Use several and Discard cannot exceed this count. "
            "Set quantity records a corrected total."
        )
        if not self.is_bound and available == 0:
            self.initial.update(action="set", amount=0)

    def clean(self):
        data = super().clean()
        if data.get("action") in ("consume", "discard") and data.get("amount") == 0:
            from .services import stock_limit_message

            self.add_error(
                "amount",
                stock_limit_message(0) if self.available == 0 else "Enter at least one item.",
            )
        return data


class RevisionForm(OperationForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class EditBatchForm(DateForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class CategoryForm(OperationForm):
    name = forms.CharField(max_length=80)
    parent = forms.ChoiceField(required=False, label="Parent category")
    revision = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)

    def __init__(self, *args, category=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .categories import tree, branch_ids

        excluded = branch_ids(category.pk) if category else set()
        self.fields["parent"].choices = [("", "Top level")] + [
            (str(node.pk), node.path) for node in tree() if node.pk not in excluded
        ]


class CategoryDeleteForm(OperationForm):
    signature = forms.RegexField(regex=r"^[a-f0-9]{64}$", widget=forms.HiddenInput)


class PhotoForm(RevisionForm):
    photo = forms.FileField(
        required=False,
        label="Product photo",
        help_text="JPG, PNG or WebP, up to 10 MB and 12.5 megapixels. Leave empty to edit the saved photo.",
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    zoom = forms.DecimalField(
        min_value=1,
        max_value=4,
        max_digits=2,
        decimal_places=1,
        initial=1,
        widget=forms.NumberInput(attrs={"type": "range", "min": "1", "max": "4", "step": "0.1"}),
    )
    horizontal = forms.IntegerField(
        min_value=0,
        max_value=100,
        initial=50,
        label="Horizontal crop position",
        widget=forms.NumberInput(attrs={"type": "range", "min": "0", "max": "100"}),
    )
    vertical = forms.IntegerField(
        min_value=0,
        max_value=100,
        initial=50,
        label="Vertical crop position",
        widget=forms.NumberInput(attrs={"type": "range", "min": "0", "max": "100"}),
    )
    rotation = forms.TypedChoiceField(
        choices=[
            (0, "No rotation"),
            (90, "90° clockwise"),
            (180, "180°"),
            (270, "90° anticlockwise"),
        ],
        coerce=int,
        initial=0,
        label="Rotate",
    )

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and photo.size > 10 * 1024 * 1024:
            raise ValidationError("Choose a photo under 10 MB.")
        return photo
