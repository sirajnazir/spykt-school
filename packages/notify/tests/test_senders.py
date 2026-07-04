"""Sender tests (02-UIUX §5 deep-link requirement; 01-TECH_SPEC §1 push/SMS stack).

No network anywhere: Recorder fakes for behavior, httpx.MockTransport for wire shape.
"""

import httpx
import pytest

from spykt_notify import Notification, OneSignalPush, RecorderPush, RecorderSms, TwilioSms


def make_notification(**overrides) -> Notification:
    kwargs = {
        "title": "Class-1 wellbeing escalation",
        "body": "Acknowledge within 15 minutes.",
        "deep_link": "/coach/escalations/abc-123",
    }
    kwargs.update(overrides)
    return Notification(**kwargs)


class TestNotificationPayload:
    def test_deep_link_is_required_non_empty(self):
        """02-UIUX §5: all notifications deep-link — empty deep_link is a construction error."""
        with pytest.raises(ValueError, match="deep_link"):
            make_notification(deep_link="")

    def test_deep_link_field_present_on_every_payload(self):
        note = make_notification()
        assert note.deep_link == "/coach/escalations/abc-123"


class TestRecorders:
    def test_recorder_push_records_without_network(self):
        push = RecorderPush()
        note = make_notification()
        push.send(to="coach-1", notification=note)
        assert push.sent == [("coach-1", note)]

    def test_recorder_sms_records_without_network(self):
        sms = RecorderSms()
        note = make_notification()
        sms.send(to="+15550001111", notification=note)
        assert sms.sent == [("+15550001111", note)]


class TestOneSignalPush:
    def _sender_with_capture(self, status_code: int = 200):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(status_code, json={"id": "n1"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        sender = OneSignalPush(
            app_id="app-1", api_key="key-1", base_url="https://api.onesignal.test", client=client
        )
        return sender, captured

    def test_posts_notification_with_deep_link(self):
        import json

        sender, captured = self._sender_with_capture()
        sender.send(to="coach-1", notification=make_notification())

        (request,) = captured
        assert request.method == "POST"
        assert str(request.url) == "https://api.onesignal.test/notifications"
        assert request.headers["Authorization"] == "Key key-1"
        payload = json.loads(request.content)
        assert payload["app_id"] == "app-1"
        assert payload["include_aliases"] == {"external_id": ["coach-1"]}
        assert payload["url"] == "/coach/escalations/abc-123"
        assert payload["data"]["deep_link"] == "/coach/escalations/abc-123"
        assert payload["contents"] == {"en": "Acknowledge within 15 minutes."}

    def test_http_error_raises(self):
        sender, _ = self._sender_with_capture(status_code=500)
        with pytest.raises(httpx.HTTPStatusError):
            sender.send(to="coach-1", notification=make_notification())


class TestTwilioSms:
    def _sender_with_capture(self, status_code: int = 201):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(status_code, json={"sid": "SM1"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        sender = TwilioSms(
            account_sid="AC123",
            auth_token="tok",
            from_number="+15550009999",
            base_url="https://api.twilio.test",
            client=client,
        )
        return sender, captured

    def test_posts_message_with_deep_link_in_body(self):
        from urllib.parse import parse_qs

        sender, captured = self._sender_with_capture()
        sender.send(to="+15550001111", notification=make_notification())

        (request,) = captured
        assert request.method == "POST"
        assert str(request.url) == "https://api.twilio.test/2010-04-01/Accounts/AC123/Messages.json"
        form = parse_qs(request.content.decode())
        assert form["From"] == ["+15550009999"]
        assert form["To"] == ["+15550001111"]
        assert "/coach/escalations/abc-123" in form["Body"][0]

    def test_http_error_raises(self):
        sender, _ = self._sender_with_capture(status_code=400)
        with pytest.raises(httpx.HTTPStatusError):
            sender.send(to="+15550001111", notification=make_notification())
