"""
Local user record.

WHMCS stays the system of record for customers: it owns the password, the
profile and the billing relationship. This table exists only to give Django
something to hang a JWT identity on, and to cache the *link* between an email
and a WHMCS client id.

Deliberately absent: customer passwords. ``set_unusable_password`` is called on
every user, and authentication always round-trips to WHMCS' ``ValidateLogin``.
Staff/superusers are the exception - they log into /admin with a local password.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str, **extra):
        extra.update(is_staff=True, is_superuser=True, is_active=True)
        return self._create(email, password, **extra)

    def link_to_whmcs(self, email: str, whmcs_client_id: int):
        """Get-or-create the local mirror of a WHMCS client."""
        email = self.normalize_email(email).lower()
        user, created = self.get_or_create(
            email=email,
            defaults={"whmcs_client_id": whmcs_client_id},
        )
        if not created and user.whmcs_client_id != whmcs_client_id:
            user.whmcs_client_id = whmcs_client_id
            user.save(update_fields=["whmcs_client_id"])
        if user.has_usable_password() and not user.is_staff:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, db_index=True)
    whmcs_client_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Client id in WHMCS. Every upstream call is scoped by it.",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"
        verbose_name = "user"

    def __str__(self) -> str:
        return self.email

    def mark_synced(self) -> None:
        self.last_synced_at = timezone.now()
        self.save(update_fields=["last_synced_at"])
