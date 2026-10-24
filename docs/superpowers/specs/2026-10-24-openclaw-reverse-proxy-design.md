# OpenClaw Reverse Proxy — Design

Date: 2026-10-24
Status: Draft for review

## 1. Goal

Users open the OpenClaw Control UI in a browser without running
`openclaw devices approve` on the gateway host. The role installs a reverse
proxy in front of the gateway that authenticates users and passes their
identity to OpenClaw (`gateway.auth.mode: trusted-proxy`), and OpenClaw
auto-approves new browser devices for those users (`deviceAutoApprove`).

Success criteria:

- After a role run with default proxy settings, a browser reaching
  `https://<hostname>` and logging in with a configured basic-auth user gets a
  working Control UI session with no manual approval.
- The gateway port is not reachable from other hosts; the proxy is the only
  remote path.
- Local CLI commands run as the service user (`openclaw gateway status`,
  `openclaw devices list`) keep working.
- A second run reports no changes (idempotence).
- `slm_ansible_role_openclaw_proxy_enabled: false` keeps today's behaviour
  (token auth, manual pairing).

Out of scope: firewall management, HSTS headers, an OIDC login flow in
Molecule (OIDC is tested at config level only), automatic cleanup of Caddy
when the proxy is switched off on a present install.

## 2. Decisions

