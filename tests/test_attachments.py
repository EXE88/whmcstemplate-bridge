"""Ticket attachments: encoding, upload rules and download authorisation."""

import base64
import json

import httpx
import pytest
import respx
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.exceptions import ApplicationError
from apps.whmcs import attachments

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db

TICKET = {
    "result": "success",
    "ticketid": 7,
    "tid": "123456",
    "userid": 42,
    "subject": "Disk full",
    "status": "Open",
    "date": "2026-07-30 10:00:00",
    "attachment": "server.log",
    "replies": {
        "reply": [
            {"replyid": 90, "name": "Ali", "message": "logs attached", "attachment": "trace.txt"}
        ]
    },
}


def test_encoding_matches_the_whmcs_double_encoded_format():
    encoded = attachments.encode([("notes.txt", b"hello")])

    payload = json.loads(base64.b64decode(encoded))

    assert payload == [{"name": "notes.txt", "data": base64.b64encode(b"hello").decode()}]


def test_filenames_are_stripped_of_path_traversal():
    assert attachments.safe_filename("../../../etc/passwd.txt") == "passwd.txt"
    assert attachments.safe_filename("evil\r\nname.txt") == "evilname.txt"
    assert attachments.safe_filename("C:\\windows\\system32\\notes.log") == "notes.log"


@pytest.mark.parametrize("name", ["shell.php", "page.html", "logo.svg", "run.exe", "x.sh"])
def test_executable_and_renderable_types_are_refused(name):
    with pytest.raises(ApplicationError):
        attachments.encode([(name, b"payload")])


def test_oversized_file_is_refused(settings):
    settings.TICKET_ATTACHMENTS = {**settings.TICKET_ATTACHMENTS, "MAX_BYTES_EACH": 10}

    with pytest.raises(ApplicationError):
        attachments.encode([("big.txt", b"x" * 11)])


def test_too_many_files_are_refused(settings):
    settings.TICKET_ATTACHMENTS = {**settings.TICKET_ATTACHMENTS, "MAX_FILES": 2}

    with pytest.raises(ApplicationError):
        attachments.encode([(f"file{i}.txt", b"x") for i in range(3)])


@respx.mock
def test_reply_uploads_reach_whmcs_as_one_encoded_parameter(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=TICKET),  # ownership check
            httpx.Response(200, json={"result": "success"}),  # AddTicketReply
            httpx.Response(200, json=TICKET),  # re-read
        ]
    )

    response = auth_client.post(
        "/api/v1/support/tickets/7/replies/",
        data={
            "message": "logs attached",
            "attachments": [SimpleUploadedFile("server.log", b"boom")],
        },
        format="multipart",
    )

    assert response.status_code == 201
    sent = route.calls[1].request.content.decode()
    assert "attachments=" in sent
    assert "server.log" not in sent  # it is inside the encoded blob, not raw


@respx.mock
def test_oversized_upload_is_refused_before_whmcs_is_touched(auth_client, settings):
    """Size is read from the multipart headers, so an enormous upload is
    rejected without the file being loaded or the ticket being fetched."""
    settings.TICKET_ATTACHMENTS = {**settings.TICKET_ATTACHMENTS, "MAX_BYTES_EACH": 16}
    route = respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=TICKET))

    response = auth_client.post(
        "/api/v1/support/tickets/7/replies/",
        data={
            "message": "here you go",
            "attachments": [SimpleUploadedFile("big.log", b"x" * 64)],
        },
        format="multipart",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert route.call_count == 0


@respx.mock
def test_attachment_of_a_foreign_ticket_is_404(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json={**TICKET, "userid": 999})
    )

    response = auth_client.get(
        "/api/v1/support/tickets/7/attachment/?type=ticket&related_id=7&index=0"
    )

    assert response.status_code == 404


@respx.mock
def test_attachment_from_a_reply_outside_the_ticket_is_404(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=TICKET))

    # Reply 91 belongs to somebody else's ticket.
    response = auth_client.get(
        "/api/v1/support/tickets/7/attachment/?type=reply&related_id=91&index=0"
    )

    assert response.status_code == 404
    # GetTicketAttachment was never reached - only the ownership read happened.
    assert route.call_count == 1


def test_staff_notes_are_not_downloadable(auth_client):
    response = auth_client.get(
        "/api/v1/support/tickets/7/attachment/?type=note&related_id=7&index=0"
    )

    assert response.status_code == 400


@respx.mock
def test_download_is_forced_and_never_rendered(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=TICKET),
            httpx.Response(
                200,
                json={
                    "result": "success",
                    "filename": "server.log",
                    "data": base64.b64encode(b"boom").decode(),
                },
            ),
        ]
    )

    response = auth_client.get(
        "/api/v1/support/tickets/7/attachment/?type=reply&related_id=90&index=0"
    )

    assert response.status_code == 200
    assert response.content == b"boom"
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["X-Content-Type-Options"] == "nosniff"
