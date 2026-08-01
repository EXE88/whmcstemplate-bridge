from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    ContactsView,
    LoginView,
    LogoutView,
    PasswordChangeView,
    ProfileView,
    RegisterView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    path("refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("verify/", TokenVerifyView.as_view(), name="verify"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", ProfileView.as_view(), name="me"),
    path("me/password/", PasswordChangeView.as_view(), name="password-change"),
    path("me/contacts/", ContactsView.as_view(), name="contacts"),
]
