from django.core.management.base import BaseCommand
from stock.services import prune_history


class Command(BaseCommand):
    help = "Remove stock history older than 32 days; preserve replay-protection records."

    def handle(self, **options):
        count, _ = prune_history()
        self.stdout.write(f"Removed {count} expired stock history records.")
