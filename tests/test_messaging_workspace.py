"""Connections bind one profile and keep native credentials out of HTTP output."""

import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


class Handler:
    def __init__(self, body=None):
        encoded = json.dumps(body).encode() if body is not None else b""
        self.headers = {"Content-Length": str(len(encoded))}
        self.rfile = io.BytesIO(encoded)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key.lower()] = value

    def end_headers(self):
        pass

    def result(self):
        return json.loads(self.wfile.getvalue())


@pytest.mark.parametrize(("source", "label"), [
    ("whatsapp", "WhatsApp"), ("teams", "Microsoft Teams"),
    ("mattermost", "Mattermost"), ("whatsapp_cloud", "WhatsApp Cloud API"),
])
def test_channel_sessions_have_messaging_identity(source, label):
    from api.agent_sessions import normalize_agent_session_source

    value = normalize_agent_session_source(source)
    assert value["session_source"] == "messaging"
    assert value["source_label"] == label


def test_automation_sources_do_not_become_messaging_conversations():
    from api.agent_sessions import normalize_agent_session_source
    assert normalize_agent_session_source("api_server")["session_source"] == "api"
    assert normalize_agent_session_source("webhook")["session_source"] == "webhook"
    assert normalize_agent_session_source("unknown-plugin")["session_source"] == "other"


def test_connections_remove_credential_fragments_and_capture_profile(monkeypatch):
    from api import messaging

    calls = []

    async def platforms(profile):
        calls.append(profile)
        return {"platforms": [{"id": "whatsapp", "name": "WhatsApp", "enabled": True,
                               "configured": True, "state": "connected",
                               "whatsapp_setup": {"mode": "bot", "allowed_users_set": True}},
                              {"id": "slack", "name": "Slack", "enabled": True,
                               "configured": True, "state": "connected",
                               "env_vars": [{"key": "SLACK_BOT_TOKEN", "is_set": True,
                                             "redacted_value": "secret-prefix...suffix",
                                             "value": "never expose", "is_password": True}]}]}

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda name: SimpleNamespace(
        get_messaging_platforms=platforms))
    handler = Handler()
    messaging.handle_get(handler, urlparse("/api/messaging/connections?profile=work"))
    assert handler.status == 200
    payload = handler.result()
    assert payload["profile"] == "work"
    assert calls == ["work"]
    assert payload["platforms"][0]["id"] == "whatsapp"
    assert payload["platforms"][0]["whatsapp_setup"] == {"mode": "bot", "allowed_users_set": True}
    assert payload["platforms"][1]["env_vars"][0]["is_set"] is True
    assert "secret-prefix" not in handler.wfile.getvalue().decode()
    assert "never expose" not in handler.wfile.getvalue().decode()
    assert handler.response_headers["cache-control"] == "no-store"


def test_gateway_whatsapp_can_be_disabled_without_changing_arc_connection(monkeypatch):
    from api import messaging

    calls = []

    async def configure(platform, model, profile):
        calls.append((platform, model.enabled, model.env, model.clear_env, profile))
        return {"ok": True, "platform": platform}

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda name: (
        SimpleNamespace(update_messaging_platform=configure) if name == "messaging"
        else pytest.fail("Gateway toggle reached ARC connector")))
    handler = Handler({"enabled": False, "env": {}, "clear_env": []})
    messaging.handle_post(handler, urlparse("/api/messaging/platforms/whatsapp/configure?profile=work"))
    assert handler.status == 200, handler.result()
    assert calls == [("whatsapp", False, {}, [], "work")]


def test_arc_connector_setup_is_profile_scoped_and_hides_credential_path(monkeypatch):
    from api import messaging

    saved = []

    async def config(profile):
        return {"arc_whatsapp": {"enabled": False, "base_url": "http://127.0.0.1:9131",
                                 "token_file": "credentials/private-arc.token"}}

    async def update(model, profile):
        saved.append((model.config, model.profile, profile))
        return {"ok": True}

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda name: SimpleNamespace(
        get_config=config, update_config=update) if name == "config_env"
        else pytest.fail("Disabled ARC connector reached native account"))
    handler = Handler()
    messaging.handle_get(handler, urlparse("/api/arc/connectors?profile=work"))
    assert handler.status == 200, handler.result()
    assert handler.result()["connectors"][0] == {
        "id": "arc-whatsapp", "enabled": False, "port": 9131, "token_file_set": True,
        "connection": {"configured": False},
    }
    assert "private-arc.token" not in handler.wfile.getvalue().decode()

    handler = Handler({"enabled": True, "port": 9132, "token_file": "credentials/new-arc.token"})
    messaging.handle_post(handler, urlparse("/api/arc/connectors/whatsapp/configure?profile=work"))
    assert handler.status == 200, handler.result()
    assert saved == [({"arc_whatsapp": {"enabled": True, "base_url": "http://127.0.0.1:9132",
                                         "token_file": "credentials/new-arc.token"}}, "work", "work")]


def test_arc_connector_rejects_browser_selected_backend_and_stale_profile(monkeypatch):
    from api import messaging

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda _: pytest.fail("Invalid ARC setup reached backend"))
    handler = Handler({"enabled": True, "base_url": "http://other-host:9131"})
    messaging.handle_post(handler, urlparse("/api/arc/connectors/whatsapp/configure?profile=work"))
    assert handler.status == 400
    handler = Handler({"enabled": False})
    messaging.handle_post(handler, urlparse("/api/arc/connectors/whatsapp/configure?profile=other"))
    assert handler.status == 409


