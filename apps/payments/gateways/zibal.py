"""
Zibal IPG client.

Flow (https://docs.zibal.ir):

1. ``POST /v1/request``  {merchant, amount, callbackUrl, orderId} -> trackId
2. redirect the browser to ``https://gateway.zibal.ir/start/<trackId>``
3. Zibal returns the browser to ``callbackUrl?trackId=&success=&status=&orderId=``
4. ``POST /v1/verify`` {merchant, trackId} -> the authoritative outcome

Step 4 is the only thing that decides whether money moved. The query string in
step 3 is attacker-controlled - anyone can open the callback URL with
``success=1`` - so it is used solely to know *which* payment to verify.

Amounts are in **Rial**, integer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://gateway.zibal.ir"
START_URL = BASE_URL + "/start/{track_id}"

#: Result codes we treat as "the payment is good".
RESULT_SUCCESS = 100
#: "Already verified" - a repeated verify of a payment we may have recorded.
RESULT_ALREADY_VERIFIED = 201

RESULT_MESSAGES = {
    100: "success",
    102: "merchant not found",
    103: "merchant is inactive",
    104: "merchant is invalid",
    105: "amount must be greater than 1000 rial",
    106: "invalid callback url",
    113: "amount exceeds the transaction limit",
    201: "already verified",
    202: "order was not paid or failed",
    203: "invalid track id",
}


class ZibalError(Exception):
    """Any failure talking to Zibal, or any non-success result code."""

    def __init__(self, message: str, *, result: int | None = None):
        super().__init__(message)
        self.message = message
        self.result = result


@dataclass(frozen=True, slots=True)
class ZibalVerification:
    """Outcome of a server-to-server verify."""

    paid: bool
    amount_rial: int
    result: int
    status: int
    ref_number: str
    card_number: str
    paid_at: datetime | None
    already_verified: bool

    @property
    def status_message(self) -> str:
        return RESULT_MESSAGES.get(self.result, f"result {self.result}")


class ZibalGateway:
    module = "zibal"

    def __init__(self, config: dict | None = None):
        config = config or getattr(settings, "PAYMENTS", {}).get("ZIBAL", {})
        self.merchant = config.get("MERCHANT", "")
        self.timeout = float(config.get("TIMEOUT", 20))
        self.verify_retries = int(config.get("VERIFY_RETRIES", 2))
        if not self.merchant:
            raise ZibalError("ZIBAL_MERCHANT is not configured.")

    # -- transport --------------------------------------------------------

    def _post(self, path: str, payload: dict, *, retries: int = 0) -> dict:
        url = f"{BASE_URL}{path}"
        body = {"merchant": self.merchant, **payload}
        last: Exception | None = None

        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, json=body)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                logger.warning("zibal %s attempt %s failed: %s", path, attempt + 1, exc)
                continue
            if not isinstance(data, dict):
                raise ZibalError(f"Unexpected response shape from Zibal for {path}")
            return data

        raise ZibalError(f"Zibal is unreachable ({last})") from last

    # -- steps ------------------------------------------------------------

    def request_payment(
        self,
        *,
        amount_rial: int,
        callback_url: str,
        order_id: str,
        description: str = "",
        mobile: str = "",
    ) -> str:
        """Open a payment and return the trackId."""
        if amount_rial < 1000:
            raise ZibalError("Zibal requires an amount of at least 1000 rial.")

        payload = {
            "amount": int(amount_rial),
            "callbackUrl": callback_url,
            "orderId": order_id,
            "description": description[:255],
        }
        if mobile:
            payload["mobile"] = mobile

        # Deliberately not retried: a retry would open a second payment session
        # for the same invoice.
        data = self._post("/v1/request", payload)

        result = int(data.get("result", 0))
        if result != RESULT_SUCCESS:
            raise ZibalError(
                f"Zibal refused the payment request: "
                f"{RESULT_MESSAGES.get(result, data.get('message', ''))}",
                result=result,
            )

        track_id = str(data.get("trackId") or "")
        if not track_id:
            raise ZibalError("Zibal did not return a trackId.", result=result)
        return track_id

    def start_url(self, track_id: str) -> str:
        return START_URL.format(track_id=track_id)

    def verify(self, track_id: str) -> ZibalVerification:
        """
        Ask Zibal what actually happened. Safe to repeat: a second call answers
        201 (already verified), which is treated as a paid outcome so a retried
        callback still converges on the same state.
        """
        data = self._post("/v1/verify", {"trackId": track_id}, retries=self.verify_retries)

        result = int(data.get("result", 0))
        status = int(data.get("status", 0))
        already = result == RESULT_ALREADY_VERIFIED
        paid = result in (RESULT_SUCCESS, RESULT_ALREADY_VERIFIED)

        if not paid:
            logger.info(
                "zibal verify says unpaid: track=%s result=%s (%s)",
                track_id,
                result,
                RESULT_MESSAGES.get(result, ""),
            )

        return ZibalVerification(
            paid=paid,
            amount_rial=int(data.get("amount") or 0),
            result=result,
            status=status,
            ref_number=str(data.get("refNumber") or ""),
            card_number=str(data.get("cardNumber") or ""),
            paid_at=_parse_paid_at(data.get("paidAt")),
            already_verified=already,
        )


def _parse_paid_at(value) -> datetime | None:
    """Zibal timestamps are local wall-clock time with no offset; attach the
    project timezone so the value is comparable and storable."""
    if not value:
        return None
    raw = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
