from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
import uuid


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        user = self.model(username=username, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    username = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    USERNAME_FIELD = "username"
    objects = UserManager()


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires = models.DateTimeField(db_index=True)


class Category(models.Model):
    name = models.CharField(max_length=80)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    automatic_key = models.CharField(max_length=30, null=True, blank=True, unique=True)
    revision = models.PositiveIntegerField(default=0)

    @property
    def path(self):
        if hasattr(self, "tree_path"):
            return self.tree_path
        names, seen, current = [], set(), self
        while current and current.pk not in seen:
            seen.add(current.pk)
            names.append(current.name)
            current = current.parent
        return " / ".join(reversed(names))

    def __str__(self):
        return self.path

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"), condition=Q(parent__isnull=True), name="unique_root_category_name"
            ),
            models.UniqueConstraint(
                Lower("name"),
                "parent",
                condition=Q(parent__isnull=False),
                name="unique_child_category_name",
            ),
        ]


class Product(models.Model):
    family = models.UUIDField(db_index=True)
    name = models.CharField(max_length=200)
    size_value = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    size_unit = models.CharField(max_length=12, blank=True)
    packaging = models.CharField(max_length=40, blank=True)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    revision = models.PositiveIntegerField(default=0)
    image = models.ForeignKey(
        "ProductImage", null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    source_url = models.URLField(blank=True, max_length=300)

    @property
    def size_label(self):
        return (
            f"{self.size_value.normalize():f} {self.size_unit}"
            if self.size_value is not None
            else "Size unknown"
        )

    @property
    def missing(self):
        fields = [] if self.image_id else ["image"]
        if self.size_value is None:
            fields.append("size")
        if self.category_id is None:
            fields.append("classification")
        if self.batches.filter(quantity__gt=0, date="").exists():
            fields.append("date")
        return ", ".join(fields)

    @property
    def missing_details(self):
        from django.urls import reverse

        fields = []
        missing_fields = set(self.missing.split(", "))
        for missing, field, label in (
            ("image", "photo", "Add photo"),
            ("size", "size_value", "Add size"),
            ("classification", "category", "Choose category"),
        ):
            if missing in missing_fields:
                fields.append(
                    {
                        "label": label,
                        "url": reverse("edit_photo", args=[self.pk])
                        if missing == "image"
                        else reverse("edit", args=[self.pk]) + "?focus=" + field + "#id_" + field,
                    }
                )
        for batch in self.batches.filter(quantity__gt=0, date="").order_by("pk"):
            fields.append(
                {
                    "label": "Add " + batch.get_date_type_display().lower(),
                    "url": reverse("edit_batch", args=[batch.pk]) + "#id_date_1",
                }
            )
        return fields

    @property
    def next_batch(self):
        return (
            self.batches.filter(quantity__gt=0).exclude(date="").order_by("date", "pk").first()
            or self.batches.filter(quantity__gt=0).order_by("pk").first()
        )

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(size_value__isnull=True, size_unit="")
                | Q(
                    size_value__isnull=False,
                    size_value__gt=0,
                    size_unit__in=["g", "kg", "ml", "l", "items"],
                ),
                name="valid_product_size",
            ),
        ]


class Barcode(models.Model):
    code = models.CharField(max_length=64, unique=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    items_per_pack = models.PositiveIntegerField(default=1)


class ProductImage(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    data = models.BinaryField()
    source_url = models.URLField(max_length=2048, blank=True)
    attribution = models.CharField(max_length=300, blank=True)
    licence = models.CharField(max_length=100, blank=True)


class LookupCache(models.Model):
    key = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20)
    expires = models.DateTimeField(db_index=True)


class LookupResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cache = models.ForeignKey(LookupCache, on_delete=models.CASCADE, related_name="results")
    code = models.CharField(max_length=64)
    details = models.JSONField()
    image = models.ForeignKey(
        ProductImage, null=True, on_delete=models.SET_NULL, related_name="lookup_results"
    )
    image_attempted = models.DateTimeField(null=True)


class ProviderBudget(models.Model):
    kind = models.CharField(max_length=16, unique=True)
    window = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    cooldown = models.DateTimeField(null=True)


class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="batches")
    date_type = models.CharField(
        max_length=12, choices=[("best_before", "Best before"), ("use_by", "Use by")]
    )
    date = models.CharField(max_length=10, blank=True)  # ISO date, '' = explicitly unknown
    precision = models.CharField(
        max_length=5, choices=[("", "Unknown"), ("month", "Month"), ("day", "Day")], blank=True
    )
    quantity = models.IntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "date_type", "date", "precision"], name="batch_identity"
            ),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="nonnegative_quantity"),
            models.CheckConstraint(
                condition=Q(date="", precision="")
                | (Q(precision__in=["month", "day"]) & ~Q(date="")),
                name="date_precision_pair",
            ),
            models.CheckConstraint(
                condition=~Q(date_type="use_by", precision="month"), name="full_use_by_date"
            ),
        ]

    @property
    def date_label(self):
        if not self.date:
            return f"{self.get_date_type_display()} unknown"
        year, month, day = self.date.split("-")
        return f"{self.get_date_type_display()} " + (
            f"{month}/{year}" if self.precision == "month" else f"{day}/{month}/{year}"
        )

    @property
    def rotation_label(self):
        if not self.date:
            return "Date unknown"
        import datetime
        from django.utils import timezone

        today = timezone.localdate()
        effective = datetime.date.fromisoformat(self.date)
        days = (effective - today).days
        label = "use by" if self.date_type == "use_by" else "best before"
        if days == 0:
            return f"{label.capitalize()} today"
        months = abs((effective.year - today.year) * 12 + effective.month - today.month)
        count, unit = (
            (months, "month") if self.precision == "month" and months else (abs(days), "day")
        )
        unit += "" if count == 1 else "s"
        return (
            f"{label.capitalize()} in {count} {unit}"
            if days > 0
            else f"{count} {unit} past {label}"
        )


class StockChange(models.Model):
    batch = models.ForeignKey(Batch, null=True, on_delete=models.SET_NULL, related_name="changes")
    product_name = models.CharField(max_length=200, blank=True)
    product_details = models.CharField(max_length=100, blank=True)
    batch_label = models.CharField(max_length=80, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    reason = models.CharField(max_length=12)
    before = models.IntegerField()
    after = models.IntegerField()
    revision = models.PositiveIntegerField()
    undone = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True, db_index=True)


class Operation(models.Model):
    """Tombstones survive deletion so an old submitted form cannot resurrect data."""

    token = models.UUIDField(unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    kind = models.CharField(max_length=30)
    target = models.CharField(max_length=100)
    change = models.ForeignKey(StockChange, null=True, on_delete=models.SET_NULL)
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
