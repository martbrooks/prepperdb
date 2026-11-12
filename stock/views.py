import datetime
import hashlib
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import OperationalError, transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.forms import IntegerField, UUIDField
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from . import categories
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from . import forms, services, images
from . import lookup as provider
from .models import Batch, LoginAttempt, LookupResult, Product, ProductImage, StockChange


def reserve_login(request, username):
    now = timezone.now()
    keys = [
        hashlib.sha256(value.encode()).hexdigest()
        for value in (
            "ip:" + request.META.get("REMOTE_ADDR", ""),
            "user:" + username.casefold(),
        )
    ]
    with transaction.atomic():
        LoginAttempt.objects.filter(expires__lte=now).delete()
        if LoginAttempt.objects.count() > 10000:
            return False
        rows = [
            LoginAttempt.objects.get_or_create(
                key=key,
                defaults={
                    "expires": now + datetime.timedelta(seconds=settings.LOGIN_WINDOW_SECONDS)
                },
            )[0]
            for key in keys
        ]
        if any(row.count >= settings.LOGIN_LIMIT for row in rows):
            return False
        for row in rows:
            row.count += 1
            row.save(update_fields=["count"])
    return True


@login_not_required
@require_http_methods(["GET", "POST"])
def sign_in(request):
    form = AuthenticationForm(request, data=request.POST if request.method == "POST" else None)
    throttled = False
    error_message = ""
    if request.method == "POST":
        username = request.POST.get("username", "")[:150].strip()
        try:
            allowed = reserve_login(request, username)
        except OperationalError:
            allowed = False
        if not allowed:
            form = AuthenticationForm(request)
            error_message = "Unable to sign in. Check your details or try again later."
            throttled = True
        elif form.is_valid():
            login(request, form.get_user())
            return redirect("stock")
        else:
            # One message for missing, inactive and unknown accounts.
            form = AuthenticationForm(request)
            error_message = "Unable to sign in. Check your details or try again later."
    response = render(
        request,
        "login.html",
        {"form": form, "error_message": error_message},
        status=429 if throttled else 200,
    )
    if throttled:
        response["Retry-After"] = str(settings.LOGIN_WINDOW_SECONDS)
    return response


@require_POST
def sign_out(request):
    logout(request)
    return redirect("login")


def catalogue():
    return Product.objects.select_related("category").annotate(
        total=Coalesce(Sum("batches__quantity"), Value(0))
    )


@require_http_methods(["GET"])
def stock_list(request):
    products = catalogue().filter(total__gt=0)
    query = request.GET.get("q", "").strip()[:200]
    if query:
        products = products.filter(name__icontains=query)
    return catalogue_list(
        request, "Stock status", products, "prepperdb-stock-collapsed", query=query
    )


@require_http_methods(["GET"])
def history(request):
    from django.core.paginator import Paginator

    services.prune_history()
    changes = StockChange.objects.select_related("batch__product", "user").order_by(
        "-created", "-pk"
    )
    page = Paginator(changes, 50).get_page(request.GET.get("page"))
    for change in page:
        change.can_undo = (
            change.user_id == request.user.pk
            and change.batch_id is not None
            and not change.undone
            and change.reason not in ("undo", "delete")
            and change.batch.revision == change.revision
        )
        change.reason_label = {
            "add": "Added",
            "consume": "Used",
            "set": "Stocktake",
            "discard": "Discarded",
            "undo": "Undone",
            "delete": "Item deleted",
        }.get(change.reason, change.reason)
    return render(request, "history.html", {"title": "History", "page": page})


def catalogue_list(request, title, products, storage, attention=False, query=""):
    groups = categories.groups(products)
    return render(
        request,
        "list.html",
        {
            "title": title,
            "groups": groups,
            "stock_tree": categories.stock_tree(groups),
            "stock_layout": "list",
            "tree_storage": storage,
            "attention": attention,
            "query": query,
        },
    )


@require_http_methods(["GET"])
def attention(request):
    return catalogue_list(
        request,
        "Needs attention",
        [p for p in catalogue() if p.missing],
        "prepperdb-attention-collapsed",
        attention=True,
    )


