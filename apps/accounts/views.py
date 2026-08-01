import logging

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.http import client_ip
from apps.core.permissions import IsLinkedToWhmcsClient
from apps.core.throttling import LoginRateThrottle, WriteRateThrottle
from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import AccountService

from .serializers import (
    LogoutSerializer,
    PasswordChangeSerializer,
    ProfileSerializer,
    ProfileUpdateSerializer,
    RegistrationSerializer,
    WhmcsTokenObtainPairSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class LoginView(APIView):
    """POST email+password -> JWT pair. Credentials are verified by WHMCS."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=WhmcsTokenObtainPairSerializer, responses={200: dict})
    def post(self, request):
        serializer = WhmcsTokenObtainPairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=RegistrationSerializer, responses={201: dict})
    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        payload["client_ip"] = client_ip(request)

        service = AccountService()
        whmcs_client_id = service.register(payload)
        user = User.objects.link_to_whmcs(payload["email"], whmcs_client_id)

        from .serializers import issue_tokens

        tokens = issue_tokens(user)
        tokens["user"] = {"email": user.email, "whmcs_client_id": whmcs_client_id}
        return Response(tokens, status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    """Blacklists the refresh token. Access tokens stay valid until they expire,
    which is why their TTL is short."""

    throttle_classes = [WriteRateThrottle]

    @extend_schema(request=LogoutSerializer, responses={204: None})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            pass  # Already expired or blacklisted - logout is idempotent.
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(ClientScopedAPIView):
    permission_classes = [IsLinkedToWhmcsClient]
    throttle_scope = "user"

    @extend_schema(responses={200: ProfileSerializer})
    def get(self, request):
        profile = AccountService().get_profile(self.whmcs_client_id)
        return Response(profile)

    @extend_schema(request=ProfileUpdateSerializer, responses={200: ProfileSerializer})
    def patch(self, request):
        serializer = ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = AccountService().update_profile(
            self.whmcs_client_id, serializer.validated_data
        )
        return Response(profile)


class PasswordChangeView(ClientScopedAPIView):
    permission_classes = [IsLinkedToWhmcsClient]
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=PasswordChangeSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AccountService().change_password(
            self.whmcs_client_id,
            request.user.email,
            serializer.validated_data["current_password"],
            serializer.validated_data["new_password"],
        )
        logger.info("password changed for client %s", self.whmcs_client_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactsView(ClientScopedAPIView):
    permission_classes = [IsLinkedToWhmcsClient]
    throttle_scope = "user"

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(AccountService().get_contacts(self.whmcs_client_id))


