import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import AuditEvent, Submission
from .services import challenge, funding_url, repository_name, review_submission, verify_submission


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class SecurityContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("maintainer", password="A-strong-example-1829!")
        cls.stranger = User.objects.create_user("stranger", password="A-strong-example-1829!")
        cls.reviewer = User.objects.create_superuser("moderator", password="A-strong-example-1829!")

    def submission(self, **changes):
        token, hashed = challenge()
        fields = dict(
            owner=self.owner,
            repository="example/project",
            title="Example",
            description_en="Example project",
            description_ru="Пример проекта",
            category="testing",
            funding_url="https://github.com/sponsors/example",
            challenge_hash=hashed,
            challenge_expires_at=timezone.now() + timedelta(hours=24),
        )
        fields.update(changes)
        return Submission.objects.create(**fields), token

    def payload(self):
        return dict(
            repository="https://github.com/example/project",
            title="Example",
            description_en="Example project",
            description_ru="Пример проекта",
            category="testing",
            funding_url="https://github.com/sponsors/example",
        )

    def post(self, path, data=None, client=None):
        return (client or self.client).post(
            "/api/" + path, data=json.dumps(data or {}), content_type="application/json"
        )

    def test_sponsorship_urls_reject_redirect_and_host_confusion(self):
        bad = [
            "http://github.com/sponsors/example",
            "https://github.com.evil.test/sponsors/a",
            "https://github.com@evil.test/sponsors/a",
            "https://github.com/sponsors/a?redirect=evil",
            "https://opencollective.com/a/donate",
            "https://localhost/a",
            "javascript:alert(1)",
            "https://github.com:443/sponsors/a",
            "https://github.com/sponsors/a#x",
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises((ValidationError, ValueError)):
                funding_url(value)

    def test_valid_funding_profiles(self):
        self.assertEqual(
            funding_url("https://github.com/sponsors/example/"),
            "https://github.com/sponsors/example",
        )
        self.assertEqual(
            funding_url("https://opencollective.com/pytest"), "https://opencollective.com/pytest"
        )

    def test_repository_ssrf_and_traversal_rejected(self):
        for value in [
            "http://127.0.0.1/a",
            "https://github.com/a/b?ref=x",
            "a/..",
            "a/../../x",
            "https://github.com.evil.test/a/b",
            "a/b/c",
        ]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                repository_name(value)

    def test_anonymous_cannot_submit(self):
        self.assertEqual(self.post("submissions/", self.payload()).status_code, 403)

    def test_csrf_required_for_login_and_registration(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            self.post("login/", {"username": "maintainer", "password": "x"}, client).status_code,
            403,
        )
        self.assertEqual(
            self.post("register/", {"username": "new", "password": "x"}, client).status_code, 403
        )

    def test_session_supplies_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get("/api/session/")
        self.assertIn("csrfToken", response.json())
        response = client.post(
            "/api/login/",
            data=json.dumps({"username": "maintainer", "password": "A-strong-example-1829!"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=response.json()["csrfToken"],
        )
        self.assertEqual(response.status_code, 200)

    def test_claim_cannot_self_approve_or_set_owner(self):
        self.client.force_login(self.owner)
        payload = self.payload() | {
            "status": "approved",
            "owner": self.reviewer.pk,
            "verified_at": timezone.now().isoformat(),
        }
        response = self.post("submissions/", payload)
        self.assertEqual(response.status_code, 201)
        row = Submission.objects.get()
        self.assertEqual(row.status, "unverified")
        self.assertEqual(row.owner_id, self.owner.pk)
        self.assertIsNone(row.verified_at)
        self.assertEqual(len(row.challenge_hash), 64)
        self.assertNotEqual(row.challenge_hash, response.json()["proof"]["token"])

    def test_stranger_cannot_read_or_verify_other_submission(self):
        row, _ = self.submission()
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get("/api/submissions/mine/").json()["submissions"], [])
        self.assertEqual(self.post(f"submissions/{row.pk}/verify/").status_code, 404)
        self.assertEqual(self.post(f"submissions/{row.pk}/challenge/").status_code, 404)

    def test_unverified_not_public(self):
        self.submission()
        self.assertEqual(self.client.get("/api/projects/").json()["projects"], [])

    def test_non_moderator_cannot_review(self):
        row, _ = self.submission(status="pending", verified_at=timezone.now())
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/api/moderation/").status_code, 403)
        self.assertEqual(
            self.post(
                f"submissions/{row.pk}/review/", {"decision": "approve", "note": "x"}
            ).status_code,
            403,
        )

    def test_unverified_cannot_be_approved(self):
        row, _ = self.submission()
        with self.assertRaises(ValidationError):
            review_submission(row.pk, self.reviewer, True, "Checked")

    def test_reviewer_cannot_approve_own_project(self):
        row, _ = self.submission(owner=self.reviewer, status="pending", verified_at=timezone.now())
        with self.assertRaises(ValidationError):
            review_submission(row.pk, self.reviewer, True, "Checked")

    def test_expired_challenge_stops_before_network(self):
        row, _ = self.submission(challenge_expires_at=timezone.now() - timedelta(seconds=1))
        with patch("directory.services.github_json") as github:
            with self.assertRaises(ValidationError):
                verify_submission(row, self.owner)
            github.assert_not_called()

    def test_forged_token_cannot_verify(self):
        row, _ = self.submission()
        with (
            patch(
                "directory.services.github_json",
                return_value={"private": False, "license": {"spdx_id": "MIT"}},
            ),
            patch("directory.services.github_file", return_value='{"token":"fake"}'),
        ):
            with self.assertRaises(ValidationError):
                verify_submission(row, self.owner)
        row.refresh_from_db()
        self.assertEqual(row.status, "unverified")

    def test_wrong_funding_destination_cannot_verify(self):
        row, token = self.submission()
        with (
            patch(
                "directory.services.github_json",
                return_value={"private": False, "license": {"spdx_id": "MIT"}},
            ),
            patch("directory.services.github_file", return_value=json.dumps({"token": token})),
            patch(
                "directory.services.public_funding",
                return_value=(
                    {"https://github.com/sponsors/other"},
                    "https://github.com/example/project",
                ),
            ),
        ):
            with self.assertRaises(ValidationError):
                verify_submission(row, self.owner)

    def test_verified_project_requires_review_then_becomes_public(self):
        row, token = self.submission()
        with (
            patch(
                "directory.services.github_json",
                return_value={
                    "private": False,
                    "license": {"spdx_id": "MIT"},
                    "language": "Python",
                },
            ),
            patch("directory.services.github_file", return_value=json.dumps({"token": token})),
            patch(
                "directory.services.public_funding",
                return_value=({row.funding_url}, "https://github.com/example/project"),
            ),
        ):
            row = verify_submission(row, self.owner)
        self.assertEqual(row.status, "pending")
        self.assertEqual(self.client.get("/api/projects/").json()["projects"], [])
        review_submission(row.pk, self.reviewer, True, "License and funding reviewed.")
        public = self.client.get("/api/projects/").json()["projects"]
        self.assertEqual(len(public), 1)
        self.assertTrue(public[0]["ownershipVerified"])
        self.assertNotIn("challenge_hash", public[0])
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_stale_verification_cannot_be_approved(self):
        row, _ = self.submission(status="pending", verified_at=timezone.now() - timedelta(days=8))
        with self.assertRaises(ValidationError):
            review_submission(row.pk, self.reviewer, True, "Reviewed")

    def test_duplicate_published_repository_rejected(self):
        self.submission(status="approved", verified_at=timezone.now())
        row, _ = self.submission(
            repository="Example/PROJECT", status="pending", verified_at=timezone.now()
        )
        with self.assertRaises(ValidationError):
            review_submission(row.pk, self.reviewer, True, "Reviewed")

    def test_rate_limit_login(self):
        for _ in range(12):
            self.post("login/", {"username": "missing", "password": "incorrect"})
        response = self.post("login/", {"username": "missing", "password": "incorrect"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "600")

    def test_security_headers(self):
        response = self.client.get("/api/session/")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_invalid_json_is_400(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(
                "/api/submissions/", data="{bad", content_type="application/json"
            ).status_code,
            400,
        )
        self.assertEqual(self.post("submissions/", []).status_code, 400)

    def test_new_challenge_invalidates_previous(self):
        row, token = self.submission()
        previous_hash = row.challenge_hash
        self.client.force_login(self.owner)
        response = self.post(f"submissions/{row.pk}/challenge/")
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertNotEqual(previous_hash, row.challenge_hash)
        self.assertNotEqual(token, response.json()["proof"]["token"])

    def test_funding_yaml_aliases_rejected(self):
        from .services import public_funding

        with patch(
            "directory.services.github_file", return_value="github: &a [example]\ncustom: *a"
        ):
            with self.assertRaises(ValidationError):
                public_funding("example/project")

    def test_github_redirects_not_followed(self):
        from .services import NoRedirect

        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://localhost/private")
        )
