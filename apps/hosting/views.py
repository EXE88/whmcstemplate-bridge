from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import CatalogueService, DomainService, ServiceService

from .serializers import (
    CancellationSerializer,
    DomainLockSerializer,
    DomainLookupSerializer,
    NameserverSerializer,
    ServicePasswordSerializer,
    ServiceSerializer,
)


# --------------------------------------------------------------------------
# Public catalogue - no auth, aggressively cached, anon-throttled.
# --------------------------------------------------------------------------
class ProductListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "anon"

    @extend_schema(responses={200: dict})
    def get(self, request):
        group_id = request.query_params.get("group_id")
        return Response(
            CatalogueService().list_products(
                group_id=int(group_id) if group_id and group_id.isdigit() else None
            )
        )



class TldPricingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "anon"

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(CatalogueService().tld_pricing())


class DomainLookupView(APIView):
    """Availability check. Public, so it is rate limited and cached."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "anon"

    @extend_schema(parameters=[DomainLookupSerializer], responses={200: dict})
    def get(self, request):
        query = DomainLookupSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(DomainService().whois(query.validated_data["domain"]))


# --------------------------------------------------------------------------
# Customer services
# --------------------------------------------------------------------------
class ServiceListView(ClientScopedAPIView):
    @extend_schema(responses={200: ServiceSerializer})
    def get(self, request):
        items, total = ServiceService().list_services(
            self.whmcs_client_id, self.page(), request.query_params.get("status")
        )
        return self.paginated(items, total)


class ServiceDetailView(ClientScopedAPIView):
    @extend_schema(responses={200: ServiceSerializer})
    def get(self, request, service_id: int):
        return Response(ServiceService().get_service(self.whmcs_client_id, service_id))


class ServicePasswordView(ClientScopedAPIView):
    @extend_schema(request=ServicePasswordSerializer, responses={204: None})
    def post(self, request, service_id: int):
        serializer = ServicePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ServiceService().change_password(
            self.whmcs_client_id, service_id, serializer.validated_data["new_password"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ServiceCancellationView(ClientScopedAPIView):
    @extend_schema(request=CancellationSerializer, responses={202: None})
    def post(self, request, service_id: int):
        serializer = CancellationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ServiceService().request_cancellation(
            self.whmcs_client_id,
            service_id,
            reason=serializer.validated_data["reason"],
            immediate=serializer.validated_data["immediate"],
        )
        return Response({"status": "requested"}, status=status.HTTP_202_ACCEPTED)


# --------------------------------------------------------------------------
# Customer domains
# --------------------------------------------------------------------------
class DomainListView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        items, total = DomainService().list_domains(self.whmcs_client_id, self.page())
        return self.paginated(items, total)


class DomainDetailView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request, domain_id: int):
        return Response(DomainService().get_domain(self.whmcs_client_id, domain_id))


class DomainNameserverView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request, domain_id: int):
        return Response(
            {"nameservers": DomainService().get_nameservers(self.whmcs_client_id, domain_id)}
        )

    @extend_schema(request=NameserverSerializer, responses={200: dict})
    def put(self, request, domain_id: int):
        serializer = NameserverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nameservers = DomainService().update_nameservers(
            self.whmcs_client_id, domain_id, serializer.validated_data["nameservers"]
        )
        return Response({"nameservers": nameservers})


class DomainLockView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request, domain_id: int):
        return Response(
            {"locked": DomainService().get_lock_status(self.whmcs_client_id, domain_id)}
        )

    @extend_schema(request=DomainLockSerializer, responses={200: dict})
    def put(self, request, domain_id: int):
        serializer = DomainLockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        locked = DomainService().set_lock_status(
            self.whmcs_client_id, domain_id, serializer.validated_data["locked"]
        )
        return Response({"locked": locked})


class DomainEppView(ClientScopedAPIView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request, domain_id: int):
        return Response(DomainService().request_epp_code(self.whmcs_client_id, domain_id))