@require_http_methods(["GET"])
def out_of_stock(request):
    # Zero-stock products remain in the catalogue for replenishment.
    return catalogue_list(
        request, "Out of stock", catalogue().filter(total=0), "prepperdb-out-of-stock-collapsed"
    )


@require_http_methods(["GET"])
def rotation(request):
    batches = Batch.objects.filter(quantity__gt=0).select_related("product")
    return render(
        request,
        "rotation.html",
        {
            "title": "Endangered stock",
            "dated": batches.exclude(date="").order_by("date", "pk"),
            "undated": batches.filter(date="").order_by("product__name"),
            "today": timezone.localdate().isoformat(),
        },
    )


@require_http_methods(["GET"])
def search(request):
    query = request.GET.get("q", "").strip()[:200]
    if not query:
        return JsonResponse({"products": []})
    from django.db.models import Q

    products = (
        catalogue().filter(Q(name__icontains=query) | Q(barcodes__code=query)).distinct()[:12]
    )
    return JsonResponse(
        {
            "products": [
                {
                    "id": p.pk,
                    "name": p.name,
                    "size": p.size_label,
                    "packaging": p.packaging,
                    "quantity": p.total,
                }
                for p in products
            ]
        }
    )


def report_error(form, error):
    if isinstance(error, ObjectDoesNotExist):
        form.add_error(None, "This record was removed. Reload stock before continuing.")
        return
    text = (
        "; ".join(error.messages)
        if isinstance(error, ValidationError)
        else "The database is busy. Please try again; nothing was changed."
    )
    form.add_error(None, text)


def success(request, result, text):
    if not result.change_id:
        messages.success(request, text)
    if result.change_id and result.change.reason != "undo":
        request.session["last_change"] = result.change_id
    return redirect(return_destination(request, result.product_id))


def return_destination(request, product_id, destination=None):
    if destination is None:
        destination = request.POST.get("return_to", "")
    try:
        parsed = urlsplit(destination)
    except ValueError:
        parsed = None
    if (
        parsed
        and not parsed.scheme
        and not parsed.netloc
        and parsed.path
        in (reverse("stock"), reverse("history"), reverse("attention"), reverse("out_of_stock"))
    ):
        return destination
    return reverse("product", args=[product_id]) if product_id else reverse("stock")


