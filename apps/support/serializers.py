import bleach
from rest_framework import serializers

from apps.whmcs.attachments import AttachmentLimits

TICKET_PRIORITIES = ("Low", "Medium", "High")
TICKET_STATUSES = ("Open", "Answered", "Customer-Reply", "Closed", "In Progress", "On Hold")


def sanitize(value: str) -> str:
    """Strip every tag: ticket bodies are stored and later rendered by WHMCS
    staff tooling, so they must not carry markup."""
    return bleach.clean(value, tags=[], attributes={}, strip=True).strip()


class TicketQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TICKET_STATUSES, required=False)


class AttachmentFileField(serializers.FileField):
    """
    A single upload, size-checked *before* anything reads it.

    Django caps form fields but not the size of an uploaded file, so without
    this a 2GB upload would be spooled to disk and then pulled into memory by
    the encoder. ``UploadedFile.size`` is known from the multipart headers, so
    the request is refused without touching the payload.
    """

    def to_internal_value(self, data):
        upload = super().to_internal_value(data)
        limit = AttachmentLimits.from_settings().max_bytes_each
        if upload.size > limit:
            raise serializers.ValidationError(
                f"{upload.name!r} is larger than {limit // (1024 * 1024)}MB."
            )
        return upload


class AttachmentListField(serializers.ListField):
    """Uploaded files. Content rules live in ``apps.whmcs.attachments`` so the
    same limits apply however the service is called."""

    child = AttachmentFileField(max_length=255, allow_empty_file=False)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("max_length", AttachmentLimits.from_settings().max_files)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        uploads = super().to_internal_value(data)
        total = sum(upload.size for upload in uploads)
        limit = AttachmentLimits.from_settings().max_bytes_total
        if total > limit:
            raise serializers.ValidationError(
                f"Attachments exceed {limit // (1024 * 1024)}MB in total."
            )
        return uploads


class TicketCreateSerializer(serializers.Serializer):
    department_id = serializers.IntegerField(min_value=1)
    subject = serializers.CharField(max_length=200)
    message = serializers.CharField(max_length=20000)
    priority = serializers.ChoiceField(choices=TICKET_PRIORITIES, default="Medium")
    service_id = serializers.IntegerField(min_value=1, required=False)
    attachments = AttachmentListField()

    def validate_subject(self, value: str) -> str:
        return sanitize(value)

    def validate_message(self, value: str) -> str:
        cleaned = sanitize(value)
        if len(cleaned) < 5:
            raise serializers.ValidationError("Message is too short.")
        return cleaned


class TicketReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=20000)
    attachments = AttachmentListField()

    def validate_message(self, value: str) -> str:
        cleaned = sanitize(value)
        if len(cleaned) < 2:
            raise serializers.ValidationError("Message is too short.")
        return cleaned


class AttachmentQuerySerializer(serializers.Serializer):
    # "note" is intentionally absent: notes are staff-internal.
    type = serializers.ChoiceField(choices=["ticket", "reply"])
    related_id = serializers.IntegerField(min_value=1)
    index = serializers.IntegerField(min_value=0, max_value=50)


class TicketSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    ticket_number = serializers.CharField(read_only=True)
    department = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    created_at = serializers.CharField(read_only=True)
    updated_at = serializers.CharField(read_only=True)
