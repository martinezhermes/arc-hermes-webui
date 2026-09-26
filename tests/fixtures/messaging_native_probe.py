"""Run with the installed ARC agent interpreter; all accounts and servers are synthetic."""

import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    root = Path(os.environ.get("ARC_WEBUI_PREVIEW_ROOT", Path(__file__).resolve().parents[2]))
    preview = "--preview" in sys.argv
    with tempfile.TemporaryDirectory(prefix="arc-messaging-native-") as temporary:
        home = Path(temporary)
        os.environ["HERMES_HOME"] = str(home)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(home / "webui")
        os.environ["HERMES_WEBUI_DEFAULT_WORKSPACE"] = str(home / "workspace")
        os.environ["HERMES_WEBUI_SERVER_CWD"] = str(home)
        os.environ["HERMES_WEBUI_AUTO_INSTALL"] = "0"
        os.environ["HERMES_WEBUI_PASSWORD"] = "synthetic-webui-password"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        sys.path.insert(0, str(root))
        credential = home / "registered.token"
        credential.write_text("a" * 64)
        credential.chmod(0o600)
        seen = []

        class Native(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                assert self.headers.get("Authorization") == "Bearer " + "a" * 64
                assert self.headers.get("Cookie") is None
                assert self.headers.get("Origin") is None
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                seen.append((self.path, body))
                status = 200
                if self.path == "/v1/status":
                    result = {"application": "arc-whatsapp", "apiVersion": 1, "phase": "ready",
                              "operations": ["groups.list", "messages.recent"], "eventHistory": "live-only"}
                elif self.path == "/v1/access":
                    result = {"source": "native-profile", "tokenId": "synthetic-registration", "revision": 1,
                              "principal": {"id": "synthetic-reader", "enabled": True, "administrator": False,
                                            "operations": ["groups.list", "messages.recent"] +
                                            (["profile.get", "contacts.list", "messages.send", "messages.react",
                                              "messages.details"] if preview else []),
                                            "rooms": ["synthetic@g.us"]}}
                elif self.path == "/v1/operations/profile.get" and preview:
                    result = {"id": "synthetic@c.us", "name": "Demo account"}
                elif self.path == "/v1/operations/groups.list":
                    if not preview:
                        assert body == {"limit": 50}
                    result = {"items": [{"id": "synthetic@g.us", "name": "Synthetic group"}],
                              "total": 1, "nextOffset": None, "coverage": "authorized-native-loaded-directory"}
                elif self.path == "/v1/operations/contacts.list" and preview:
                    result = {"items": [], "total": 0, "nextOffset": None,
                              "coverage": "authorized-native-loaded-directory"}
                elif self.path == "/v1/operations/messages.recent":
                    assert body["roomId"] == "synthetic@g.us"
                    result = {"items": [{"messageId": "false_synthetic@g.us_MSG", "roomId": "synthetic@g.us",
                                          "text": "Synthetic message", "senderId": "synthetic@c.us",
                                          "timestamp": 1790000000, "fromMe": False}],
                              "requested": 50, "coverage": "authorized-native-available-history",
                              "observedAt": "2026-09-26T12:00:00Z"}
                elif preview and self.path == "/v1/operations/messages.send":
                    assert body.get("body") and body.get("roomId") == "synthetic@g.us"
                    result = {"outcome": "submitted", "messageId": "true_synthetic@g.us_SEND",
                              "roomId": "synthetic@g.us", "ack": 0}
                elif preview and self.path == "/v1/operations/messages.react":
                    result = {"outcome": "submitted", "messageId": body["messageId"],
                              "emoji": body["emoji"], "verified": False}
                elif preview and self.path == "/v1/operations/messages.details":
                    result = {"message": {"reactions": {"status": "available", "items": []}},
                              "delivery": {"status": "unavailable", "info": None}}
                else:
                    status = 403
                    result = None
                payload = {"ok": True, "result": result} if status == 200 else {
                    "ok": False, "error": {"code": "permission_denied", "message": "Synthetic denial"}}
                encoded = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("X-ARC-WhatsApp-API", "1")
                self.end_headers()
                self.wfile.write(encoded)

        native = ThreadingHTTPServer(("127.0.0.1", 0), Native)
        native_thread = threading.Thread(target=native.serve_forever)
        native_thread.start()
        (home / "config.yaml").write_text(
            "database:\n  journal_mode: delete\nmodel:\n  default: synthetic\narc_whatsapp:\n  enabled: true\n"
            f"  base_url: http://127.0.0.1:{native.server_port}\n  token_file: {credential}\n"
        )
        try:
            from hermes_state import SessionDB
            db = SessionDB(home / "state.db")
            try:
                db.create_session("synthetic-slack-session", "slack", profile_name="default", model="synthetic")
                db.set_session_title("synthetic-slack-session", "Team check-in")
                db.append_message("synthetic-slack-session", "user", "Synthetic project question")
                db.append_message("synthetic-slack-session", "assistant", "Synthetic project answer")
            finally:
                db.close()
            from server import Handler
            webui = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            webui.daemon_threads = True
            thread = threading.Thread(target=webui.serve_forever)
            thread.start()
            try:
                if preview:
                    print(f"PREVIEW http://127.0.0.1:{webui.server_port} PID {os.getpid()} (synthetic accounts only)", flush=True)
                    threading.Event().wait()
                    return
                cookie, csrf = "", ""

                def call(path, body=None, authenticated=True, with_csrf=True):
                    conn = http.client.HTTPConnection("127.0.0.1", webui.server_port, timeout=30)
                    headers = {"Content-Type": "application/json"}
                    if authenticated and cookie:
                        headers["Cookie"] = cookie
                    if with_csrf and csrf:
                        headers["X-Hermes-CSRF-Token"] = csrf
                    if body is not None:
                        headers["Origin"] = f"http://127.0.0.1:{webui.server_port}"
                    conn.request("POST" if body is not None else "GET", path,
                                 json.dumps(body) if body is not None else None, headers)
                    response = conn.getresponse()
                    data, result_headers, status = response.read(), dict(response.getheaders()), response.status
                    conn.close()
                    return status, data, result_headers

                status, _, _ = call("/api/messaging/connections?profile=default", authenticated=False)
                assert status == 401
                status, _, headers = call("/api/auth/login", {"password": "synthetic-webui-password"}, authenticated=False)
                assert status == 200
                cookie = headers["Set-Cookie"].split(";")[0]
                status, page, _ = call("/")
                assert status == 200
                csrf = json.loads(re.search(rb'csrfToken:("(?:[^"\\]|\\.)*")', page).group(1))
                status, data, _ = call("/api/messaging/connections?profile=default")
                assert status == 200, data
                connections = json.loads(data)
                assert connections["whatsapp"]["access"]["principal"]["id"] == "synthetic-reader", data
                assert ("a" * 64).encode() not in data
                assert b"redacted_value" not in data
                status, data, _ = call("/api/messaging/conversations?profile=default&platform=slack")
                assert status == 200, data
                conversations = json.loads(data)["items"]
                assert len(conversations) == 1 and conversations[0]["title"] == "Team check-in", data
                assert conversations[0]["source_label"] == "Slack", data
                status, _, _ = call("/api/arc/whatsapp/operations/groups.list?profile=default", {"limit": 50}, with_csrf=False)
                assert status == 403
                status, data, _ = call("/api/arc/whatsapp/operations/groups.list?profile=default", {"limit": 50})
                assert status == 200 and json.loads(data)["items"][0]["name"] == "Synthetic group", data
                status, data, _ = call("/api/arc/whatsapp/operations/messages.recent?profile=default",
                                       {"roomId": "synthetic@g.us", "limit": 50})
                assert status == 200 and json.loads(data)["items"][0]["text"] == "Synthetic message", data
                status, data, _ = call("/api/arc/whatsapp/operations/messages.send?profile=default",
                                       {"roomId": "synthetic@g.us", "body": "Synthetic send denied"})
                assert status == 403 and json.loads(data)["detail"]["code"] == "permission_denied", data
                assert len([entry for entry in seen if entry[0].endswith("messages.send")]) == 1
                status, _, _ = call("/api/arc/whatsapp/connection?profile=another-profile")
                assert status == 409
                print("PASS: real agent binding, WebUI auth/CSRF, native credentials, directory/history, denial, no retries, profile isolation")
            finally:
                webui.shutdown()
                webui.server_close()
                thread.join()
        finally:
            native.shutdown()
            native.server_close()
            native_thread.join()


if __name__ == "__main__":
    main()
