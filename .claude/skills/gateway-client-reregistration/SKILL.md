---
name: gateway-client-reregistration
description: >
  Fix "Unknown client" (400) on the PingOne Privilege AI Gateway authorize
  endpoint when the gateway has dropped its dynamic OAuth client
  registration. Use when gateway login fails with Unknown client, invalid
  client, or an authorize-URL error for client_id that used to work.
---

# Re-registering the AI Gateway OAuth client after "Unknown client"

The deployed AI Gateway (`https://rluisi-agentless-mcpgw.ping-devops.com`,
path prefix `/todos-mcp-server`) creates OAuth clients through **dynamic
client registration (RFC 7591)** — `POST /todos-mcp-server/register`. Those
registrations are **not durable**: when the gateway itself restarts or is
redeployed, previously registered clients disappear server-side. The app's
config still holds the old `GATEWAY_CLIENT_ID`, so the browser authorize
redirect fails with `400 Unknown client`.

Confirmed 2026-08-30: client `GBhiLwRp0fWd-Mo95IfCRLkgJ5HU54UV` (registered
earlier the same week) returned `Unknown client` from the authorize endpoint
while the deployed backend still carried it. Re-registering + redeploying
fixed it within minutes. Expect this to recur after any gateway-side
restart.

This is the gateway forgetting, not the app being wrong — do not
"fix" the app code or rotate PingOne apps for this.

## Diagnosis (confirm before re-registering)

1. Check what client_id the deployed backend is currently configured with:

```bash
kubectl get configmap agentorchestration-backend-config \
  -n ping-devops-robertluisi -o jsonpath='{.data.GATEWAY_CLIENT_ID}{"\n"}'
```

2. Probe the authorize endpoint with that exact client_id (no auth needed
   to reproduce the error — URL-encode the redirect_uri):

```bash
curl -sk -o /dev/null -w '%{http_code}\n' \
  'https://rluisi-agentless-mcpgw.ping-devops.com/todos-mcp-server/authorize?response_type=code&client_id=<DEPLOYED_ID>&redirect_uri=https%3A%2F%2Frluisi-agent-orchestration-client.ping-devops.com%2Fapi%2Fauth%2Fgateway%2Fcallback&scope=openid+profile+email&state=probe&code_challenge=v0ZaQK2AWE1EDhGp2Ak9uQzS14zvjPHd6JAhXcTnC04&code_challenge_method=S256'
```

- **400 with `Unknown client`** → registration lost; follow the fix below.
- **302** → the client is fine; the login failure is something else
  (redirect mismatch, expired policy, gateway auth flow issue) — do NOT
  re-register; debug the actual failure instead.

The code_challenge value above is a throwaway probe constant, not a real
PKCE verifier for any session — the probe never reaches token exchange.

## Fix

### 1. Re-register with the gateway

Register with the callback URL(s) the app actually redirects to. For the
**deployed** console only:

```bash
curl -sk -X POST \
  'https://rluisi-agentless-mcpgw.ping-devops.com/todos-mcp-server/register' \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data '{
    "client_name": "Agent Orchestration Console",
    "client_uri": "https://rluisi-agent-orchestration-client.ping-devops.com",
    "redirect_uris": [
      "https://rluisi-agent-orchestration-client.ping-devops.com/api/auth/gateway/callback"
    ],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"]
  }'
```

For a setup that must work **both locally and deployed**, include both
redirect URIs (local first) and use the same client_id in `backend/.env`
and Helm values:

```json
    "redirect_uris": [
      "http://localhost:8000/api/auth/gateway/callback",
      "https://rluisi-agent-orchestration-client.ping-devops.com/api/auth/gateway/callback"
    ],
```

Save the returned `client_id`.

### 2. Update `GATEWAY_CLIENT_ID` in `helm/values.yaml`

Edit the backend's ConfigMap entry (the `GATEWAY_CLIENT_ID: "..."` line
under the backend service's env config) to the new client_id. The client_id
is a public identifier — it is fine to store here; never put any token or
secret in values files.

### 3. Redeploy and restart the backend

```bash
cd /Users/robertluisi/source/repos/agent-orchestration-demo
helm upgrade --install agentorchestration ./helm \
  --namespace ping-devops-robertluisi -f helm/values.yaml --wait --timeout 10m
kubectl rollout restart deployment/agentorchestration-backend \
  -n ping-devops-robertluisi
kubectl rollout status deployment/agentorchestration-backend \
  -n ping-devops-robertluisi --timeout=180s
```

The rollout restart is required — `envFrom` ConfigMap values only load at
pod start (hit during the v7 deploy too).

### 4. Verify

```bash
kubectl exec deployment/agentorchestration-backend \
  -n ping-devops-robertluisi -- printenv GATEWAY_CLIENT_ID
```

must print the new id, and the probe curl from the diagnosis section must
now return **302**, not 400. Then retry gateway login in the browser
(incognito avoids a cached failure page).

## Gotchas

- **Postman's client_id is never the app's client_id.** Postman runs its
  own dynamic registration per flow and registers its own redirect
  (`https://oauth.pstmn.io/v1/callback`). Copying a Postman-minted
  client_id into the app config guarantees a redirect_uri mismatch. Use
  only a client registered with the app's own callback URLs.
- **`-k` on curl** may be needed because the gateway presents a self-signed
  cert in this environment; prefer installing the CA cert over disabling
  verification in production.
- **Local-only workaround while deployed config is broken**: none — the
  client_id lives in the ConfigMap, so fixing it is always the helm +
  restart path above. There is no in-pod env override short of a rebuild.
- **No secret is involved**: this client uses `token_endpoint_auth_method:
  none` (public client, PKCE only). Re-registering creates no secret to
  rotate or re-distribute.
