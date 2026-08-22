#!/usr/bin/env bash
# UC10 (round 2) - Enterprise SSO surface: does the generic-OIDC env trio actually wire a
# working OIDC handshake, or just paint a button? A stub IdP serves real OIDC discovery; the
# engine must fetch it and come back with the stub's own authorization URL.
#
# Round-1 finding this probes: [GAP] "no SAML/SCIM (OAuth is Google/GitHub only)" (UC7).
# The honest scope shipped: generic OIDC (covers Okta/Entra/Auth0/Workspace/Keycloak);
# SAML and SCIM remain unsupported and are documented as such, not implied.
#
#   AGENTX_ENGINE_DIR=.../AgentX-trace-eval/engine bash uc10_sso_surface.sh
set -uo pipefail
: "${AGENTX_ENGINE_DIR:?set AGENTX_ENGINE_DIR to the engine checkout}"

IDP_PORT=4794
ENGINE_PORT=4793
TMPHOME=$(mktemp -d)

# A minimal OIDC issuer: discovery only - exactly what the engine needs for the redirect leg.
python3 - "$IDP_PORT" <<'PY' > "$TMPHOME/idp.log" 2>&1 &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/.well-known/openid-configuration"):
            body = json.dumps({
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "userinfo_endpoint": f"{base}/userinfo",
                "jwks_uri": f"{base}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
IDP_PID=$!
sleep 1

(cd "$AGENTX_ENGINE_DIR" && PORT=$ENGINE_PORT AGENTX_HOME="$TMPHOME" AGENTX_AUTH=enabled \
  AGENTX_OIDC_ISSUER="http://127.0.0.1:$IDP_PORT" \
  AGENTX_OIDC_CLIENT_ID=uc10-client AGENTX_OIDC_CLIENT_SECRET=uc10-secret \
  AGENTX_OIDC_NAME="Okta" \
  npx tsx src/index.ts > "$TMPHOME/engine.log" 2>&1 &)
for i in $(seq 1 40); do curl -s -o /dev/null "http://localhost:$ENGINE_PORT/api/v1/auth/config" && break; sleep 1; done

echo "=== 10.1 the sign-in screen learns about SSO from /auth/config ==="
CFG=$(curl -s "http://localhost:$ENGINE_PORT/api/v1/auth/config")
echo "auth config: $CFG"
echo "$CFG" | grep -q '"oidc"' && echo "oidc advertised: True (expect True)" || echo "oidc advertised: False (expect True)"
echo "$CFG" | grep -q '"ssoLabel":"Okta"' && echo "button label from AGENTX_OIDC_NAME: True" || echo "button label: MISSING"

echo
echo "=== 10.2 the handshake is real: discovery fetched, authorize URL returned ==="
# callbackURL is the engine's own origin - exactly what the dashboard sends (it is served from
# the engine). A foreign origin is properly rejected as INVALID_CALLBACK_URL unless listed in
# AGENTX_TRUSTED_ORIGINS, which is its own [GOOD] finding: no open-redirect via the SSO leg.
SIGNIN=$(curl -s -X POST "http://localhost:$ENGINE_PORT/api/v1/auth/sign-in/oauth2" \
  -H 'content-type: application/json' \
  -d "{\"providerId\":\"oidc\",\"callbackURL\":\"http://localhost:$ENGINE_PORT/\"}")
echo "$SIGNIN" | grep -q "127.0.0.1:$IDP_PORT/authorize" \
  && echo "authorization URL points at the IdP: True (expect True)" \
  || { echo "authorization URL missing - response: $SIGNIN"; }
echo "$SIGNIN" | grep -q "client_id=uc10-client" && echo "client_id forwarded: True (expect True)"

echo
echo "=== 10.3 without the env trio, nothing is advertised (no placebo buttons) ==="
TMPHOME2=$(mktemp -d)
(cd "$AGENTX_ENGINE_DIR" && PORT=4795 AGENTX_HOME="$TMPHOME2" AGENTX_AUTH=enabled \
  npx tsx src/index.ts > "$TMPHOME2/engine.log" 2>&1 &)
for i in $(seq 1 40); do curl -s -o /dev/null "http://localhost:4795/api/v1/auth/config" && break; sleep 1; done
BARE=$(curl -s "http://localhost:4795/api/v1/auth/config")
echo "$BARE" | grep -q '"oidc"' && echo "oidc advertised without config: True (BAD)" || echo "oidc advertised without config: False (expect False)"

# Cleanup
kill $IDP_PID 2>/dev/null
lsof -ti :$ENGINE_PORT | xargs kill 2>/dev/null
lsof -ti :4795 | xargs kill 2>/dev/null
echo
echo "UC10 complete - interpret results against FINDINGS.md round 2"
