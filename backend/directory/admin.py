from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import AuditEvent, Submission
from .services import review_submission


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["repository", "owner", "status", "verified_at", "created_at"]
    list_filter = ["status", "category"]
    search_fields = ["repository", "owner__username"]
    readonly_fields = [f.name for f in Submission._meta.fields]
    actions = ["approve", "reject"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _review(self, request, queryset, approve):
        if not request.user.has_perm("directory.review_submission"):
            self.message_user(request, "Review permission required.", messages.ERROR)
            return
        for row in queryset:
            try:
                review_submission(
                    row.pk,
                    request.user,
                    approve,
                    "Approved after manual review in admin."
                    if approve
                    else "Rejected in admin. Contact the platform administrator for details.",
                )
            except ValidationError as exc:
                self.message_user(
                    request, f"{row.repository}: {' '.join(exc.messages)}", messages.ERROR
                )

    @admin.action(description="Approve verified submissions")
    def approve(self, request, queryset):
        self._review(request, queryset, True)

    @admin.action(description="Reject pending submissions")
    def reject(self, request, queryset):
        self._review(request, queryset, False)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "submission", "action"]
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
