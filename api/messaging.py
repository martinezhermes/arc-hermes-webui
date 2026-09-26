"""Authenticated WebUI binding for the installed agent's messaging services.

This adapter reuses the agent's profile/configuration and registered-principal
handlers. It never starts a dashboard, pairs a second native session, forwards
browser credentials, or accepts a caller-selected backend URL.
"""

import asyncio
import importlib
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

from api.helpers import j

MAX_BODY = 512 * 1024
ARC_OPERATIONS = frozenset({
    "profile.get", "groups.list", "groups.get", "contacts.list", "contacts.get",
    "messages.recent", "messages.details", "messages.send", "messages.react",
    "messages.reactions", "messages.mark_read",
})
_PLATFORM_KEYS = ("id", "name", "description", "enabled", "configured", "state",
                  "gateway_running", "error_code", "updated_at")
_FIELD_KEYS = ("key", "required", "is_set", "description", "prompt", "is_password", "advanced")
logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def active_profile():
    from api.profiles import get_active_profile_name
    return get_active_profile_name()


def agent_module(name):
    return importlib.import_module("hermes_cli.web_routers." + name)


def _profile(parsed, *, mutation=False):
    current = active_profile()
    values = parse_qs(parsed.query, keep_blank_values=True).get("profile", [])
    if len(values) > 1 or (values and values[0] != current):
        raise WorkspaceError("profile_changed", "The active profile changed. Refresh this view before continuing.", 409)
    if mutation and not values:
        raise WorkspaceError("profile_required", "Select a profile before changing messaging settings.")
    return current


def _body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 <= length <= MAX_BODY:
            handler.close_connection = True
            raise WorkspaceError("body_too_large", "Messaging requests are limited to 512 KiB.", 413)
        value = json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, UnicodeError):
        handler.close_connection = True
        raise WorkspaceError("invalid_body", "Expected a JSON argument object.") from None
    if not isinstance(value, dict):
        raise WorkspaceError("invalid_body", "Expected a JSON argument object.")
    return value


class _Request:
    """The body-only request consumed by existing agent operation handlers."""

    def __init__(self, body, method="POST"):
        self.body, self.method = body, method

    async def stream(self):
        yield json.dumps(self.body).encode()


def _reply(handler, value, status=200):
    return j(handler, value, status=status, extra_headers={"Cache-Control": "no-store"})


def _unpack(value):
    if hasattr(value, "status_code") and hasattr(value, "body"):
        return json.loads(value.body), value.status_code
    return value, 200


async def _connections(profile):
    result = await agent_module("messaging").get_messaging_platforms(profile=profile)
    platforms = []
    for item in result.get("platforms", []):
        public = {key: item[key] for key in _PLATFORM_KEYS if key in item}
        if item.get("id") == "whatsapp" and isinstance(item.get("whatsapp_setup"), dict):
            public["whatsapp_setup"] = {
                key: item["whatsapp_setup"][key]
                for key in ("mode", "allowed_users_set", "home_channel_set")
                if key in item["whatsapp_setup"]
            }
        public["env_vars"] = [
            {key: field[key] for key in _FIELD_KEYS if key in field}
            for field in item.get("env_vars", [])
        ]
        platforms.append(public)
    return {"profile": profile, "platforms": platforms}


