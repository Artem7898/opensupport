import hashlib
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import RateLimit


class HeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and (
            request.path.startswith("/api/") or request.path == "/admin/login/"
        ):
            if int(request.META.get("CONTENT_LENGTH") or "0") > 16384:
                return JsonResponse({"error": "Request too large"}, status=413)
            # REMOTE_ADDR is supplied by the trusted web server; never trust arbitrary X-Forwarded-For.
            address = request.META.get("REMOTE_ADDR", "unknown")
            window = int(time.time()) // 600
            kind = (
                "auth"
                if request.path in ("/api/login/", "/api/register/", "/admin/login/")
                else "write"
            )
            budget = 12 if kind == "auth" else 40
            raw = f"{settings.SECRET_KEY}:{address}:{kind}:{window}"
            key = hashlib.sha256(raw.encode()).hexdigest()
            with transaction.atomic():
                row, _ = RateLimit.objects.get_or_create(
                    key=key, defaults={"expires_at": timezone.now() + timedelta(minutes=20)}
                )
                row = RateLimit.objects.select_for_update().get(pk=key)
                row.count += 1
                row.save(update_fields=["count"])
                blocked = row.count > budget
            if blocked:
                response = JsonResponse(
                    {"error": "Too many attempts. Try again in 10 minutes."}, status=429
                )
                response["Retry-After"] = "600"
                return response
        response = self.get_response(request)
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Django admin uses inline styles; the public app does not.
        style_policy = "'self' 'unsafe-inline'" if request.path.startswith("/admin/") else "'self'"
        response["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'; style-src {style_policy}; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store"
        return response
