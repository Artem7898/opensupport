import base64
import hashlib
import json
import re
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, Submission

REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z")
HANDLE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


class GitHubUnavailable(Exception):
    pass


class GitHubMissing(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def repository_name(value):
    if not isinstance(value, str):
        raise ValidationError("A GitHub repository URL is required.")
    value = value.strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/").rstrip("/")
    if not REPOSITORY_PATTERN.fullmatch(value) or value.split("/")[1] in (".", ".."):
        raise ValidationError("Use https://github.com/owner/repository.")
    return value


def funding_url(value):
    if not isinstance(value, str) or len(value) > 220:
        raise ValidationError("Invalid sponsorship URL.")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or parts.port
    ):
        raise ValidationError("Use a direct HTTPS sponsorship URL without query parameters.")
    segments = parts.path.strip("/").split("/")
    if (
        parts.netloc == "github.com"
        and len(segments) == 2
        and segments[0] == "sponsors"
        and HANDLE_PATTERN.fullmatch(segments[1])
    ):
        return "https://github.com/sponsors/" + segments[1]
    if (
        parts.netloc == "opencollective.com"
        and len(segments) == 1
        and HANDLE_PATTERN.fullmatch(segments[0])
    ):
        return "https://opencollective.com/" + segments[0]
    raise ValidationError("Only GitHub Sponsors and Open Collective profile URLs are accepted.")


def github_json(path):
    # Only fixed GitHub API hosts and validated repository names reach this function.
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenSupport/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = "Bearer " + settings.GITHUB_TOKEN
    request = Request("https://api.github.com/" + path, headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=8) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise GitHubUnavailable("GitHub response too large.")
            return json.loads(raw)
    except HTTPError as exc:
        if exc.code == 404:
            raise GitHubMissing() from exc
        raise GitHubUnavailable("GitHub could not be reached. Try again later.") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise GitHubUnavailable("GitHub could not be reached. Try again later.") from exc


def github_file(repository, path):
    data = github_json(f"repos/{repository}/contents/{path}")
    if (
        not isinstance(data, dict)
        or data.get("type") != "file"
        or data.get("encoding") != "base64"
        or data.get("size", 999999) > 16384
    ):
        raise ValidationError("Verification files must be regular files smaller than 16 KB.")
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except (ValueError, UnicodeError, KeyError) as exc:
        raise ValidationError("Invalid verification file.") from exc


def challenge():
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def public_funding(repository):
    path = ".github/FUNDING.yml"
    source_repo = repository
    try:
        text = github_file(repository, path)
    except GitHubMissing:
        source_repo = repository.split("/")[0] + "/.github"
        path = "FUNDING.yml"
        text = github_file(source_repo, path)
    try:
        # Reject YAML aliases: the funding format needs neither aliases nor nested structures.
        if any(
            isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
            for token in yaml.scan(text)
        ):
            raise ValidationError("Funding YAML aliases are not supported.")
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError("Invalid FUNDING.yml.") from exc
    if not isinstance(data, dict):
        raise ValidationError("FUNDING.yml must contain a mapping.")
    urls = set()
    for kind, prefix in [
        ("github", "https://github.com/sponsors/"),
        ("open_collective", "https://opencollective.com/"),
    ]:
        values = data.get(kind, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or len(values) > 10:
            continue
        for value in values:
            if isinstance(value, str) and HANDLE_PATTERN.fullmatch(value):
                urls.add(prefix + value)
    return urls, f"https://github.com/{source_repo}"


def verify_submission(submission, actor):
    if submission.owner_id != actor.pk:
        raise PermissionDenied()
    if submission.status != Submission.Status.UNVERIFIED:
        raise ValidationError("This submission is no longer awaiting verification.")
    if submission.challenge_expires_at <= timezone.now():
        raise ValidationError("Challenge expired. Generate a new challenge.")
    try:
        metadata = github_json(f"repos/{submission.repository}")
        if not isinstance(metadata, dict) or metadata.get("private") or metadata.get("archived"):
            raise ValidationError("Use an active public repository.")
        license_id = (metadata.get("license") or {}).get("spdx_id", "")
        if not license_id or license_id == "NOASSERTION":
            raise ValidationError("An identifiable open source license is required.")
        proof = json.loads(github_file(submission.repository, ".github/opensupport.json"))
        token = proof.get("token") if isinstance(proof, dict) else None
        if not isinstance(token, str) or not secrets.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), submission.challenge_hash
        ):
            raise ValidationError("The ownership token does not match.")
        urls, source = public_funding(submission.repository)
        if submission.funding_url not in urls:
            raise ValidationError(
                "The sponsorship URL must appear in the repository's FUNDING.yml."
            )
    except GitHubMissing as exc:
        raise ValidationError(
            "Repository, proof file or FUNDING.yml not found on the default branch."
        ) from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Invalid ownership proof JSON.") from exc
    with transaction.atomic():
        current = Submission.objects.select_for_update().get(pk=submission.pk)
        if (
            current.status != Submission.Status.UNVERIFIED
            or current.challenge_hash != submission.challenge_hash
            or current.challenge_expires_at <= timezone.now()
        ):
            raise ValidationError("The challenge changed or expired. Reload and try again.")
        current.status = Submission.Status.PENDING
        current.verified_at = timezone.now()
        current.license = license_id[:40]
        current.language = str(metadata.get("language") or "Other")[:40]
        current.funding_source = source
        current.save()
        AuditEvent.objects.create(actor=actor, submission=current, action="ownership_verified")
    return current


def review_submission(submission_id, actor, approve, note):
    if not actor.is_staff or not actor.has_perm("directory.review_submission"):
        raise PermissionDenied()
    if not note.strip():
        raise ValidationError("A review note is required.")
    with transaction.atomic():
        row = Submission.objects.select_for_update().get(pk=submission_id)
        if row.owner_id == actor.pk:
            raise ValidationError("A different moderator must review your submission.")
        if row.status != Submission.Status.PENDING or not row.verified_at:
            raise ValidationError("Only ownership-verified pending submissions can be reviewed.")
        if approve and row.verified_at < timezone.now() - timedelta(days=7):
            raise ValidationError(
                "Ownership verification is over 7 days old; reject and request a fresh submission."
            )
        if (
            approve
            and Submission.objects.filter(
                repository__iexact=row.repository, status="approved"
            ).exists()
        ):
            raise ValidationError("This repository is already published.")
        row.status = Submission.Status.APPROVED if approve else Submission.Status.REJECTED
        row.reviewer = actor
        row.reviewed_at = timezone.now()
        row.review_note = note[:500]
        row.save()
        AuditEvent.objects.create(
            actor=actor, submission=row, action=row.status, detail=row.review_note
        )
    return row
