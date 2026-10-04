import json
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import AuditEvent, Submission
from .services import (
    GitHubUnavailable,
    challenge,
    funding_url,
    repository_name,
    review_submission,
    verify_submission,
)


def api(function):
    @wraps(function)
    def inner(request, *args, **kwargs):
        try:
            return function(request, *args, **kwargs)
        except (ValidationError, ValueError, TypeError) as exc:
            messages = getattr(exc, "messages", ["Invalid request."])
            return JsonResponse({"error": " ".join(messages)}, status=400)
        except PermissionDenied:
            return JsonResponse({"error": "Permission denied."}, status=403)
        except GitHubUnavailable as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        except IntegrityError:
            return JsonResponse({"error": "A matching record already exists."}, status=409)

    return inner


def body(request):
    if request.content_type != "application/json":
        raise ValidationError("Expected application/json.")
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValidationError("Expected a JSON object.")
    return data


def text(data, key, limit, required=True):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValidationError(f"Invalid {key} (maximum {limit} characters).")
    return value.strip()


def authenticated(request):
    if not request.user.is_authenticated:
        raise PermissionDenied()


@ensure_csrf_cookie
@require_GET
def session(request):
    return JsonResponse(
        {
            "available": True,
            "csrfToken": get_token(request),
            "username": request.user.username if request.user.is_authenticated else None,
            "canReview": request.user.is_authenticated
            and request.user.is_staff
            and request.user.has_perm("directory.review_submission"),
            "signupEnabled": settings.SIGNUP_ENABLED,
        }
    )


@require_POST
@api
def register(request):
    if not settings.SIGNUP_ENABLED:
        raise PermissionDenied()
    data = body(request)
    form = UserCreationForm(
        data={
            "username": text(data, "username", 150),
            "password1": text(data, "password", 128),
            "password2": text(data, "password", 128),
        }
    )
    if not form.is_valid():
        return JsonResponse(
            {"error": " ".join(str(e) for values in form.errors.values() for e in values)},
            status=400,
        )
    user = form.save()
    login(request, user)
    return JsonResponse({"username": user.username, "csrfToken": get_token(request)}, status=201)


@require_POST
@api
def sign_in(request):
    data = body(request)
    user = authenticate(
        request, username=text(data, "username", 150), password=text(data, "password", 128)
    )
    if user is None:
        return JsonResponse({"error": "Invalid username or password."}, status=400)
    login(request, user)
    return JsonResponse({"username": user.username, "csrfToken": get_token(request)})


@require_POST
@api
def sign_out(request):
    authenticated(request)
    logout(request)
    return JsonResponse({"ok": True, "csrfToken": get_token(request)})


def serialize(row, private=False):
    data = {
        "id": row.pk,
        "repository": row.repository,
        "name": row.title,
        "description": {"en": row.description_en, "ru": row.description_ru},
        "category": row.category,
        "language": row.language,
        "license": row.license,
        "funding": [
            {
                "kind": "github"
                if row.funding_url.startswith("https://github.com/")
                else "collective",
                "url": row.funding_url,
            }
        ],
        "ownershipVerified": bool(row.verified_at),
        "fundingSource": row.funding_source,
    }
    if private:
        data.update(
            status=row.status, reviewNote=row.review_note, createdAt=row.created_at.isoformat()
        )
    return data


@require_GET
def projects(request):
    rows = Submission.objects.filter(status=Submission.Status.APPROVED).order_by("title")[:300]
    return JsonResponse({"projects": [serialize(row) for row in rows]})


@require_GET
@api
def mine(request):
    authenticated(request)
    return JsonResponse(
        {
            "submissions": [
                serialize(row, True) for row in Submission.objects.filter(owner=request.user)[:100]
            ]
        }
    )


@require_POST
@api
def create_submission(request):
    authenticated(request)
    data = body(request)
    repository = repository_name(data.get("repository"))
    if (
        Submission.objects.filter(repository__iexact=repository)
        .exclude(status=Submission.Status.REJECTED)
        .exists()
    ):
        return JsonResponse(
            {"error": "This repository already has an active submission."}, status=409
        )
    category = text(data, "category", 20)
    if category not in dict(Submission._meta.get_field("category").choices):
        raise ValidationError("Unknown category.")
    token, hashed = challenge()
    row = Submission.objects.create(
        owner=request.user,
        repository=repository,
        title=text(data, "title", 80),
        description_en=text(data, "description_en", 400),
        description_ru=text(data, "description_ru", 400),
        category=category,
        funding_url=funding_url(data.get("funding_url")),
        challenge_hash=hashed,
        challenge_expires_at=timezone.now() + timedelta(hours=24),
    )
    AuditEvent.objects.create(actor=request.user, submission=row, action="submitted")
    return JsonResponse(
        {
            "id": row.pk,
            "status": row.status,
            "proof": {"token": token},
            "path": ".github/opensupport.json",
        },
        status=201,
    )


@require_POST
@api
def verify(request, pk):
    authenticated(request)
    row = get_object_or_404(Submission, pk=pk, owner=request.user)
    row = verify_submission(row, request.user)
    return JsonResponse({"status": row.status})


@require_POST
@api
def renew_challenge(request, pk):
    from django.db import transaction

    authenticated(request)
    with transaction.atomic():
        row = get_object_or_404(
            Submission.objects.select_for_update(),
            pk=pk,
            owner=request.user,
            status=Submission.Status.UNVERIFIED,
        )
        token, hashed = challenge()
        row.challenge_hash = hashed
        row.challenge_expires_at = timezone.now() + timedelta(hours=24)
        row.save(update_fields=["challenge_hash", "challenge_expires_at"])
    return JsonResponse(
        {"id": row.pk, "proof": {"token": token}, "path": ".github/opensupport.json"}
    )


@require_GET
@api
def moderation(request):
    authenticated(request)
    if not request.user.is_staff or not request.user.has_perm("directory.review_submission"):
        raise PermissionDenied()
    return JsonResponse(
        {
            "submissions": [
                serialize(row, True) for row in Submission.objects.filter(status="pending")[:100]
            ]
        }
    )


@require_POST
@api
def review(request, pk):
    authenticated(request)
    data = body(request)
    decision = data.get("decision")
    if decision not in ("approve", "reject"):
        raise ValidationError("Choose approve or reject.")
    row = review_submission(pk, request.user, decision == "approve", text(data, "note", 500))
    return JsonResponse({"status": row.status})


@require_GET
def health(request):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})
