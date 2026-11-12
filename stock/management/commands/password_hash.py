import getpass
import sys
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Prompt privately for a new password and print its Django hash (no DB mutation)."

    def handle(self, **options):
        if not sys.stdin.isatty():
            raise CommandError(
                "Run this command in an interactive terminal; passwords must not be piped or passed as arguments."
            )
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        self.stdout.write(make_password(password))
