"""
Input validation for the auth endpoints.

Everything the SPA sends is validated here *before* a service runs, so no
unvalidated value ever reaches a WHMCS parameter.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.whmcs.services import AccountService

User = get_user_model()


def issue_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    # Carry the WHMCS id in the token so permission checks need no DB hit; the
    # value is still re-read from the user row before any upstream call.
    refresh["whmcs_client_id"] = user.whmcs_client_id
    refresh["email"] = user.email
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class WhmcsTokenObtainPairSerializer(serializers.Serializer):
    """Authenticates against WHMCS ``ValidateLogin`` and mints our own JWT pair.

    WHMCS never sees the token; the bridge never sees a stored password.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate(self, attrs: dict) -> dict:
        result = AccountService().validate_login(attrs["email"], attrs["password"])

        user = User.objects.link_to_whmcs(attrs["email"], result["whmcs_client_id"])
        if not user.is_active:
            raise serializers.ValidationError("This account is disabled.")

        tokens = issue_tokens(user)
        tokens["user"] = {
            "email": user.email,
            "whmcs_client_id": user.whmcs_client_id,
            "two_factor_enabled": result["two_factor_enabled"],
        }
        return tokens


class RegistrationSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=60)
    last_name = serializers.CharField(max_length=60)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    company = serializers.CharField(max_length=100, required=False, allow_blank=True)
    address1 = serializers.CharField(max_length=150, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80, required=False, allow_blank=True)
    state = serializers.CharField(max_length=80, required=False, allow_blank=True)
    postcode = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.CharField(min_length=2, max_length=2, required=False, allow_blank=True)
    phone = serializers.RegexField(r"^\+?[0-9 \-()]{6,20}$", required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value


class ProfileSerializer(serializers.Serializer):
    """Read shape of the WHMCS client profile (output only)."""

    whmcs_client_id = serializers.IntegerField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    company = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    address1 = serializers.CharField(read_only=True)
    address2 = serializers.CharField(read_only=True)
    city = serializers.CharField(read_only=True)
    state = serializers.CharField(read_only=True)
    postcode = serializers.CharField(read_only=True)
    country = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    currency_code = serializers.CharField(read_only=True)
    credit = serializers.CharField(read_only=True)
    created_at = serializers.CharField(read_only=True)


class ProfileUpdateSerializer(serializers.Serializer):
    """Only these fields may be written; anything else is rejected upfront."""

    first_name = serializers.CharField(max_length=60, required=False)
    last_name = serializers.CharField(max_length=60, required=False)
    company = serializers.CharField(max_length=100, required=False, allow_blank=True)
    address1 = serializers.CharField(max_length=150, required=False)
    address2 = serializers.CharField(max_length=150, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80, required=False)
    state = serializers.CharField(max_length=80, required=False)
    postcode = serializers.CharField(max_length=20, required=False)
    country = serializers.CharField(min_length=2, max_length=2, required=False)
    phone = serializers.RegexField(r"^\+?[0-9 \-()]{6,20}$", required=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, max_length=128)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, attrs: dict) -> dict:
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError("The new password must be different.")
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
