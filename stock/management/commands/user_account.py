"""Interactive account setup without putting passwords in arguments or SQL."""

import getpass
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from stock.models import User


class Command(BaseCommand):
    help = "Create/reset a shared-inventory account, or enable/disable it."

    def add_arguments(self, parser):
        parser.add_argument("username")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--disable", action="store_true")
        action.add_argument("--enable", action="store_true")

    def handle(self, username, disable, enable, **options):
        if not username.strip() or len(username) > 150 or any(c.isspace() for c in username):
            raise CommandError("Use a non-empty username without spaces, at most 150 characters.")
        if disable or enable:
            changed = User.objects.filter(username=username).update(is_active=enable)
            if not changed:
                raise CommandError("Account does not exist.")
            self.stdout.write("Account enabled." if enable else "Account disabled.")
            return
        if not sys.stdin.isatty():
            raise CommandError("Use an interactive terminal; never pass passwords as arguments.")
        password = getpass.getpass("New password (at least 12 characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise CommandError("Passwords do not match.")
        user = User(username=username)
        try:
            validate_password(password, user=user)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        with transaction.atomic():
            user, created = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.save(update_fields=["password"])
        self.stdout.write(
            "Account created." if created else "Password replaced; existing sessions revoked."
        )
