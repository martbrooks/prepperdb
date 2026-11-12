"""Editable category trees and stable optional automatic-classification targets."""

import hashlib
import json
from collections import defaultdict
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from .models import Category, Operation, Product

LEGACY_KEYS = {
    "Food & drink": "food",
    "Food & drink / Tinned products": "tinned",
    "Food & drink / Dry products": "dry",
    "Food & drink / Drinks": "drinks",
    "Household": "household",
    "Health & hygiene": "health",
}


def tree():
    children = defaultdict(list)
    for node in Category.objects.all():
        children[node.parent_id].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda c: (c.name.casefold(), c.pk))
    result, stack = [], [(node, 0, "", node.name) for node in reversed(children[None])]
    while stack:
        node, depth, parent_path, root_name = stack.pop()
        node.tree_depth = depth
        node.tree_root_name = root_name
        node.tree_path = parent_path + " / " + node.name if parent_path else node.name
        result.append(node)
        stack.extend(
            (child, depth + 1, node.tree_path, root_name) for child in reversed(children[node.pk])
        )
    return result


def resolve(value):
    if isinstance(value, Category):
        return Category.objects.get(pk=value.pk)
    if not value or value in ("Uncategorised", "uncategorised"):
        return None
    if str(value).isdigit():
        return Category.objects.get(pk=value)
    if key := LEGACY_KEYS.get(value):
        return Category.objects.filter(automatic_key=key).first()
    return next((node for node in tree() if node.path == value), None)


def nested_tree():
    nodes = tree()
    by_id = {node.pk: node for node in nodes}
    roots = []
    for node in nodes:
        node.tree_children = []
    for node in nodes:
        if node.parent_id is None:
            roots.append(node)
        else:
            by_id[node.parent_id].tree_children.append(node)
    return roots


def groups(products):
    nodes = tree()
    by_id = {node.pk: node for node in nodes}
    assigned, needed = defaultdict(list), set()
    for product in products:
        assigned[product.category_id].append(product)
        current = product.category_id
        while current is not None and current not in needed:
            needed.add(current)
            current = by_id[current].parent_id
    result = []
    # Uncategorised is a fallback group, alphabetised alongside actual roots.
    for node in nodes:
        if node.pk in needed:
            result.append(
                {
                    "id": str(node.pk),
                    "parent_id": str(node.parent_id) if node.parent_id else None,
                    "name": node.name,
                    "path": node.path,
                    "depth": range(node.tree_depth),
                    "level": node.tree_depth + 2,
                    "root": node.tree_root_name,
                    "products": assigned[node.pk],
                }
            )
    if assigned[None]:
        fallback = {
            "id": "uncategorised",
            "parent_id": None,
            "name": "Uncategorised",
            "path": "Uncategorised",
            "depth": range(0),
            "level": 2,
            "root": "Uncategorised",
            "products": assigned[None],
        }
        index = next(
            (i for i, group in enumerate(result) if group["root"].casefold() > "uncategorised"),
            len(result),
        )
        result.insert(index, fallback)
    return result


def branch_ids(pk):
    children = defaultdict(list)
    for node_id, parent in Category.objects.values_list("pk", "parent_id"):
        children[parent].append(node_id)
    result, stack = set(), [pk]
    while stack:
        current = stack.pop()
        if current not in result:
            result.add(current)
            stack.extend(children[current])
    return result


def deletion_snapshot(pk):
    ids = branch_ids(pk)
    nodes = list(
        Category.objects.filter(pk__in=ids)
        .order_by("pk")
        .values_list("pk", "parent_id", "name", "revision")
    )
    products = list(
        Product.objects.filter(category_id__in=ids)
        .order_by("pk")
        .values_list("pk", "category_id", "revision")
    )
    signature = hashlib.sha256(json.dumps([nodes, products], sort_keys=True).encode()).hexdigest()
    return signature, len(nodes), len(products)


@transaction.atomic
def save(user, data, pk=None):
    from .services import operation

    target = pk or "new"
    if previous := operation(user, data["token"], "category_save", target):
        return previous
    node = Category.objects.get(pk=pk) if pk else Category()
    if pk and node.revision != data["revision"]:
        raise ValidationError("This category changed. Reload before saving.")
    name = " ".join(data["name"].split())
    parent_id = int(data["parent"]) if data.get("parent") else None
    parent = Category.objects.get(pk=parent_id) if parent_id else None
    if pk and parent_id in branch_ids(pk):
        raise ValidationError("A category cannot be moved inside itself or its descendants.")
    if not parent and name.casefold() == "uncategorised":
        raise ValidationError("Uncategorised is reserved for items without a category.")
    if any(
        sibling.pk != node.pk and sibling.name.casefold() == name.casefold()
        for sibling in Category.objects.filter(parent_id=parent_id)
    ):
        raise ValidationError("That parent already has a category with this name.")
    node.name, node.parent = name, parent
    node.revision += 1
    node.full_clean()
    node.save()
    return Operation.objects.create(
        user=user, token=data["token"], kind="category_save", target=str(target)
    )


@transaction.atomic
def delete(user, data, pk):
    from .services import operation

    if previous := operation(user, data["token"], "category_delete", pk):
        return previous
    node = Category.objects.get(pk=pk)
    signature, _, _ = deletion_snapshot(pk)
    if signature != data["signature"]:
        raise ValidationError(
            "This branch or its items changed. Check the updated counts and confirm again."
        )
    Product.objects.filter(category_id__in=branch_ids(pk)).update(
        category=None, revision=F("revision") + 1
    )
    node.delete()
    return Operation.objects.create(
        user=user, token=data["token"], kind="category_delete", target=str(pk)
    )


def stock_tree(groups):
    """Nest occupied groups without changing product ordering or duplicating products."""
    by_id = {group["id"]: {**group, "children": []} for group in groups}
    roots = []
    for group in by_id.values():
        if group["parent_id"] is None:
            roots.append(group)
        else:
            by_id[group["parent_id"]]["children"].append(group)
    return roots
