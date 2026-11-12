from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from stock.models import User


class AccountCommandTests(TestCase):
    @patch("stock.management.commands.user_account.sys.stdin.isatty", return_value=True)
    @patch("stock.management.commands.user_account.getpass.getpass")
    def test_create_and_reset_hash_password(self, prompt, tty):
        prompt.side_effect = ["A-private-test-password-42!"] * 2
        call_command("user_account", "alex", stdout=StringIO())
        user = User.objects.get(username="alex")
        self.assertTrue(user.check_password("A-private-test-password-42!"))
        previous = user.password
        prompt.side_effect = ["Another-private-test-password-42!"] * 2
        call_command("user_account", "alex", stdout=StringIO())
        user.refresh_from_db()
        self.assertNotEqual(previous, user.password)
        self.assertTrue(user.check_password("Another-private-test-password-42!"))

    @patch("stock.management.commands.user_account.sys.stdin.isatty", return_value=False)
    def test_reject_noninteractive_password(self, tty):
        with self.assertRaises(CommandError):
            call_command("user_account", "alex", stdout=StringIO())
        self.assertFalse(User.objects.exists())

    def test_disable_account_blocks_existing_session(self):
        user = User.objects.create_user("alex", "Private-test-password-42!")
        self.client.force_login(user)
        call_command("user_account", "alex", disable=True, stdout=StringIO())
        self.assertEqual(self.client.get("/").status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    @patch("stock.management.commands.user_account.sys.stdin.isatty", return_value=True)
    @patch("stock.management.commands.user_account.getpass.getpass", side_effect=["short", "short"])
    def test_weak_password_does_not_create_account(self, prompt, tty):
        with self.assertRaises(CommandError):
            call_command("user_account", "alex", stdout=StringIO())
        self.assertFalse(User.objects.exists())