| Topic | Decision |
|---|---|
| Proxy | Caddy from the official Caddy apt repository (`dl.cloudsmith.io/public/caddy/stable`), same host as the gateway |
| User authentication | `basic` (Caddy `basic_auth`, default) or `oidc` (oauth2-proxy sidecar via Caddy `forward_auth`), selected by `slm_ansible_role_openclaw_proxy_auth` |
| oauth2-proxy install | Pinned GitHub release tarball, sha256 checked against the release checksum file, systemd unit, own system user |
| TLS | `internal` (Caddy internal CA, default), `acme` (Let's Encrypt), `files` (own cert/key) via `slm_ansible_role_openclaw_proxy_tls` |
| Enabled by default | Yes (`slm_ansible_role_openclaw_proxy_enabled: true`) |
| Gateway auth with proxy | `trusted-proxy`, `allowLoopback: true`, `trustedProxies: ["127.0.0.1"]`, gateway stays on `bind: loopback` |
| Local CLI auth with proxy | `gateway.auth.password` from `OPENCLAW_GATEWAY_PASSWORD` in `.env`; the token is not written (OpenClaw refuses to start with a token in trusted-proxy mode) |
| Device scopes | `deviceAutoApprove.enabled: true` with OpenClaw's default scopes (read, write, approvals, questions); `operator.admin` only per user via `gateway.auth.identityScopes` (session-only) |
| Basic-auth passwords | Supplied as bcrypt hashes (`caddy hash-password`), not plaintext, so runs stay idempotent and the controller needs no `passlib` |
| Caddyfile ownership | The role owns `/etc/caddy/Caddyfile` entirely |
| Uninstall | oauth2-proxy always removed; Caddy only with `slm_ansible_role_openclaw_uninstall_caddy: true` (default false, like Node.js) |

## 3. Variables

Added to `defaults/main.yml`:

```yaml
# Reverse proxy in front of the gateway (trusted-proxy auth, no manual device approval)
slm_ansible_role_openclaw_proxy_enabled: true
# basic | oidc
slm_ansible_role_openclaw_proxy_auth: basic
slm_ansible_role_openclaw_proxy_hostname: "{{ ansible_facts['default_ipv4']['address'] }}"
slm_ansible_role_openclaw_proxy_https_port: 443
# internal | acme | files
slm_ansible_role_openclaw_proxy_tls: internal
# tls files: paths on the controller
slm_ansible_role_openclaw_proxy_tls_cert_file: ""
slm_ansible_role_openclaw_proxy_tls_key_file: ""
# tls acme: account email
slm_ansible_role_openclaw_proxy_acme_email: ""
# Identities granted operator.admin per session (basic user names or OIDC emails)
slm_ansible_role_openclaw_proxy_admin_users: []

# auth basic: [{name: alice, password_hash: "$2a$14$..."}], hashes from `caddy hash-password`
slm_ansible_role_openclaw_proxy_basic_users: []

# auth oidc (keep secrets in Ansible Vault)
slm_ansible_role_openclaw_proxy_oidc_issuer_url: ""
slm_ansible_role_openclaw_proxy_oidc_client_id: ""
slm_ansible_role_openclaw_proxy_oidc_client_secret: ""
# 16, 24 or 32 bytes
slm_ansible_role_openclaw_proxy_oidc_cookie_secret: ""
slm_ansible_role_openclaw_proxy_oidc_allowed_emails: []
# Extra oauth2-proxy settings merged into its config file
slm_ansible_role_openclaw_proxy_oidc_extra_config: {}
slm_ansible_role_openclaw_oauth2_proxy_version: "<latest release at implementation time>"

# Password for local CLI access when the proxy is enabled; keep it in Ansible Vault
slm_ansible_role_openclaw_gateway_password: ""

# Uninstall: also remove the caddy package and its repository
slm_ansible_role_openclaw_uninstall_caddy: false
```

`slm_ansible_role_openclaw_gateway_token` stays and is only used when the
proxy is disabled.

## 4. Gateway configuration

`vars/main.yml` selects the base config by `proxy_enabled`. Proxy disabled:
unchanged (token auth). Proxy enabled:

```yaml
gateway:
  mode: local
  bind: loopback
  port: <gateway_port>
  publicOrigin: "https://<proxy_hostname>[:<proxy_https_port> unless 443]"
  trustedProxies: ["127.0.0.1"]
  auth:
    mode: trusted-proxy
    password: "${OPENCLAW_GATEWAY_PASSWORD}"
    identityScopes: {<each admin user>: [operator.admin]}
    trustedProxy:
      userHeader: x-forwarded-user
      requiredHeaders: [x-forwarded-proto, x-forwarded-host]
      allowUsers: <basic user names | oidc allowed emails>
      allowLoopback: true
      deviceAutoApprove:
        enabled: true
```

`controlUi.allowedOrigins` is omitted so OpenClaw uses `publicOrigin`.
`slm_ansible_role_openclaw_config` still merges recursively over the base
config. The `.env` file holds `OPENCLAW_GATEWAY_PASSWORD` instead of
`OPENCLAW_GATEWAY_TOKEN` when the proxy is enabled; `slm_ansible_role_openclaw_env`
is merged as before.

## 5. Components

### 5.1 Task layout

- `tasks/install.yml` includes `install/proxy.yml` after `install/service.yml`
  when `proxy_enabled`.
- `tasks/install/proxy.yml` includes `proxy/caddy.yml`, and
  `proxy/oauth2_proxy.yml` when `proxy_auth == 'oidc'` (oauth2-proxy before
  Caddy so the forward-auth target exists when Caddy reloads).
- `tasks/uninstall.yml` gains proxy removal (section 5.5).

### 5.2 Caddy (`proxy/caddy.yml`)

1. Add the Caddy apt repository with its signing key (`deb822_repository`,
   like NodeSource) and install `caddy`.
2. `tls: files`: copy cert (`0644`) and key (`0640`, group `caddy`) to
   `/etc/caddy/certs/`.
3. Render `/etc/caddy/Caddyfile` from `templates/Caddyfile.j2`, validated with
   `caddy validate --adapter caddyfile --config %s`; notify `Reload caddy`.
4. Enable and start `caddy`.

Caddyfile, common part:

```caddy
{
    # acme only
    email <acme_email>
}

https://<hostname>:<port> {
    tls internal                                   # internal
    tls /etc/caddy/certs/openclaw.crt /etc/caddy/certs/openclaw.key   # files
    # acme: no tls directive (automatic HTTPS)
    ...auth block...
}
```

Basic auth block:

```caddy
basic_auth {
    alice $2a$14$...
}
reverse_proxy 127.0.0.1:<gateway_port> {
    header_up X-Forwarded-User {http.auth.user.id}
}
```

OIDC block:

```caddy
handle /oauth2/* {
    reverse_proxy 127.0.0.1:4180
}
handle {
    forward_auth 127.0.0.1:4180 {
        uri /oauth2/auth
        copy_headers X-Auth-Request-Email>X-Forwarded-User
        @unauthenticated status 401
        handle_response @unauthenticated {
            redir * /oauth2/sign_in?rd={scheme}://{host}{uri}
        }
    }
    reverse_proxy 127.0.0.1:<gateway_port>
}
```

`header_up` and `copy_headers` overwrite any client-supplied
`X-Forwarded-User`. Caddy trusts no upstream proxies by default, so it
rebuilds `X-Forwarded-For/-Proto/-Host` from the real connection. WebSocket
upgrades need no extra directives.

### 5.3 oauth2-proxy (`proxy/oauth2_proxy.yml`)

1. Create system user and group `oauth2-proxy` (no home, `nologin`).
2. Download `oauth2-proxy-v<version>.linux-<arch>.tar.gz` and its sha256 file
   from the GitHub release, verify, unpack, install
   `/usr/local/bin/oauth2-proxy` (`0755`). Skipped when the installed binary
   already reports the pinned version.
3. Render `/etc/oauth2-proxy/oauth2-proxy.cfg` (`0600`, owner
   `oauth2-proxy`, `no_log`) and `/etc/oauth2-proxy/emails` (allowed emails).
   Settings: `http_address = "127.0.0.1:4180"`, `provider = "oidc"`,
   `oidc_issuer_url`, `client_id`, `client_secret`, `cookie_secret`,
   `cookie_secure = true`, `redirect_url = "https://<hostname>[:<port>]/oauth2/callback"`,
   `authenticated_emails_file`, `reverse_proxy = true`, `set_xauthrequest = true`,
   then `slm_ansible_role_openclaw_proxy_oidc_extra_config` merged over them.
4. Render `/etc/systemd/system/oauth2-proxy.service`, enable and start;
   config or binary changes notify `Restart oauth2-proxy`.

### 5.4 Handlers

`Reload caddy` (systemd reload) and `Restart oauth2-proxy` (restarted,
`daemon_reload: true`) are added to `handlers/main.yml`.

### 5.5 Uninstall

`state: absent`:

- Stop and disable `oauth2-proxy` if its unit exists; remove unit, binary,
  `/etc/oauth2-proxy`, and the `oauth2-proxy` user and group.
- Remove `/etc/caddy/certs/openclaw.*`.
- With `slm_ansible_role_openclaw_uninstall_caddy: true`: purge `caddy`,
  remove `/etc/caddy` and the Caddy repository (stat first, like NodeSource).
  Otherwise Caddy and its Caddyfile are left untouched.

## 6. Validation (`tasks/validate.yml`)

When `state == 'present'`:

- `proxy_enabled: false` → `gateway_token` required (as today).
- `proxy_enabled: true` →
  - `gateway_password` required; same single-quote/newline rule as other
    `.env` values.
  - `proxy_auth` in `[basic, oidc]`, `proxy_tls` in `[internal, acme, files]`.
  - `proxy_https_port` is an integer 1–65535 and not the gateway port.
  - `basic`: at least one user; every entry has `name` (no whitespace or `:`)
    and a `password_hash` starting with `$2`.
  - `oidc`: issuer URL (`https://`), client ID, client secret, cookie secret
    of 16, 24 or 32 bytes, and at least one allowed email.
  - `acme`: hostname is not an IP address and `acme_email` is set.
  - `files`: both cert and key paths set.
  - Every entry of `proxy_admin_users` is also an allowed user.

All checks fail before any host change. The README states the breaking
change: playbooks that set only `gateway_token` must add `gateway_password`
and basic users, or set `proxy_enabled: false`.

## 7. Testing

### 7.1 Shared Molecule input (`molecule/default/vars.yml`)

- Remove the `gateway.bind: lan` and `controlUi.allowedOrigins` overrides.
- Add a basic user `molecule` with a pre-generated bcrypt hash of a known
  test password, `proxy_admin_users: [molecule]`, and a gateway password.

### 7.2 `install-ubuntu2204`, `install-ubuntu2404` (basic, tls internal)

`default-install/verify.yml`:

- Rendered config asserts `auth.mode == 'trusted-proxy'`, `bind == 'loopback'`,
  `trustedProxies`, `allowUsers`, `publicOrigin`, `identityScopes`,
  `deviceAutoApprove.enabled`; no `auth.token`.
- `.env` holds `OPENCLAW_GATEWAY_PASSWORD`, no `OPENCLAW_GATEWAY_TOKEN`.
- Gateway port is closed from the control node (`wait_for: state: stopped`,
  delegated to localhost); replaces the reachability check added earlier.
- `caddy` and `openclaw-gateway` running and enabled.
- From the control node (`uri`, `validate_certs: false`):
  no credentials → 401; wrong password → 401; correct credentials → 200 on
  `/`; correct credentials → 200 on the Control UI runtime config endpoint
  (gated by gateway HTTP auth, so this proves OpenClaw accepted the proxy
  identity).
- A request with a forged `X-Forwarded-User` header and no credentials → 401.
- Auto-approval end to end: a WebSocket client connects through the proxy
  with a device identity; afterwards `openclaw devices list --json` (as the
  service user) shows that device as paired and no pending request. First
  choice for the client is the openclaw CLI (`gateway probe --url wss://…`
  with basic credentials); if it cannot do this, a small Python client
  (`websockets` + Ed25519 device key) under `molecule/default-install/`.
- Kept: `openclaw gateway status` connectivity probe (now via password),
  invalid-config rejection, idempotence.

### 7.3 `uninstall-ubuntu2204`, `uninstall-ubuntu2404` (basic, tls files)

- Prepare generates a self-signed cert and key with `community.crypto` on the
  controller and installs with `proxy_tls: files`.
- Verify after `absent`: role-owned proxy files are gone; Caddy is still
  installed (`uninstall_caddy` defaults to false).

### 7.4 `install-oidc-ubuntu2404` (oidc, config level)

New scenario sharing `default/create.yml`/`destroy.yml`:

- Fake issuer settings plus `proxy_oidc_extra_config` with
  `skip_oidc_discovery: true` and explicit `login_url`, `redeem_url`,
  `oidc_jwks_url`, so oauth2-proxy starts without a reachable IdP.
- Verify: `oauth2-proxy` and `caddy` running; config file `0600`; an
  unauthenticated request to `/` redirects to `/oauth2/sign_in`; a forged
  `X-Forwarded-User` header without a session does not reach the gateway;
  idempotence.

### 7.5 Fast tests

`tests/test_validation.yml` gains one failing case per new validation rule
and one passing case each for basic and oidc.

## 8. Documentation

README: new "Reverse proxy" section (modes, TLS options, generating hashes
with `caddy hash-password`, admin users, local CLI as the service user), the
breaking-change note, new variables in the variable table, and the new
scenario in the testing table.