@pytest.mark.parametrize("path", ["/api/messaging/connections", "/api/arc/whatsapp/connection"])
def test_stale_profile_is_rejected_before_backend_access(monkeypatch, path):
    from api import messaging

    monkeypatch.setattr(messaging, "active_profile", lambda: "personal")
    monkeypatch.setattr(messaging, "agent_module", lambda _: pytest.fail("stale profile reached backend"))
    handler = Handler()
    messaging.handle_get(handler, urlparse(path + "?profile=work"))
    assert handler.status == 409
    assert handler.result()["detail"]["code"] == "profile_changed"


def test_platform_update_cannot_override_profile_or_choose_a_backend(monkeypatch):
    from api import messaging

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda _: pytest.fail("invalid input reached backend"))
    handler = Handler({"profile": "personal", "base_url": "http://attacker.invalid"})
    messaging.handle_post(handler, urlparse("/api/messaging/platforms/slack/configure?profile=work"))
    assert handler.status == 400


def test_unknown_native_operation_does_not_reach_backend(monkeypatch):
    from api import messaging

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda _: pytest.fail("unknown operation reached backend"))
    handler = Handler({})
    messaging.handle_post(handler, urlparse("/api/arc/whatsapp/operations/owner.exec?profile=work"))
    assert handler.status == 404


def test_native_operation_has_no_retries_and_keeps_uncertainty(monkeypatch):
    from api import messaging

    calls = []

    async def operate(operation, request, profile):
        calls.append((operation, profile, json.loads(b"".join([part async for part in request.stream()]))))
        return SimpleNamespace(status_code=504, body=json.dumps({"detail": {
            "code": "request_timeout", "message": "Inspect history before retrying.", "uncertain": True,
        }}).encode())

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda _: SimpleNamespace(operate=operate))
    handler = Handler({"roomId": "synthetic@g.us", "text": "Hello"})
    messaging.handle_post(handler, urlparse("/api/arc/whatsapp/operations/messages.send?profile=work"))
    assert handler.status == 504
    assert handler.result()["detail"]["uncertain"] is True
    assert len(calls) == 1
    assert calls[0][1] == "work"


def test_missing_agent_management_is_visible_not_an_empty_success(monkeypatch):
    from api import messaging

    monkeypatch.setattr(messaging, "active_profile", lambda: "default")

    def missing(_):
        raise ImportError("private host details must not escape")

    monkeypatch.setattr(messaging, "agent_module", missing)
    handler = Handler()
    messaging.handle_get(handler, urlparse("/api/messaging/connections?profile=default"))
    assert handler.status == 503
    assert "private host details" not in handler.wfile.getvalue().decode()


def test_conversations_preserve_profile_archive_and_hidden_visibility(monkeypatch):
    from api import messaging, models, routes

    rows = [
        {"session_id": "visible", "title": "Visible work", "profile": "work"},
        {"session_id": "hidden", "title": "Visible hidden", "profile": "work", "default_hidden": True},
        {"session_id": "archived", "title": "Visible archived", "profile": "work", "archived": True},
        {"session_id": "other", "title": "Visible other", "profile": "personal"},
    ]
    for index, row in enumerate(rows):
        row.update(raw_source="slack", source_tag="slack", source_label="Slack",
                   session_source="messaging", message_count=2, visible_message_count=2,
                   updated_at=100 + index, is_cli_session=False)
    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_previous_messaging_sessions": True})
    monkeypatch.setattr(routes, "all_sessions", lambda **_: [])
    monkeypatch.setattr(routes, "get_cli_sessions", lambda *args, **kwargs: list(rows))
    monkeypatch.setattr(models, "get_cli_sessions", lambda *args, **kwargs: list(rows))
    handler = Handler()
    messaging.handle_get(handler, urlparse("/api/messaging/conversations?profile=work&q=Visible"))
    assert handler.status == 200, handler.result()
    assert [row["session_id"] for row in handler.result()["items"]] == ["visible"]


def test_interrupted_attachment_closes_native_stream_without_appending_json(monkeypatch):
    from api import messaging

    closed = []

    async def chunks():
        yield b"partial attachment"
        raise OSError("synthetic upstream disconnected")

    async def cleanup():
        closed.append(True)

    async def media(**_):
        return SimpleNamespace(
            status_code=200, headers={"Content-Type": "application/octet-stream", "Content-Length": "100"},
            body_iterator=chunks(), background=cleanup,
        )

    monkeypatch.setattr(messaging, "active_profile", lambda: "work")
    monkeypatch.setattr(messaging, "agent_module", lambda _: SimpleNamespace(get_media=media))
    handler = Handler()
    messaging.handle_get(handler, urlparse("/api/arc/whatsapp/media?profile=work&message_id=synthetic"))
    assert handler.status == 200
    assert handler.wfile.getvalue() == b"partial attachment"
    assert handler.close_connection is True
    assert closed == [True]


def test_conversation_search_terms_do_not_enter_request_logs():
    from server import Handler as HTTPHandler

    records = []
    handler = HTTPHandler.__new__(HTTPHandler)
    handler.path = "/api/messaging/conversations?profile=work&q=synthetic-private-topic"
    handler.command = "GET"
    handler.client_address = ("127.0.0.1", 12345)
    handler.headers = {}
    handler._safe_webui_print = records.append
    handler.log_request(200)
    assert "synthetic-private-topic" not in records[0]
    assert json.loads(records[0].removeprefix("[webui] "))["path"] == "/api/messaging/conversations"
