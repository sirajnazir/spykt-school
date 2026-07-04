"""Push (OneSignal) + SMS (Twilio) senders behind Protocols (01-TECH_SPEC §1 stack row "Push").

Coach escalation alerts ride these senders (PRD §6.2 class-1: push + SMS). Concrete
implementations speak HTTP via httpx with config passed as constructor args — callers
own env handling; nothing here reads the environment, at import time or otherwise.

Tests never touch the network: use the exported `RecorderPush` / `RecorderSms` fakes,
or inject an `httpx.Client` built on `httpx.MockTransport` to assert wire payloads.

Every notification payload carries a `deep_link` (02-UIUX §5: "all notifications
deep-link") — it is a required, non-empty field on `Notification` by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Notification:
    """One outbound notification. `deep_link` is mandatory (02-UIUX §5)."""

    title: str
    body: str
    deep_link: str
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.deep_link:
            raise ValueError("Notification requires a non-empty deep_link (02-UIUX §5)")


class PushSender(Protocol):
    """Push channel duck type (OneSignal in production, RecorderPush in tests)."""

    def send(self, *, to: str, notification: Notification) -> None:
        """Deliver `notification` to recipient `to` (external user id)."""
        ...


class SmsSender(Protocol):
    """SMS channel duck type (Twilio in production, RecorderSms in tests)."""

    def send(self, *, to: str, notification: Notification) -> None:
        """Deliver `notification` as a text message to phone number `to`."""
        ...


class OneSignalPush:
    """OneSignal REST push sender. Config via constructor args only (no env reads)."""

    def __init__(
        self,
        *,
        app_id: str,
        api_key: str,
        base_url: str = "https://api.onesignal.com",
        client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client if client is not None else httpx.Client(timeout=10.0)

    def send(self, *, to: str, notification: Notification) -> None:
        payload = {
            "app_id": self._app_id,
            "include_aliases": {"external_id": [to]},
            "target_channel": "push",
            "headings": {"en": notification.title},
            "contents": {"en": notification.body},
            # OneSignal opens `url` on tap — the deep link (02-UIUX §5).
            "url": notification.deep_link,
            "data": {**notification.data, "deep_link": notification.deep_link},
        }
        response = self._client.post(
            f"{self._base_url}/notifications",
            json=payload,
            headers={"Authorization": f"Key {self._api_key}"},
        )
        response.raise_for_status()


class TwilioSms:
    """Twilio Messages API sender. Config via constructor args only (no env reads)."""

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        base_url: str = "https://api.twilio.com",
        client: httpx.Client | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._base_url = base_url.rstrip("/")
        self._client = client if client is not None else httpx.Client(timeout=10.0)

    def send(self, *, to: str, notification: Notification) -> None:
        # SMS has no tap-target metadata; the deep link rides in the body text.
        body = f"{notification.title}: {notification.body} {notification.deep_link}"
        response = self._client.post(
            f"{self._base_url}/2010-04-01/Accounts/{self._account_sid}/Messages.json",
            data={"From": self._from_number, "To": to, "Body": body},
            auth=(self._account_sid, self._auth_token),
        )
        response.raise_for_status()


class RecorderPush:
    """Test fake: records (to, notification) pairs in `.sent`; never networks."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Notification]] = []

    def send(self, *, to: str, notification: Notification) -> None:
        self.sent.append((to, notification))


class RecorderSms:
    """Test fake: records (to, notification) pairs in `.sent`; never networks."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Notification]] = []

    def send(self, *, to: str, notification: Notification) -> None:
        self.sent.append((to, notification))
