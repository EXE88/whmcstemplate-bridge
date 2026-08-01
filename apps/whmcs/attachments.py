"""
Encoding and validation for ticket attachments.

WHMCS wants ``attachments`` as *base64( json( [ {name, data: base64(bytes)} ] ) )*
- a double encoding that is easy to get subtly wrong, so it lives in one place
with tests around it.

Validation happens here rather than in the serializer because the same rules
must hold whoever calls the service (view, Celery task, management command).
"""

import base64
import json
import logging
import os
from dataclasses import dataclass

from django.conf import settings

from apps.core.exceptions import ApplicationError

logger = logging.getLogger(__name__)

#: Extensions WHMCS itself accepts by default. Keep this list narrower than the
#: WHMCS setting, never wider - it is the first gate, not the only one.
DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".pdf", ".txt", ".log", ".csv",
        ".doc", ".docx", ".xls", ".xlsx",
        ".zip", ".gz", ".rar",
    }
)

#: Extensions that must never be accepted even if someone widens the allowlist:
#: anything a browser or a server might execute.
FORBIDDEN_EXTENSIONS = frozenset(
    {
        ".php", ".php3", ".php4", ".php5", ".phtml", ".phar",
        ".htm", ".html", ".xhtml", ".svg", ".xml",
        ".js", ".mjs", ".htaccess",
        ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi",
        ".sh", ".bash", ".py", ".pl", ".rb", ".jsp", ".asp", ".aspx",
    }
)


@dataclass(frozen=True, slots=True)
class AttachmentLimits:
    max_files: int = 5
    max_bytes_each: int = 5 * 1024 * 1024
    max_bytes_total: int = 15 * 1024 * 1024

    @classmethod
    def from_settings(cls) -> "AttachmentLimits":
        config = getattr(settings, "TICKET_ATTACHMENTS", {}) or {}
        return cls(
            max_files=config.get("MAX_FILES", cls.max_files),
            max_bytes_each=config.get("MAX_BYTES_EACH", cls.max_bytes_each),
            max_bytes_total=config.get("MAX_BYTES_TOTAL", cls.max_bytes_total),
        )


def allowed_extensions() -> frozenset[str]:
    configured = getattr(settings, "TICKET_ATTACHMENTS", {}).get("ALLOWED_EXTENSIONS")
    if not configured:
        return DEFAULT_ALLOWED_EXTENSIONS
    return frozenset(ext.lower() for ext in configured) - FORBIDDEN_EXTENSIONS


def safe_filename(raw: str) -> str:
    """
    Reduce a client-supplied filename to a bare, harmless name.

    Directory components are dropped (``../../etc/passwd`` becomes
    ``passwd``), control characters and separators are stripped, and the result
    is length-capped. WHMCS stores the name we send, so a traversal sequence or
    a newline must never survive this function.
    """
    name = os.path.basename(str(raw).replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '/\\:*?"<>|\r\n')
    name = name.lstrip(".") or "attachment"
    stem, dot, ext = name.rpartition(".")
    if dot:
        name = f"{stem[:80]}.{ext[:10]}"
    return name[:100]


def validate(name: str, content: bytes, limits: AttachmentLimits | None = None) -> str:
    """Check one file and return the sanitised filename."""
    limits = limits or AttachmentLimits.from_settings()
    filename = safe_filename(name)
    extension = os.path.splitext(filename)[1].lower()

    if not extension:
        raise ApplicationError(
            f"{filename!r} has no file extension.", code="attachment_rejected"
        )
    if extension in FORBIDDEN_EXTENSIONS:
        raise ApplicationError(
            f"{extension} files are not accepted.", code="attachment_rejected"
        )
    if extension not in allowed_extensions():
        raise ApplicationError(
            f"{extension} files are not accepted.", code="attachment_rejected"
        )
    if not content:
        raise ApplicationError(f"{filename!r} is empty.", code="attachment_rejected")
    if len(content) > limits.max_bytes_each:
        raise ApplicationError(
            f"{filename!r} is larger than {limits.max_bytes_each // (1024 * 1024)}MB.",
            code="attachment_too_large",
        )
    return filename


def encode(files: list[tuple[str, bytes]], limits: AttachmentLimits | None = None) -> str:
    """
    Validate a batch and produce the WHMCS ``attachments`` parameter.

    ``files`` is ``[(filename, raw_bytes), ...]``.
    """
    limits = limits or AttachmentLimits.from_settings()

    if len(files) > limits.max_files:
        raise ApplicationError(
            f"At most {limits.max_files} attachments per message.",
            code="too_many_attachments",
        )
    total = sum(len(content) for _, content in files)
    if total > limits.max_bytes_total:
        raise ApplicationError(
            f"Attachments exceed {limits.max_bytes_total // (1024 * 1024)}MB in total.",
            code="attachment_too_large",
        )

    payload = [
        {
            "name": validate(name, content, limits),
            "data": base64.b64encode(content).decode(),
        }
        for name, content in files
    ]
    logger.info("encoding %s attachment(s), %s bytes total", len(payload), total)
    return base64.b64encode(json.dumps(payload).encode()).decode()


def decode(data: str) -> bytes:
    """Decode the base64 blob WHMCS returns for a stored attachment."""
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(
            "The attachment could not be read.", code="attachment_unreadable"
        ) from exc