@require_http_methods(["GET", "POST"])
def add_stock(request):
    selected_id = (
        request.POST.get("product") if request.method == "POST" else request.GET.get("product")
    )
    if selected_id:
        try:
            selected_id = IntegerField(min_value=1, max_value=9223372036854775807).clean(
                selected_id
            )
        except ValidationError as error:
            raise Http404("Invalid product selection.") from error
    selected = get_object_or_404(Product, pk=selected_id) if selected_id else None
    initial = {}
    imported = None
    if request.method == "GET" and request.GET.get("lookup"):
        try:
            result_id = UUIDField().clean(request.GET["lookup"])
        except ValidationError as error:
            raise Http404("Invalid lookup selection.") from error
        imported = get_object_or_404(LookupResult, pk=result_id)
        local = provider.local_match(imported.code)
        if local:
            return redirect(
                reverse("add") + "?product=" + str(local.product_id) + "&barcode=" + local.code
            )
        if imported.cache.key.startswith("search:"):
            try:
                detailed = provider.lookup(imported.code, "barcode")
                exact = detailed.results.first()
                if exact:
                    imported = exact
            except OperationalError:
                pass  # The selected cached name remains usable manually.
        imported = images.import_result_image(imported)
        initial = {**imported.details, "barcode": imported.code, "lookup": imported.pk}
        if not initial.get("size_value"):
            from .sizes import known_size

            initial.update(known_size([imported.code]))
        initial["category"] = provider_category = services.classify(
            initial.get("name", ""), initial.get("packaging", ""), initial.get("category", "")
        )
        initial["category"] = provider_category.pk if provider_category else "uncategorised"
    elif request.method == "GET" and request.GET.get("barcode"):
        code = request.GET["barcode"][:64]
        initial["barcode"] = code
        if selected:
            saved = selected.barcodes.filter(code=code).first()
            if saved:
                initial["items_per_pack"] = saved.items_per_pack
    form = forms.AddForm(
        request.POST if request.method == "POST" else None, product=selected, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = services.add(request.user, form.cleaned_data, selected)
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            return success(request, result, "Stock saved.")
    return render(
        request,
        "add.html",
        {
            "title": "Add stock",
            "form": form,
            "selected": selected,
            "imported": imported,
            "lookup_message": request.session.pop("lookup_message", ""),
        },
    )


@require_POST
def product_lookup(request):
    from urllib.parse import urlencode

    code = request.POST.get("barcode", "").strip()[:64]
    query = request.POST.get("query", "").strip()[:120]
    if code:
        local = provider.local_match(code)
        if local:
            return redirect(
                reverse("add")
                + "?"
                + urlencode({"product": local.product_id, "barcode": local.code})
            )
        valid = provider.gtin(code)
        if not valid:
            request.session["lookup_message"] = (
                "No saved match for this code. Check the digits or enter a name manually below."
            )
            return redirect(reverse("add") + "?" + urlencode({"barcode": code}))
        query = valid
        kind = "barcode"
    elif len(query) >= 3:
        kind = "search"
    else:
        request.session["lookup_message"] = (
            "Enter a barcode or at least three letters to look up a product."
        )
        return redirect("add")
    try:
        cached = provider.lookup(query, kind)
    except OperationalError:
        request.session["lookup_message"] = provider.MESSAGES["unavailable"]
        return redirect(reverse("add") + ("?" + urlencode({"barcode": code}) if code else ""))
    results = list(cached.results.all())
    for result in results:
        node = services.classify(
            result.details.get("name", ""),
            result.details.get("packaging", ""),
            result.details.get("category", ""),
        )
        result.category_label = str(node) if node else "Uncategorised"
    if kind == "barcode" and len(results) == 1:
        return redirect(reverse("add") + "?lookup=" + str(results[0].pk))
    if not results:
        request.session["lookup_message"] = provider.MESSAGES.get(
            cached.status, provider.MESSAGES["unavailable"]
        )
        return redirect(reverse("add") + ("?" + urlencode({"barcode": code}) if code else ""))
    return render(
        request, "lookup.html", {"title": "Choose a product", "results": results, "query": query}
    )


@require_http_methods(["GET"])
def product_image(request, digest):
    import re

    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise Http404
    image = get_object_or_404(ProductImage, pk=digest)
    try:
        return redirect(images.materialise(image))
    except (OSError, ValueError):
        raise Http404("Image unavailable.") from None


@require_http_methods(["GET"])
def product_detail(request, pk):
    product = get_object_or_404(catalogue(), pk=pk)
    batches = product.batches.filter(quantity__gt=0).order_by("date", "pk")
    return render(
        request,
        "product.html",
        {
            "product": product,
            "batches": batches,
            "title": product.name,
            "use_form": forms.RevisionForm(initial={"revision": product.revision}),
        },
    )


@require_POST
def use_one(request, pk):
    return quick_stock_change(request, pk, increase=False)


@require_POST
def increase_one(request, pk):
    return quick_stock_change(request, pk, increase=True)


def quick_stock_change(request, pk, increase):
    form = forms.RevisionForm(request.POST)
    if form.is_valid():
        try:
            product = catalogue().get(pk=pk)
            if (
                not increase
                and product.total == 1
                and product.revision == form.cleaned_data["revision"]
                and request.POST.get("confirm_last") != "1"
            ):
                return render(
                    request,
                    "confirm_last.html",
                    {
                        "title": "Use last item?",
                        "product": product,
                        "form": form,
                        "return_to": return_destination(request, product.pk),
                    },
                )
            action = services.increase_one if increase else services.consume_one
            result = action(
                request.user, form.cleaned_data["token"], pk, form.cleaned_data["revision"]
            )
        except Product.DoesNotExist:
            return redirect("stock")
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            text = "Added one to the displayed batch." if increase else "Used one."
            return success(request, result, text + " You can undo this below.")
    return render(
        request,
        "form.html",
        {"title": "Could not update stock", "form": form, "no_submit": True},
        status=409,
    )


@require_http_methods(["GET", "POST"])
def adjust(request, pk):
    batch = get_object_or_404(Batch.objects.select_related("product"), pk=pk)
    form = forms.AdjustForm(
        request.POST if request.method == "POST" else None,
        initial={"revision": batch.revision},
        available=batch.quantity,
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = services.adjust(request.user, batch_id=pk, **form.cleaned_data)
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            if isinstance(error, ValidationError) and getattr(error, "code", None) == "stock_limit":
                form.add_error("amount", error)
            else:
                report_error(form, error)
        else:
            return success(request, result, "Quantity updated. You can undo this below.")
    return render(
        request,
        "form.html",
        {
            "title": f"Update {batch.product.name}",
            "form": form,
            "detail": f"{batch.date_label} · {batch.quantity} in stock",
            "button": "Update quantity",
        },
    )


@require_POST
def undo(request, pk):
    form = forms.OperationForm(request.POST)
    if form.is_valid():
        try:
            result = services.undo(request.user, form.cleaned_data["token"], pk)
        except StockChange.DoesNotExist:
            return redirect("stock")
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            request.session.pop("last_change", None)
            return success(request, result, "Stock change undone.")
    return render(
        request,
        "form.html",
        {"title": "Could not undo", "form": form, "no_submit": True},
        status=409,
    )


@require_http_methods(["GET", "POST"])
def edit_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    initial = {
        field: getattr(product, field)
        for field in ("name", "size_value", "size_unit", "packaging", "category", "revision")
    }
    initial["barcode"] = product.barcodes.values_list("code", flat=True).first() or ""
    initial["category"] = product.category_id or "uncategorised"
    if product.size_value is None:
        from .sizes import known_size

        initial.update(
            known_size(product.barcodes.values_list("code", flat=True), product.packaging)
        )
    form = forms.EditProductForm(
        request.POST if request.method == "POST" else None, request.FILES or None, initial=initial
    )
    focus = request.GET.get("focus", "")
    if focus in ("photo", "size_value", "category", "packaging"):
        form.fields[focus].widget.attrs["autofocus"] = True
    if request.method == "POST" and form.is_valid():
        try:
            result = services.edit_product(
                request.user, form.cleaned_data["token"], pk, form.cleaned_data
            )
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            return success(request, result, "Product corrected.")
    return render(
        request,
        "form.html",
        {
            "title": "Correct product details",
            "detail": product.name,
            "form": form,
            "button": "Save corrections",
        },
    )


@require_http_methods(["GET", "POST"])
def edit_batch(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    date = batch.date_label.removeprefix(batch.get_date_type_display() + " ") if batch.date else ""
    form = forms.EditBatchForm(
        request.POST if request.method == "POST" else None,
        initial={"date_type": batch.date_type, "date": date, "revision": batch.revision},
    )
    form.fields["date"].widget.widgets[1].attrs["autofocus"] = True
    if request.method == "POST" and form.is_valid():
        try:
            result = services.edit_date(
                request.user, form.cleaned_data["token"], pk, form.cleaned_data
            )
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            return success(request, result, "Date corrected. Matching batches were combined.")
    return render(
        request,
        "form.html",
        {
            "title": "Correct batch date",
            "detail": f"{batch.product.name} · {batch.quantity} in stock",
            "form": form,
            "button": "Save date",
        },
    )


@require_http_methods(["GET"])
def category_list(request):
    return render(
        request, "categories.html", {"title": "Categories", "categories": categories.nested_tree()}
    )


@require_http_methods(["GET", "POST"])
def category_edit(request, pk=None):
    from .models import Category

    node = get_object_or_404(Category, pk=pk) if pk else None
    initial = (
        {"name": node.name, "parent": node.parent_id or "", "revision": node.revision}
        if node
        else {}
    )
    if not node and request.GET.get("parent"):
        try:
            parent_id = IntegerField().clean(request.GET["parent"])
        except ValidationError:
            raise Http404("Invalid parent category.") from None
        parent = get_object_or_404(Category, pk=parent_id)
        initial["parent"] = parent.pk
    form = forms.CategoryForm(
        request.POST if request.method == "POST" else None, initial=initial, category=node
    )
    if request.method == "POST" and form.is_valid():
        try:
            categories.save(request.user, form.cleaned_data, pk)
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            messages.success(request, "Category saved.")
            return redirect("categories")
    return render(
        request,
        "category_form.html",
        {"title": "Edit category" if node else "Add category", "form": form},
    )


@require_http_methods(["GET", "POST"])
def category_delete(request, pk):
    from .models import Category

    if request.method == "POST":
        form = forms.CategoryDeleteForm(request.POST)
        if form.is_valid():
            try:
                categories.delete(request.user, form.cleaned_data, pk)
            except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
                # A refreshed signature/counts requires a deliberate second confirmation.
                failure = error
            else:
                messages.success(
                    request, "Category branch deleted. Its items are now Uncategorised."
                )
                return redirect("categories")
        else:
            failure = ValidationError("Reload the confirmation before deleting this category.")
    else:
        failure = None
    node = get_object_or_404(Category, pk=pk)
    signature, count, products = categories.deletion_snapshot(pk)
    form = forms.CategoryDeleteForm(initial={"signature": signature})
    if failure:
        form = forms.CategoryDeleteForm({"signature": signature, "token": uuid.uuid4()})
        form.is_valid()
        report_error(form, failure)
    return render(
        request,
        "category_delete.html",
        {
            "title": "Delete category branch",
            "category": node,
            "form": form,
            "category_count": count,
            "product_count": products,
        },
    )


@require_http_methods(["GET", "POST"])
def delete_product(request, pk):
    destination = (request.POST if request.method == "POST" else request.GET).get("return_to", "")
    return_to = return_destination(request, None, destination)
    # Replayed deletion is checked before fetching a now-deleted product.
    if request.method == "POST":
        form = forms.RevisionForm(request.POST)
        if form.is_valid():
            try:
                services.delete(
                    request.user, form.cleaned_data["token"], pk, form.cleaned_data["revision"]
                )
            except Product.DoesNotExist:
                return redirect(return_to)
            except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
                messages.error(
                    request,
                    "; ".join(error.messages)
                    if isinstance(error, ValidationError)
                    else "Database busy; deletion was not applied. Try again.",
                )
            else:
                messages.success(request, "Item permanently deleted.")
                return redirect(return_to)
        else:
            messages.error(request, "Invalid confirmation. Check the total and confirm again.")
    product = get_object_or_404(catalogue(), pk=pk)
    return render(
        request,
        "delete.html",
        {
            "title": "Delete item",
            "product": product,
            "form": forms.RevisionForm(initial={"revision": product.revision}),
            "return_to": return_to,
            "cancel_to": return_destination(request, pk, destination),
        },
    )


@require_http_methods(["GET", "POST"])
def edit_photo(request, pk):
    from PIL import Image

    product = get_object_or_404(Product.objects.select_related("image"), pk=pk)
    form = forms.PhotoForm(
        request.POST if request.method == "POST" else None,
        request.FILES or None,
        initial={"revision": product.revision},
    )
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["photo"]
        if not upload and not product.image_id:
            form.add_error("photo", "Choose a photo to add to this item.")
        else:
            try:
                data = images.edit_photo(
                    upload.read() if upload else bytes(product.image.data),
                    **{
                        key: form.cleaned_data[key]
                        for key in ("zoom", "horizontal", "vertical", "rotation")
                    },
                )
                result = services.set_photo(
                    request.user,
                    form.cleaned_data["token"],
                    pk,
                    form.cleaned_data["revision"],
                    data=data,
                    source=None if upload else product.image,
                )
            except (
                ValueError,
                OSError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ):
                form.add_error(
                    "photo", "Choose a readable JPG, PNG or WebP under 10 MB and 12.5 megapixels."
                )
            except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
                report_error(form, error)
            else:
                return success(request, result, "Photo saved.")
    return render(
        request, "photo.html", {"title": "Manage photo", "product": product, "form": form}
    )


@require_http_methods(["GET", "POST"])
def remove_photo(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = forms.RevisionForm(
        request.POST if request.method == "POST" else None, initial={"revision": product.revision}
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = services.set_photo(
                request.user,
                form.cleaned_data["token"],
                pk,
                form.cleaned_data["revision"],
                remove=True,
            )
        except (ValidationError, OperationalError, ObjectDoesNotExist) as error:
            report_error(form, error)
        else:
            return success(request, result, "Photo removed.")
    return render(
        request, "photo_remove.html", {"title": "Remove photo", "product": product, "form": form}
    )
