import bleach
from rest_framework import serializers

TICKET_PRIORITIES = ("Low", "Medium", "High")
TICKET_STATUSES = ("Open", "Answered", "Customer-Reply", "Closed", "In Progress", "On Hold")


def sanitize(value: str) -> str:
    """Strip every tag: ticket bodies are stored and later rendered by WHMCS
    staff tooling, so they must not carry markup."""
    return bleach.clean(value, tags=[], attributes={}, strip=True).strip()


class TicketQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TICKET_STATUSES, required=False)


class TicketCreateSerializer(serializers.Serializer):
    department_id = serializers.IntegerField(min_value=1)
    subject = serializers.CharField(max_length=200)
    message = serializers.CharField(max_length=20000)
    priority = serializers.ChoiceField(choices=TICKET_PRIORITIES, default="Medium")
    service_id = serializers.IntegerField(min_value=1, required=False)

    def validate_subject(self, value: str) -> str:
        return sanitize(value)

    def validate_message(self, value: str) -> str:
        cleaned = sanitize(value)
        if len(cleaned) < 5:
            raise serializers.ValidationError("Message is too short.")
        return cleaned


class TicketReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=20000)

    def validate_message(self, value: str) -> str:
        cleaned = sanitize(value)
        if len(cleaned) < 2:
            raise serializers.ValidationError("Message is too short.")
        return cleaned


class TicketSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    ticket_number = serializers.CharField(read_only=True)
    department = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    created_at = serializers.CharField(read_only=True)
    updated_at = serializers.CharField(read_only=True)