def _arc_settings(config):
    value = config.get("arc_whatsapp", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def _arc_port(settings):
    try:
        parsed = urlsplit(settings.get("base_url", "http://127.0.0.1:9131"))
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port:
            return parsed.port
    except (TypeError, ValueError):
        pass
    return None


async def _arc_connectors(profile):
    settings = _arc_settings(await agent_module("config_env").get_config(profile=profile))
    enabled = settings.get("enabled") is True
    connection = {"configured": False}
    if enabled:
        try:
            connection, status = _unpack(await agent_module("arc_whatsapp").get_connection(profile=profile))
            if status != 200:
                connection = {"configured": True, "error": connection.get("detail", {})}
        except Exception as error:
            logger.warning("ARC connector status failed (%s)", type(error).__name__)
            connection = {"configured": True, "error": {
                "code": "backend_error", "message": "The ARC WhatsApp connection could not be checked.",
            }}
    return {"profile": profile, "connectors": [{
        "id": "arc-whatsapp", "enabled": enabled, "port": _arc_port(settings),
        "token_file_set": bool(settings.get("token_file")), "connection": connection,
    }]}


async def _configure_arc_connector(profile, body):
    if set(body) - {"enabled", "port", "token_file"} or not body:
        raise WorkspaceError("invalid_body", "Unsupported ARC connector setting.")
    if "enabled" in body and type(body["enabled"]) is not bool:
        raise WorkspaceError("invalid_body", "Enabled must be true or false.")
    config = _arc_settings(await agent_module("config_env").get_config(profile=profile))
    update = {}
    if "enabled" in body:
        update["enabled"] = body["enabled"]
    if "port" in body:
        if type(body["port"]) is not int or not 1 <= body["port"] <= 65535:
            raise WorkspaceError("invalid_port", "Choose a valid local ARC API port.")
        update["base_url"] = f"http://127.0.0.1:{body['port']}"
    if "token_file" in body:
        path = body["token_file"]
        if not isinstance(path, str) or not 1 <= len(path.strip()) <= 1024 or any(ord(c) < 32 for c in path):
            raise WorkspaceError("invalid_token_file", "Choose an existing private ARC credential file path.")
        update["token_file"] = path.strip()
    if update.get("enabled", config.get("enabled") is True) and not update.get("token_file", config.get("token_file")):
        raise WorkspaceError("missing_token_file", "Configure a registered ARC credential file before enabling.")
    from hermes_cli.web_models import ConfigUpdate
    result = await agent_module("config_env").update_config(
        ConfigUpdate(config={"arc_whatsapp": update}, profile=profile), profile=profile)
    return result


def _conversations(parsed, profile):
    from api.agent_sessions import MESSAGING_SOURCES, SOURCE_LABELS
    from api import routes
    query = parse_qs(parsed.query)
    search = query.get("q", [""])[0].casefold()
    platform = query.get("platform", [""])[0]
    try:
        offset = max(0, int(query.get("offset", ["0"])[0]))
        limit = min(100, max(1, int(query.get("limit", ["50"])[0])))
    except ValueError:
        raise WorkspaceError("invalid_page", "Invalid conversation page.") from None
    # Share the sidebar's visibility, archive, deletion, lineage and redaction
    # policy. Reading raw state.db rows here would resurrect hidden sessions.
    settings = routes.load_settings()
    payload = routes._build_session_list_cache_payload(
        active_profile=profile, all_profiles=False, show_cli_sessions=True,
        show_previous_messaging_sessions=bool(settings.get("show_previous_messaging_sessions")),
        show_cron_sessions=False, show_claude_code_sessions=False,
        include_archived=False, exclude_hidden=True, visible_only=True,
    )
    public = routes._session_list_payload_to_response(payload)
    rows = []
    for item in public.get("sessions", []):
        source = str(item.get("raw_source") or item.get("source_tag") or item.get("source") or "").lower()
        if source not in MESSAGING_SOURCES or (platform and source != platform):
            continue
        if search and search not in str(item.get("title") or "").casefold():
            continue
        rows.append({key: item.get(key) for key in (
            "session_id", "title", "source_label", "raw_source", "source_tag", "profile",
            "updated_at", "message_count",
        )})
    end = offset + limit
    return {"profile": profile, "items": rows[offset:end], "total": len(rows),
            "next_offset": end if end < len(rows) else None, "coverage": "agent-sessions",
            "platforms": [{"id": source, "name": SOURCE_LABELS.get(source, source.title())}
                          for source in sorted(MESSAGING_SOURCES)]}


async def _media(handler, profile, message_id):
    response = await agent_module("arc_whatsapp").get_media(message_id=message_id, profile=profile)
    if not hasattr(response, "body_iterator"):
        payload, status = _unpack(response)
        return _reply(handler, payload, status)
    try:
        handler.send_response(response.status_code)
        for key, value in response.headers.items():
            handler.send_header(key, value)
        handler.end_headers()
        async for chunk in response.body_iterator:
            handler.wfile.write(chunk)
        return True
    except Exception:
        # Headers may already advertise binary bytes. End the incomplete
        # transfer instead of appending a second JSON response to the file.
        handler.close_connection = True
        logger.warning("ARC attachment transfer did not complete")
        return True
    finally:
        if response.background:
            await response.background()


def _failure(handler, error, *, mutation=False):
    if isinstance(error, WorkspaceError):
        return _reply(handler, {"detail": {"code": error.code, "message": str(error)}}, error.status)
    if isinstance(error, ImportError):
        return _reply(handler, {"detail": {
            "code": "backend_unavailable",
            "message": "This server needs the ARC Hermes messaging management integration.",
        }}, 503)
    if isinstance(error, ValueError):
        return _reply(handler, {"detail": {
            "code": "invalid_configuration", "message": "The configuration values are invalid.",
        }}, 400)
    # Agent HTTPException / validation errors must not echo credential inputs,
    # native connection URLs, or a credential-bearing validation representation.
    status = getattr(error, "status_code", 502)
    status = status if isinstance(status, int) and 400 <= status <= 599 else 502
    return _reply(handler, {"detail": {
        "code": "backend_error", "message": "The messaging request failed. Check the selected account and configuration.",
        "uncertain": mutation and status >= 500,
    }}, status)


def handles(path):
    return path.startswith(("/api/messaging/", "/api/arc/whatsapp/", "/api/arc/connectors"))


def handle_get(handler, parsed):
    if not handles(parsed.path):
        return False
    try:
        profile = _profile(parsed)
        if parsed.path == "/api/messaging/connections":
            return _reply(handler, asyncio.run(_connections(profile)))
        if parsed.path == "/api/messaging/conversations":
            return _reply(handler, _conversations(parsed, profile))
        if parsed.path == "/api/arc/connectors":
            return _reply(handler, asyncio.run(_arc_connectors(profile)))
        if parsed.path == "/api/arc/whatsapp/connection":
            payload, status = _unpack(asyncio.run(agent_module("arc_whatsapp").get_connection(profile=profile)))
            return _reply(handler, payload, status)
        if parsed.path == "/api/arc/whatsapp/media":
            ids = parse_qs(parsed.query).get("message_id", [])
            if len(ids) != 1 or not 1 <= len(ids[0]) <= 1024:
                raise WorkspaceError("invalid_message", "Select an exact message attachment.")
            return asyncio.run(_media(handler, profile, ids[0]))
        raise WorkspaceError("not_found", "Unsupported messaging route.", 404)
    except (BrokenPipeError, ConnectionResetError):
        return True
    except Exception as error:
        return _failure(handler, error)


def handle_post(handler, parsed):
    if not handles(parsed.path):
        return False
    try:
        profile = _profile(parsed, mutation=True)
        body = _body(handler)
        if parsed.path == "/api/arc/connectors/whatsapp/configure":
            return _reply(handler, asyncio.run(_configure_arc_connector(profile, body)))
        match = re.fullmatch(r"/api/messaging/platforms/([a-z0-9_-]+)/(configure|test)", parsed.path)
        if match:
            platform, action = match.groups()
            if set(body) - {"enabled", "env", "clear_env"}:
                raise WorkspaceError("invalid_body", "Unsupported messaging configuration field.")
            service = agent_module("messaging")
            if action == "configure":
                from hermes_cli.web_models import MessagingPlatformUpdate
                model = MessagingPlatformUpdate(**body, profile=profile)
                result = asyncio.run(service.update_messaging_platform(platform, model, profile=profile))
            else:
                result = asyncio.run(service.test_messaging_platform(platform, profile=profile))
            payload, status = _unpack(result)
            return _reply(handler, payload, status)
        match = re.fullmatch(r"/api/arc/whatsapp/operations/([a-z_.]+)", parsed.path)
        if not match or match[1] not in ARC_OPERATIONS:
            raise WorkspaceError("not_found", "Unsupported native operation.", 404)
        response = asyncio.run(agent_module("arc_whatsapp").operate(match[1], _Request(body), profile=profile))
        payload, status = _unpack(response)
        return _reply(handler, payload, status)
    except (BrokenPipeError, ConnectionResetError):
        return True
    except Exception as error:
        return _failure(handler, error, mutation=True)
