import uuid
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from stock import categories, forms, services
from stock.models import Category, Product, User
from .test_stock import addition


class CategoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("category-fixture", "synthetic-category-password-9187")

    def setUp(self):
        self.client.force_login(self.user)

    def save(self, name, parent=None, node=None, token=None):
        return categories.save(
            self.user,
            {
                "token": token or uuid.uuid4(),
                "name": name,
                "parent": parent.pk if parent else "",
                "revision": node.revision if node else 0,
            },
            node.pk if node else None,
        )

    def test_arbitrary_tree_ordering_and_grouped_stock(self):
        self.save("Zoo")
        self.save("Alpha")
        alpha = Category.objects.get(name="Alpha")
        self.save("zeta", alpha)
        self.save("Beta", alpha)
        beta = Category.objects.get(name="Beta", parent=alpha)
        self.save("Nested", beta)
        nested = Category.objects.get(name="Nested")
        services.add(self.user, addition(name="Deep stock", category=nested))
        services.add(self.user, addition(name="Unsorted stock", category="uncategorised"))
        nodes = [n.name for n in categories.tree() if n.path.startswith("Alpha")]
        self.assertEqual(nodes, ["Alpha", "Beta", "Nested", "zeta"])
        response = self.client.get("/")
        self.assertEqual(
            [g["name"] for g in response.context["groups"]],
            ["Alpha", "Beta", "Nested", "Uncategorised"],
        )
        self.assertContains(response, 'aria-level="4"')
        self.assertContains(response, "Deep stock")
        self.assertContains(response, "Unsorted stock")

    def test_move_rename_and_cycle_prevention(self):
        self.save("Root")
        root = Category.objects.get(name="Root")
        self.save("Child", root)
        child = Category.objects.get(name="Child", parent=root)
        with self.assertRaises(ValidationError):
            self.save("Root", child, root)
        with self.assertRaises(ValidationError):
            self.save("Child", child, child)
        self.save("Moved", node=child)
        child.refresh_from_db()
        self.assertIsNone(child.parent_id)
        self.assertEqual(child.name, "Moved")
        form = forms.CategoryForm(category=root)
        self.assertNotIn(str(root.pk), dict(form.fields["parent"].choices))

    def test_sibling_names_reserved_fallback_and_stale_edits(self):
        self.save("Mixed Case")
        root = Category.objects.get(name="Mixed Case")
        with self.assertRaises(ValidationError):
            self.save(" mixed   case ")
        with self.assertRaises(ValidationError):
            self.save("Uncategorised")
        stale = Category.objects.get(pk=root.pk)
        self.save("Renamed", node=root)
        with self.assertRaises(ValidationError):
            self.save("Stale", node=stale)
        self.save("Mixed Case", root)
        self.assertEqual(Category.objects.filter(name="Mixed Case").count(), 1)

    def test_delete_branch_preserves_stock_and_is_replay_safe(self):
        self.save("Temporary")
        root = Category.objects.get(name="Temporary")
        self.save("Child", root)
        child = Category.objects.get(name="Child", parent=root)
        one = services.add(self.user, addition(name="Parent stock", quantity=2, category=root))
        two = services.add(self.user, addition(name="Child stock", quantity=3, category=child))
        token = uuid.uuid4()
        signature, count, products = categories.deletion_snapshot(root.pk)
        self.assertEqual((count, products), (2, 2))
        categories.delete(self.user, {"token": token, "signature": signature}, root.pk)
        categories.delete(self.user, {"token": token, "signature": signature}, root.pk)
        self.assertFalse(Category.objects.filter(pk__in=[root.pk, child.pk]).exists())
        for product in Product.objects.filter(pk__in=[one.product_id, two.product_id]):
            self.assertIsNone(product.category_id)
            self.assertIn("classification", product.missing)
        self.assertEqual(one.product.batches.get().quantity, 2)
        self.assertEqual(two.product.batches.get().quantity, 3)

    def test_delete_refreshes_changed_confirmation_and_cancellation(self):
        self.save("Changing")
        root = Category.objects.get(name="Changing")
        response = self.client.get(f"/categories/{root.pk}/delete/")
        old = response.context["form"].initial["signature"]
        services.add(self.user, addition(name="New stock", category=root))
        response = self.client.post(
            f"/categories/{root.pk}/delete/", {"token": uuid.uuid4(), "signature": old}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "changed")
        self.assertEqual(response.context["product_count"], 1)
        self.assertTrue(Category.objects.filter(pk=root.pk).exists())

    def test_defaults_follow_renames_and_do_not_recreate_deleted_categories(self):
        tinned = Category.objects.get(automatic_key="tinned")
        self.save("Cans", node=tinned)
        selected = services.classify("Beans", "tin")
        self.assertEqual(selected.pk, tinned.pk)
        self.assertEqual(selected.name, "Cans")
        signature, _, _ = categories.deletion_snapshot(tinned.pk)
        categories.delete(self.user, {"token": uuid.uuid4(), "signature": signature}, tinned.pk)
        self.assertIsNone(services.classify("Beans", "tin"))
        self.assertFalse(Category.objects.filter(automatic_key="tinned").exists())
        self.assertEqual(services.classify("Cola", "tin").automatic_key, "canned_drinks")

    def test_category_management_authentication_csrf_and_repeated_create(self):
        anonymous = Client()
        for path in (
            "/categories/",
            "/categories/add/",
            "/categories/1/edit/",
            "/categories/1/delete/",
        ):
            self.assertEqual(anonymous.get(path).status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post("/categories/add/", {"name": "No CSRF"}).status_code, 403)
        token = uuid.uuid4()
        data = {"token": token, "name": "Repeated", "parent": "", "revision": 0}
        self.client.post("/categories/add/", data)
        self.client.post("/categories/add/", data)
        self.assertEqual(Category.objects.filter(name="Repeated").count(), 1)
        self.assertEqual(self.client.get("/categories/add/?parent=bad").status_code, 404)
