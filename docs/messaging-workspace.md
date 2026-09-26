# Connections, ARC Connectors, and Conversations

ARC WebUI provides three messaging surfaces in its desktop rail and mobile menu.
Android clients displaying this WebUI receive the same screens from the server.

## Connections

Connections lists Hermes messaging gateways. The standard Hermes WhatsApp
gateway is the first card shown. It can be enabled or disabled per profile
without changing the separate ARC WhatsApp account.
The other gateway cards include Slack, Telegram, Discord and Matrix.
Configuration readiness and connection state are separate: saving a token does
not establish a running connection. A gateway restart is required after a
configuration change, including a disable.

Configure uses the agent's channel-specific fields and validators. Inputs start
empty. An empty input preserves its stored value; removing one requires explicit
selection and confirmation. Credential values and redacted credential fragments
are omitted from listing responses. The existing agent service owns profile
configuration and credential-file writes. Saving does not restart the gateway;
operators apply connection changes through their deployment's lifecycle procedure.

## ARC Connectors

ARC WhatsApp uses the existing native application binding. Its card appears
in its own ARC Connectors view and shows account readiness and
registered-principal access. An operator can bind an existing native owner to
the selected Hermes profile by entering its local API port and the filesystem
path to a pre-issued private registered credential. The browser never sends
the credential content, and listing responses report only whether the path is
set. Enable/disable controls change only this Hermes profile binding. The
native account owner retains pairing/start/stop ownership: the current native
HTTP contract does not provide these lifecycle operations.
The Hermes WhatsApp gateway has its own linked-device state; disabling its
gateway does not stop ARC WhatsApp, and configuring ARC does not enable the
gateway. Do not link the same account to both owners without an explicit
account plan.

## Conversations

- **Agent conversations** lists messaging-origin Hermes sessions for the selected
  profile. Search and platform filters operate on profile-scoped metadata.
  Opening a row uses the existing agent-session view. It is not a complete
  platform inbox or an outbound message action.
- **ARC WhatsApp groups / contacts** queries the connected native application.
  Selecting a room reads available native history, independently of Hermes
  execution sessions. The current API provides bounded history rather than
  complete synchronization or durable event replay.
- Exact native room/message identities select reads, downloads, sends and
  reactions. Native permissions remain authoritative; UI hints do not authorize
  an operation.
- Sending text requires room confirmation. An empty emoji removes a reaction.
  Mutations are never automatically retried. Submitted results are shown as
  submitted; unknown transport outcomes remain uncertain so an operator can
  inspect history before deciding whether to retry.
- Visible conversation views refresh snapshots every fifteen seconds. Refresh
  pauses while hidden or while an edit/operation is pending. Navigation cancels
  reads and timers. Unsaved edits require a discard decision; a pending mutation
  must settle before an in-app panel/profile change.

The full scope remains in the
[ARC applications contract](https://github.com/martinezhermes/arc/blob/main/docs/architecture/applications.md).
This surface does not imply that native Slack application queries, complete
history synchronization, account administration or pairing APIs have shipped.

## Server prerequisites

The installed ARC Hermes checkout must provide the messaging and arc_whatsapp
modules under hermes_cli.web_routers and their dependencies.
HERMES_WEBUI_AGENT_DIR must identify that checkout. WebUI calls the existing
handlers inside its authenticated server; a separately running Hermes dashboard
is not required. Missing integration dependencies are reported as unavailable.

ARC WhatsApp uses the same profile configuration as the existing agent dashboard:

~~~yaml
arc_whatsapp:
  enabled: true
  base_url: http://127.0.0.1:9131
  token_file: credentials/registered-whatsapp.token
~~~

The native API/account owner must already be provisioned. The shared client
requires an explicitly ported loopback endpoint and an owned, private, non-symlink
registered credential file. Credentials stay on the backend, without a browser
bearer or owner-socket fallback. Container deployments need the native binding in
the server's network namespace. Provisioning, principal registration and
credential projection remain separate deployment/account operations.

New routes inherit WebUI authentication and POST CSRF enforcement:

| Route | Purpose |
| --- | --- |
| GET /api/messaging/connections | Hermes gateway configuration and status |
| GET /api/messaging/conversations | Paginated agent conversation metadata |
| POST /api/messaging/platforms/{id}/configure | Existing agent configuration |
| POST /api/messaging/platforms/{id}/test | Existing readiness/status check |
| GET /api/arc/connectors | ARC binding setup and native connection status |
| POST /api/arc/connectors/whatsapp/configure | Profile-local ARC binding setup/enable/disable |
| GET /api/arc/whatsapp/connection | Native account and registered-principal status |
| POST /api/arc/whatsapp/operations/{operation} | Allowlisted native operations |
| GET /api/arc/whatsapp/media | Authorized exact-message attachment |

Every request captures its profile query parameter. A mismatching profile is
rejected before backend access; writes require an explicit profile. Conversation
queries also accept platform, q, offset and limit; media requires message_id.
Responses are no-store and request bodies are bounded. Callers cannot choose a
backend URL or override a profile in a configuration body. Media retains the
shared client's authorization, safe headers and cleanup.

## Validation

Run feature and neighboring tests through the repository runner:

~~~bash
./scripts/test.sh tests/test_messaging_workspace.py tests/test_messaging_frontend.py \
  tests/test_gateway_status_agent_health.py tests/test_gateway_sync.py \
  tests/test_auth.py tests/test_issue1909_csrf_token.py \
  tests/test_profile_env_isolation.py tests/test_profile_path_security.py
~~~

For the cross-repository check, install the ARC agent's frozen dependencies in its
own .venv using its declared toolchain, then run:

~~~bash
ARC_WEBUI_TEST_AGENT=/path/to/arc-hermes-agent \
  ./scripts/test.sh tests/test_messaging_agent_integration.py
~~~

The test uses real agent handlers/client and real WebUI HTTP authentication, CSRF
and errors against an isolated synthetic native server. It does not pair an
account, contact a messaging provider or send a real message.

The same fixture accepts --preview with the agent interpreter for desktop and
narrow-viewport checks. Verify save/preserve/remove, empty/unavailable states,
directory/history discovery, send confirmation, permission denial, uncertainty,
navigation cleanup and narrow keyboard layouts using its synthetic accounts.
