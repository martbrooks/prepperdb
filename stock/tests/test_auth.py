import uuid
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import get_resolver

from stock.models import Batch, LoginAttempt, Product, User


class AuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("tester", "test-only-long-password-9187")
        cls.product = Product.objects.create(family=uuid.uuid4(), name="Beans")
        cls.batch = Batch.objects.create(product=cls.product, date_type="best_before", quantity=3)

    def test_every_catalogue_and_stock_route_requires_login(self):
        for pattern in get_resolver().url_patterns:
            if pattern.name == "login":
                continue
            route = "/" + str(pattern.pattern).replace("<int:pk>", "1").replace(
                "<slug:digest>", "a" * 64
            )
            for method in ("get", "post"):
                with self.subTest(route=route, method=method):
                    self.assertIn(getattr(self.client, method)(route).status_code, (302, 403))
        self.assertEqual(Batch.objects.get(pk=self.batch.pk).quantity, 3)

    def test_unknown_disabled_and_wrong_password_have_same_message(self):
        self.user.is_active = False
        self.user.save()
        for username, password in [
            ("missing", "bad"),
            ("tester", "bad"),
            ("tester", "test-only-long-password-9187"),
        ]:
            response = self.client.post("/login/", {"username": username, "password": password})
            self.assertContains(
                response, "Unable to sign in. Check your details or try again later."
            )
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rotation_and_explicit_logout_invalidate_session(self):
        self.client.get("/login/")
        self.client.post(
            "/login/", {"username": "tester", "password": "test-only-long-password-9187"}
        )
        stolen = self.client.cookies["prepperdb_session"].value
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.client.post("/logout/")
        self.client.cookies["prepperdb_session"] = stolen
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_password_replacement_via_sql_revokes_existing_session(self):
        self.client.force_login(self.user)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stock_user SET password=%s WHERE id=%s",
                [make_password("replacement-password-12857"), self.user.pk],
            )
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_raw_sql_user_creation_and_removal(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO stock_user(username,password,is_active,last_login) VALUES(%s,%s,1,NULL)",
                ["manual", make_password("manual-password-12857")],
            )
        manual = User.objects.get(username="manual")
        self.client.force_login(manual)
        self.client.post(
            "/add/",
            {
                "token": str(uuid.uuid4()),
                "name": "Manual user stock",
                "quantity": 2,
                "items_per_pack": 1,
                "date_type": "best_before",
            },
        )
        # Actual documented SQL must remove nullable history references and operation ownership first.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stock_stockchange SET user_id=NULL WHERE user_id=%s", [manual.pk]
            )
            cursor.execute("DELETE FROM stock_operation WHERE user_id=%s", [manual.pk])
            cursor.execute("DELETE FROM stock_user WHERE id=%s", [manual.pk])
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_disabling_account_via_sql_revokes_session(self):
        self.client.force_login(self.user)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE stock_user SET is_active=0 WHERE id=%s", [self.user.pk])
        self.assertEqual(self.client.get("/").status_code, 302)

    @override_settings(LOGIN_LIMIT=2)
    def test_persistent_throttle_known_and_unknown_with_expiry(self):
        for _ in range(2):
            self.client.post("/login/", {"username": "missing", "password": "wrong"})
        response = Client().post("/login/", {"username": "missing", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        self.assertEqual(LoginAttempt.objects.count(), 2)
        from django.utils import timezone

        LoginAttempt.objects.update(expires=timezone.now())
        self.assertEqual(
            self.client.post("/login/", {"username": "missing", "password": "wrong"}).status_code,
            200,
        )

    def test_csrf_on_login_and_mutation(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            client.post(
                "/login/", {"username": "tester", "password": "test-only-long-password-9187"}
            ).status_code,
            403,
        )
        client.force_login(self.user)
        self.assertEqual(client.post("/products/1/use/", {}).status_code, 403)

    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_cookie_flags_and_private_cache_policy(self):
        response = self.client.post(
            "/login/",
            {"username": "tester", "password": "test-only-long-password-9187"},
            secure=True,
        )
        cookie = response.cookies["prepperdb_session"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["domain"], "")
        response = self.client.get("/", secure=True)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])

    @override_settings(SECURE_SSL_REDIRECT=True, TRUSTED_PROXIES={"127.0.0.1"})
    def test_untrusted_forwarding_headers_do_not_bypass_https(self):
        response = self.client.get(
            "/login/",
            REMOTE_ADDR="203.0.113.2",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_PREPPERDB_PROTO="https",
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            self.client.get(
                "/login/", REMOTE_ADDR="127.0.0.1", HTTP_X_PREPPERDB_PROTO="https"
            ).status_code,
            200,
        )

    def test_no_account_management_or_source_database_routes(self):
        self.client.force_login(self.user)
        for path in [
            "/admin/",
            "/register/",
            "/password-reset/",
            "/stock.sqlite3",
            "/.env",
            "/manage.py",
            "/images/materialise/1/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_hash_command_uses_private_prompt(self):
        from io import StringIO
        from django.core.management import call_command
        from django.contrib.auth.hashers import check_password

        out = StringIO()
        with (
            patch("stock.management.commands.password_hash.sys.stdin.isatty", return_value=True),
            patch(
                "stock.management.commands.password_hash.getpass.getpass",
                return_value="new-long-password-9187",
            ),
        ):
            call_command("password_hash", stdout=out)
        self.assertTrue(check_password("new-long-password-9187", out.getvalue().strip()))

    def test_hash_command_rejects_noninteractive_input(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with patch("stock.management.commands.password_hash.sys.stdin.isatty", return_value=False):
            with self.assertRaises(CommandError):
                call_command("password_hash")
