from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class Submission(models.Model):
    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "Awaiting ownership verification"
        PENDING = "pending", "Awaiting moderation"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    repository = models.CharField(max_length=140)
    title = models.CharField(max_length=80)
    description_en = models.CharField(max_length=400)
    description_ru = models.CharField(max_length=400)
    category = models.CharField(
        max_length=20,
        choices=[
            ("frameworks", "Frameworks"),
            ("testing", "Testing"),
            ("networking", "Networking"),
            ("data", "Data & validation"),
            ("tooling", "Developer tools"),
        ],
    )
    funding_url = models.URLField(max_length=220)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNVERIFIED)
    challenge_hash = models.CharField(max_length=64)
    challenge_expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    license = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=40, blank=True)
    funding_source = models.URLField(max_length=300, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviews",
    )
    review_note = models.CharField(max_length=500, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_submission", "Can approve or reject verified submissions")]
        constraints = [
            models.UniqueConstraint(
                Lower("repository"),
                condition=models.Q(status="approved"),
                name="one_approved_repository",
            )
        ]

    def __str__(self):
        return self.repository


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class RateLimit(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
