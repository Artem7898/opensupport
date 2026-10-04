from django.core.management.base import BaseCommand
from django.utils import timezone

from directory.models import RateLimit


class Command(BaseCommand):
    help = "Delete expired rate-limit windows; run hourly."

    def handle(self, *args, **options):
        count, _ = RateLimit.objects.filter(expires_at__lt=timezone.now()).delete()
        self.stdout.write(f"Removed {count} expired counters.")
