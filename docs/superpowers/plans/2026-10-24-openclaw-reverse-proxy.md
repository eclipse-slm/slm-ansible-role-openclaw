# OpenClaw Reverse Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The role installs Caddy (basic auth or OIDC via oauth2-proxy) in front of the gateway and switches OpenClaw to trusted-proxy auth with device auto-approval, so browsers use the Control UI without `openclaw devices approve`.

**Architecture:** Same-host Caddy terminates TLS on `proxy_https_port`, authenticates the user (`basic_auth` or `forward_auth` to oauth2-proxy on `127.0.0.1:4180`) and forwards to the gateway on `127.0.0.1:<gateway_port>` with `X-Forwarded-User`. OpenClaw trusts `127.0.0.1` (`allowLoopback`), auto-approves operator devices, and accepts a local password for CLI calls. Everything is switched by `slm_ansible_role_openclaw_proxy_enabled` (default `true`).

**Tech Stack:** Ansible (ansible.builtin, community.crypto for tests), Caddy 2 from the official apt repo, oauth2-proxy v7.15.5, Molecule via molecule_generic on KubeVirt.

**Spec:** `docs/superpowers/specs/2026-10-24-openclaw-reverse-proxy-design.md`

## Global Constraints

- Work on branch `feat/reverse-proxy`, not `main`.
- Supported OS: Ubuntu 22.04 and 24.04 only (existing `validate.yml` check).
- Variable prefix `slm_ansible_role_openclaw_`; internal vars and registers prefix `__slm_ansible_role_openclaw_`; molecule registers prefix `__verify_`.
- Caddy repo: `https://dl.cloudsmith.io/public/caddy/stable/deb/debian`, suite `any-version`, component `main`, key `https://dl.cloudsmith.io/public/caddy/stable/gpg.key`, via `ansible.builtin.deb822_repository` named `caddy-stable`.
- oauth2-proxy: version `7.15.5`, asset `oauth2-proxy-v7.15.5.linux-<amd64|arm64>.tar.gz` and checksum asset `<tarball>-sha256sum.txt` from `https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v7.15.5/`; listens on `127.0.0.1:4180`.
- Identity header `x-forwarded-user`; required headers `x-forwarded-proto`, `x-forwarded-host`; `trustedProxies: ["127.0.0.1"]`; `allowLoopback: true`; `deviceAutoApprove: {enabled: true}` without a `scopes` key.
- With the proxy enabled the `.env` file must not contain `OPENCLAW_GATEWAY_TOKEN` (OpenClaw refuses to start); it contains `OPENCLAW_GATEWAY_PASSWORD`.
- Secrets (`gateway_password`, OIDC client/cookie secrets, the oauth2-proxy config) are handled with `no_log: true`.
- Molecule test user: name `molecule`, password `molecule-Pa55`, hash `$2b$14$H81L2RzQQsxVL4VXLARLeekuBkRElmiX7tr7I.Tp7ukUKrftScMJ6`; gateway password `molecule-gw-Pa55`.
- Lint gate after every task: `yamllint . && ansible-lint` exits 0.
- Commit messages: conventional commits, end with the attribution line from the session.

## Review Focus

- `proxy_hostname` with a non-default `proxy_https_port` (e.g. 8443): `publicOrigin`, Caddy site address and oauth2-proxy `redirect_url` must all carry `:8443`; with 443 none may carry a port, or OpenClaw rejects the browser origin. Test in Task 2 (`tests/test_config.yml`, case "custom https port").
- Client sends its own `X-Forwarded-User: admin` header: must never reach the gateway as identity. Test in Task 3 (forged header without credentials → 401; Caddy's `header_up` overwrites it when credentials are valid) and Task 5 (forged header without session → redirect).
- Switching an existing token install to the proxy (upgrade path): the old `OPENCLAW_GATEWAY_TOKEN` line must disappear from `.env` and the gateway must restart cleanly. Covered by Molecule install scenarios: prepare installs with `proxy_enabled: false` and a token, converge enables the proxy (Task 3).
- Re-running with unchanged basic users and certs reports no changes (bcrypt hashes and copied certs are stable). Covered by the `idempotence` step of every scenario.
- `proxy_admin_users` entry that is not an allowed user: must fail validation, not silently grant nothing. Test in Task 1 (case "admin not allowed").

---

### Task 1: Proxy defaults and input validation

**Files:**
- Modify: `defaults/main.yml` (append the block from spec §3 with `slm_ansible_role_openclaw_oauth2_proxy_version: "7.15.5"`)
- Modify: `vars/main.yml` (add `__slm_ansible_role_openclaw_proxy_auths: [basic, oidc]`, `__slm_ansible_role_openclaw_proxy_tls_modes: [internal, acme, files]`, `__slm_ansible_role_openclaw_proxy_allowed_users` = basic user names or oidc emails by `proxy_auth`)
- Modify: `tasks/validate.yml`
- Modify: `tests/expect_failure.yml`, `tests/test_validation.yml`

**Interfaces:**
- Produces: all `slm_ansible_role_openclaw_proxy_*` defaults, `slm_ansible_role_openclaw_gateway_password`, `slm_ansible_role_openclaw_uninstall_caddy`; `__slm_ansible_role_openclaw_proxy_allowed_users` (list of strings) used by Tasks 2 and 5.

- [ ] **Step 1: Extend the test harness.** In `tests/expect_failure.yml` pass every case key through with a valid basic-proxy default: `slm_ansible_role_openclaw_proxy_enabled: "{{ item.proxy_enabled | default(true) }}"`, `..._proxy_auth: "{{ item.proxy_auth | default('basic') }}"`, `..._proxy_tls: "{{ item.proxy_tls | default('internal') }}"`, `..._proxy_hostname: "{{ item.hostname | default('192.0.2.10') }}"`, `..._proxy_https_port: "{{ item.https_port | default(443) }}"`, `..._proxy_basic_users: "{{ item.basic_users | default([{'name': 'alice', 'password_hash': '$2b$14$abc'}]) }}"`, `..._proxy_admin_users: "{{ item.admin_users | default([]) }}"`, `..._gateway_password: "{{ item.password | default('valid-password') }}"`, plus `item.oidc | default({})` keys mapped onto the five oidc vars and `acme_email`, `tls_cert_file`, `tls_key_file`.

- [ ] **Step 2: Write failing cases** in `tests/test_validation.yml` `__cases` (name → `expect` substring):

| name | input | expect |
|---|---|---|
| missing token without proxy | `proxy_enabled: false`, `token: ''` | `slm_ansible_role_openclaw_gateway_token must be set` |
| missing gateway password | `password: ''` | `slm_ansible_role_openclaw_gateway_password must be set` |
| quote in gateway password | `password: "a'b"` | `must not contain single quotes or newlines` |
| invalid proxy auth | `proxy_auth: ldap` | `slm_ansible_role_openclaw_proxy_auth must be one of` |
| invalid tls mode | `proxy_tls: selfsigned` | `slm_ansible_role_openclaw_proxy_tls must be one of` |
| port clash | `https_port: 18789` | `slm_ansible_role_openclaw_proxy_https_port must be` |
| no basic users | `basic_users: []` | `slm_ansible_role_openclaw_proxy_basic_users needs at least one` |
| colon in basic user | `basic_users: [{name: 'a:b', password_hash: '$2b$14$x'}]` | `slm_ansible_role_openclaw_proxy_basic_users entries` |
| plaintext basic password | `basic_users: [{name: a, password_hash: secret}]` | `slm_ansible_role_openclaw_proxy_basic_users entries` |
| oidc incomplete | `proxy_auth: oidc`, `oidc: {issuer_url: 'http://idp'}` | `slm_ansible_role_openclaw_proxy_oidc_` |
| oidc bad cookie secret | full oidc with `cookie_secret: short` | `slm_ansible_role_openclaw_proxy_oidc_cookie_secret` |
| acme with ip | `proxy_tls: acme`, `acme_email: a@b.c` | `needs a DNS name` |
| acme without email | `proxy_tls: acme`, `hostname: claw.example.com` | `slm_ansible_role_openclaw_proxy_acme_email` |
| files without paths | `proxy_tls: files` | `slm_ansible_role_openclaw_proxy_tls_cert_file` |
| admin not allowed | `admin_users: [bob]` | `slm_ansible_role_openclaw_proxy_admin_users` |

Change the existing "missing token" case to the "missing token without proxy" row. Add passing tasks "Accept valid basic proxy input", "Accept valid oidc proxy input" (issuer `https://idp.example.com/realms/slm`, client id `openclaw`, secret `s3cret`, cookie secret of 32 chars, `allowed_emails: [alice@example.com]`, `admin_users: [alice@example.com]`), and "Accept token without proxy".

- [ ] **Step 3: Run and see the new cases fail**

Run: `ansible-playbook -i tests/inventory tests/test_validation.yml`
Expected: FAIL at the first new case ("was not rejected as expected").

- [ ] **Step 4: Implement the rules from spec §6 in `tasks/validate.yml`** as separate `ansible.builtin.assert` tasks with `quiet: true`, each `when: slm_ansible_role_openclaw_state == 'present'` plus the proxy/auth/tls condition, with `fail_msg` starting with the variable name used in the table. Token check gets `and not proxy_enabled`. The env-value check covers `__slm_ansible_role_openclaw_env_all` (Task 2 changes its contents; keep the check reading that var). Cookie secret length: `length in [16, 24, 32]`. IP detection: `hostname is ansible.utils.ipaddr` is not available without a collection; use `hostname is match('^[0-9.]+$') or ':' in hostname`.

- [ ] **Step 5: Run tests**

Run: `ansible-playbook -i tests/inventory tests/test_validation.yml && yamllint . && ansible-lint`
Expected: all cases "rejected as expected", play recap `failed=0`, lint rc 0.

- [ ] **Step 6: Commit** `feat: add reverse proxy variables and validation`

### Task 2: Gateway config and env for proxy mode

**Files:**
- Modify: `vars/main.yml` (split base config)
- Create: `tests/test_config.yml`
- Modify: `molecule/default/vars.yml`

**Interfaces:**
- Consumes: Task 1 variables, `__slm_ansible_role_openclaw_proxy_allowed_users`.
- Produces: `__slm_ansible_role_openclaw_proxy_origin` (string, `https://<hostname>` or `https://<hostname>:<port>` when port ≠ 443) used by Tasks 3 and 5; `__slm_ansible_role_openclaw_effective_config` and `__slm_ansible_role_openclaw_env_all` now depend on `proxy_enabled`.

- [ ] **Step 1: Write `tests/test_config.yml`** (localhost play; `include_vars` the role's `defaults/main.yml` then `vars/main.yml`; set `ansible_facts.default_ipv4.address` not needed because each case sets `proxy_hostname`). Cases, each asserting on `__slm_ansible_role_openclaw_effective_config` (`__c.gateway` below) and `__slm_ansible_role_openclaw_env_all` (`__e`):
  - "proxy disabled": `__c.gateway.auth == {'mode': 'token', 'token': '${OPENCLAW_GATEWAY_TOKEN}'}`, `'publicOrigin' not in __c.gateway`, `__e.keys() | list == ['OPENCLAW_GATEWAY_TOKEN']`.
  - "proxy basic": `__c.gateway.bind == 'loopback'`, `__c.gateway.publicOrigin == 'https://192.0.2.10'`, `__c.gateway.trustedProxies == ['127.0.0.1']`, `__c.gateway.auth.mode == 'trusted-proxy'`, `__c.gateway.auth.password == '${OPENCLAW_GATEWAY_PASSWORD}'`, `'token' not in __c.gateway.auth`, `__c.gateway.auth.identityScopes == {'alice': ['operator.admin']}`, `__c.gateway.auth.trustedProxy == {'userHeader': 'x-forwarded-user', 'requiredHeaders': ['x-forwarded-proto', 'x-forwarded-host'], 'allowUsers': ['alice', 'bob'], 'allowLoopback': True, 'deviceAutoApprove': {'enabled': True}}`, `'OPENCLAW_GATEWAY_TOKEN' not in __e`, `__e.OPENCLAW_GATEWAY_PASSWORD == 'pw'`.
  - "proxy oidc": `allowUsers == ['alice@example.com']`.
  - "custom https port": port 8443 → `publicOrigin == 'https://192.0.2.10:8443'`.
  - "user override merges": `slm_ansible_role_openclaw_config: {gateway: {auth: {trustedProxy: {allowUsers: [carol]}}}}` → `allowUsers == ['carol']` and `allowLoopback` still true.
  - "no admin users": `identityScopes` key absent.

- [ ] **Step 2: Run, expect failure**

Run: `ansible-playbook -i tests/inventory tests/test_config.yml`
Expected: FAIL on "proxy basic" (`publicOrigin` undefined).

- [ ] **Step 3: Implement in `vars/main.yml`:** keep `__slm_ansible_role_openclaw_base_config` for the token case as `__slm_ansible_role_openclaw_token_config`; add `__slm_ansible_role_openclaw_proxy_config` per spec §4; `__slm_ansible_role_openclaw_base_config` picks one by `proxy_enabled`; `identityScopes` built from `proxy_admin_users` and omitted when empty; `__slm_ansible_role_openclaw_env_all` starts from `{'OPENCLAW_GATEWAY_PASSWORD': gateway_password}` or `{'OPENCLAW_GATEWAY_TOKEN': gateway_token}`. Port must stay an int in JSON (existing `| int`).

- [ ] **Step 4: Run fast tests**

Run: `ansible-playbook -i tests/inventory tests/test_config.yml && ansible-playbook -i tests/inventory tests/test_validation.yml`
Expected: both `failed=0`.

- [ ] **Step 5: Update `molecule/default/vars.yml`:** remove the `gateway.bind`/`controlUi` override and its comment; add `slm_ansible_role_openclaw_proxy_basic_users: [{name: molecule, password_hash: <hash from Global Constraints>}]`, `slm_ansible_role_openclaw_proxy_admin_users: [molecule]`, `slm_ansible_role_openclaw_gateway_password: molecule-gw-Pa55`. Keep the token var (used by install prepare, Task 3).

- [ ] **Step 6: Lint and commit** `feat: switch gateway to trusted-proxy auth when the proxy is enabled`

### Task 3: Caddy with basic auth, TLS internal/acme/files

**Files:**
- Create: `tasks/install/proxy.yml`, `tasks/install/proxy/caddy.yml`, `templates/Caddyfile.j2`
- Modify: `tasks/install.yml`, `handlers/main.yml`, `vars/main.yml` (Caddy repo constants, `__slm_ansible_role_openclaw_caddy_cert_dir: /etc/caddy/certs`)
- Modify: `molecule/default-install/prepare.yml`, `molecule/default-install/verify.yml`, `requirements.yml` (add `community.crypto` `>=2.0.0` for Task 6)

**Interfaces:**
- Consumes: `__slm_ansible_role_openclaw_proxy_origin`.
- Produces: handler `Reload caddy`; `templates/Caddyfile.j2` with a `{% if slm_ansible_role_openclaw_proxy_auth == 'oidc' %}` branch left for Task 5; cert files `/etc/caddy/certs/openclaw.crt` and `openclaw.key`.

- [ ] **Step 1: Make install prepare start from a token install** (upgrade path): in `molecule/default-install/prepare.yml` set `slm_ansible_role_openclaw_proxy_enabled: false` alongside the existing old-version pin.

- [ ] **Step 2: Write the failing verify assertions** in `molecule/default-install/verify.yml`:
  - Replace the rendered-config assertions with: `bind == 'loopback'`, `auth.mode == 'trusted-proxy'`, `'token' not in auth`, `trustedProxy.allowUsers == ['molecule']`, `publicOrigin == 'https://' ~ ansible_host`, `identityScopes == {'molecule': ['operator.admin']}`; drop the `allowedOrigins` assertion.
  - `.env` assertion: `"OPENCLAW_GATEWAY_PASSWORD='molecule-gw-Pa55'" in __lines` and no line starting with `OPENCLAW_GATEWAY_TOKEN=`.
  - Replace "Wait for gateway port from the control node" with "Assert gateway port is closed from the control node": `wait_for` `state: stopped`, `timeout: 10`, delegated to localhost, `become: false`.
  - Service facts: `caddy.service` running and enabled.
  - From localhost (`ansible.builtin.uri`, `validate_certs: false`, `become: false`, `delegate_to: localhost`, base `https://{{ ansible_host }}`): `/` without auth → `status_code: 401`; `/` with `url_username: molecule`, `url_password: wrong`, `force_basic_auth: true` → 401; `/` with correct credentials → 200; `/control-ui-config.json` with correct credentials → 200; `/` with header `X-Forwarded-User: molecule` and no credentials → 401.

- [ ] **Step 3: Run, expect failure**

Run: `molecule converge -s install-ubuntu2404 && molecule verify -s install-ubuntu2404`
Expected: verify FAILS on the caddy/https assertions (no Caddy yet).

- [ ] **Step 4: Implement `tasks/install/proxy/caddy.yml`:** `deb822_repository` `caddy-stable` (pattern of `tasks/install/nodejs.yml`), `apt: name: caddy`; when `proxy_tls == 'files'`: `file` `/etc/caddy/certs` (`0750`, `root:caddy`) and `copy` cert `0644 root:caddy`, key `0640 root:caddy` (key with `no_log: true`), both notify `Reload caddy`; `template` `Caddyfile.j2` → `/etc/caddy/Caddyfile` `0644 root:caddy`, `validate: caddy validate --adapter caddyfile --config %s`, notify `Reload caddy`; `systemd_service` caddy `enabled: true`, `state: started`. `tasks/install/proxy.yml` includes `proxy/caddy.yml` (Task 5 adds oauth2-proxy before it); `tasks/install.yml` includes `install/proxy.yml` after service tasks `when: slm_ansible_role_openclaw_proxy_enabled | bool`. Handler `Reload caddy`: `systemd_service` `state: reloaded`.

- [ ] **Step 5: Implement `templates/Caddyfile.j2`** per spec §5.2: `# {{ ansible_managed }}`-style header like the unit template; global `email` only for acme; site address `{{ __slm_ansible_role_openclaw_proxy_origin }}` (port 443 omitted); `tls internal` / `tls <cert> <key>` / none; basic block with one `<name> <password_hash>` line per user; `reverse_proxy 127.0.0.1:{{ gateway_port }}` with `header_up X-Forwarded-User {http.auth.user.id}`.

- [ ] **Step 6: Run the full scenario**

Run: `molecule test -s install-ubuntu2404`
Expected: all actions successful, including idempotence (`changed=0`).

- [ ] **Step 7: Run the other OS and lint**

Run: `molecule test -s install-ubuntu2204 && yamllint . && ansible-lint`
Expected: successful, lint rc 0.

- [ ] **Step 8: Commit** `feat: install caddy reverse proxy with basic auth`

### Task 4: End-to-end device auto-approval test

**Files:**
- Modify: `molecule/default-install/verify.yml`
- Create (only if Step 1 finds the CLI cannot do it): `molecule/default-install/files/pair_client.py`

**Interfaces:**
- Consumes: running proxy from Task 3.

- [ ] **Step 1: Decide the client.** On the converged VM, as the service user, run `openclaw gateway probe --help` and try `openclaw gateway probe --url 'wss://molecule:molecule-Pa55@<ansible_host>' --json` (with `NODE_TLS_REJECT_UNAUTHORIZED=0`). If it connects through Caddy and a device appears in `openclaw devices list --json` under paired, use the CLI; otherwise use the Python client. Record the choice in the commit message.

- [ ] **Step 2: Python client (only if needed):** `pair_client.py <url> <user> <password>` generates an Ed25519 key, opens a WebSocket with a basic `Authorization` header and `ssl` verification off, performs the OpenClaw connect handshake as a Control UI client (role `operator`, scopes `operator.read`), exits 0 when the server reports the connection as authorized, non-zero otherwise. The handshake fields come from the Control UI client in the openclaw package (`dist/` connect frame) or `docs/gateway/protocol*.md`. Runs on the controller with `python3 -I`; `websockets` and `cryptography` are installed into a venv under the molecule ephemeral directory by a `pip` task in verify (`virtualenv_command: python3 -m venv`).

- [ ] **Step 3: Write the verify tasks:** count paired devices before (`openclaw devices list --json` as service user, `become_user: openclaw`, `-H` equivalent via `environment: HOME=/var/lib/openclaw`), run the client, count again; assert paired count increased by one and pending list is empty.

- [ ] **Step 4: Run**

Run: `molecule converge -s install-ubuntu2404 && molecule verify -s install-ubuntu2404`
Expected: verify passes; then `molecule idempotence -s install-ubuntu2404` passes.

- [ ] **Step 5: Lint and commit** `test: verify browser devices are auto-approved through the proxy`

### Task 5: oauth2-proxy and OIDC scenario

**Files:**
- Create: `tasks/install/proxy/oauth2_proxy.yml`, `templates/oauth2-proxy.cfg.j2`, `templates/oauth2-proxy.service.j2`, `templates/oauth2-proxy-emails.j2`
- Modify: `tasks/install/proxy.yml`, `templates/Caddyfile.j2`, `handlers/main.yml`, `vars/main.yml`
- Create: `molecule/install-oidc-ubuntu2404/molecule.yml`, `molecule/default-install-oidc/converge.yml`, `molecule/default-install-oidc/verify.yml`

**Interfaces:**
- Consumes: `__slm_ansible_role_openclaw_proxy_origin`, `__slm_ansible_role_openclaw_proxy_allowed_users`, Caddyfile oidc branch.
- Produces: handler `Restart oauth2-proxy`; vars `__slm_ansible_role_openclaw_oauth2_proxy_bin: /usr/local/bin/oauth2-proxy`, `__slm_ansible_role_openclaw_oauth2_proxy_conf_dir: /etc/oauth2-proxy`, `__slm_ansible_role_openclaw_oauth2_proxy_unit_path: /etc/systemd/system/oauth2-proxy.service` (used by Task 6).

- [ ] **Step 1: Create the scenario.** `molecule.yml` copied from `molecule/install-ubuntu2404/molecule.yml` with platform name `openclaw-oidc-ubuntu2404`, scenario name `install-oidc-ubuntu2404`, prepare `../default/prepare.yml`, converge/verify `../default-install-oidc/`. Converge uses `../default/vars.yml` plus: `proxy_auth: oidc`, issuer `https://idp.invalid/realms/test`, client id `openclaw`, secret `molecule-client-secret`, cookie secret `0123456789abcdef0123456789abcdef`, `allowed_emails: [molecule@example.com]`, `admin_users: [molecule@example.com]`, `basic_users: []`, `oidc_extra_config: {skip_oidc_discovery: true, login_url: https://idp.invalid/auth, redeem_url: https://idp.invalid/token, oidc_jwks_url: https://idp.invalid/certs}`.

- [ ] **Step 2: Write `default-install-oidc/verify.yml`:** `oauth2-proxy.service` and `caddy.service` running and enabled; `/etc/oauth2-proxy/oauth2-proxy.cfg` mode `0600` owner `oauth2-proxy`; `oauth2-proxy --version` output contains `7.15.5`; from localhost `GET https://<ansible_host>/` (`follow_redirects: none`) → 302 with `location` containing `/oauth2/sign_in`; same with header `X-Forwarded-User: molecule@example.com` → 302; rendered `openclaw.json` `allowUsers == ['molecule@example.com']`.

- [ ] **Step 3: Run, expect failure**

Run: `molecule converge -s install-oidc-ubuntu2404`
Expected: validation passes but verify later fails (no oauth2-proxy); converge itself may fail on Caddyfile validation because the oidc branch is empty — either is the expected red state.

- [ ] **Step 4: Implement `proxy/oauth2_proxy.yml`** per spec §5.3: group/user `oauth2-proxy` (`system: true`, `create_home: false`, shell `/usr/sbin/nologin`); `command: <bin> --version` (`failed_when: false`, `changed_when: false`) to skip download when it already reports `7.15.5`; otherwise `get_url` the tarball into a `tempfile` dir with `checksum: "sha256:{{ url }}-sha256sum.txt"`, `unarchive` `remote_src: true`, `copy` `remote_src: true` the binary to `0755 root:root`, notify restart; arch map `{'x86_64': 'amd64', 'aarch64': 'arm64'}[ansible_facts.architecture]`. Config template renders `key = value` lines (strings quoted, booleans lower-case, lists as TOML arrays) from the spec §5.3 dict combined with `oidc_extra_config`; `0600 oauth2-proxy:oauth2-proxy`, `no_log: true`, notify restart. Unit: `User=oauth2-proxy`, `ExecStart=<bin> --config=/etc/oauth2-proxy/oauth2-proxy.cfg`, `Restart=always`, `After=network-online.target`. Enable and start. `tasks/install/proxy.yml` includes this file before `proxy/caddy.yml` `when: slm_ansible_role_openclaw_proxy_auth == 'oidc'`.

- [ ] **Step 5: Fill the Caddyfile oidc branch** with the block from spec §5.2 (`handle /oauth2/*`, `forward_auth` with `copy_headers X-Auth-Request-Email>X-Forwarded-User`, 401 → `redir * /oauth2/sign_in?rd={scheme}://{host}{uri}`).

- [ ] **Step 6: Run**

Run: `molecule test -s install-oidc-ubuntu2404 && molecule test -s install-ubuntu2404`
Expected: both successful including idempotence (basic scenario proves the shared template still works).

- [ ] **Step 7: Lint and commit** `feat: support OIDC login via oauth2-proxy`

### Task 6: Uninstall proxy components; uninstall scenarios with TLS files

**Files:**
- Modify: `tasks/uninstall.yml`
- Modify: `molecule/default-uninstall/prepare.yml`, `molecule/default-uninstall/verify.yml`

**Interfaces:**
- Consumes: Task 5 path vars, `__slm_ansible_role_openclaw_caddy_cert_dir`.

- [ ] **Step 1: Prepare with TLS files:** add a localhost play to `default-uninstall/prepare.yml` before the install play that creates `{{ lookup('env', 'MOLECULE_EPHEMERAL_DIRECTORY') }}/tls/openclaw.{key,crt}` with `community.crypto.openssl_privatekey` and `community.crypto.x509_certificate` (`provider: selfsigned`); the install play sets `proxy_tls: files` and the two paths.

- [ ] **Step 2: Write failing verify assertions** in `default-uninstall/verify.yml`: `/etc/oauth2-proxy`, `/usr/local/bin/oauth2-proxy`, `/etc/systemd/system/oauth2-proxy.service`, `/etc/caddy/certs/openclaw.crt`, `/etc/caddy/certs/openclaw.key` absent; user `oauth2-proxy` absent (`getent` with `fail_key: false`); package `caddy` still installed (`package_facts`).

- [ ] **Step 3: Run, expect failure**

Run: `molecule converge -s uninstall-ubuntu2404 && molecule verify -s uninstall-ubuntu2404`
Expected: FAIL on the cert file assertions.

- [ ] **Step 4: Implement in `tasks/uninstall.yml`** per spec §5.5, following the existing stat-then-act pattern: stop/disable oauth2-proxy when its unit exists, remove unit (notify `Reload systemd`), binary, conf dir, user and group; remove the two cert files; block `when: slm_ansible_role_openclaw_uninstall_caddy | bool`: `apt` caddy `state: absent`, `purge: true`, remove `/etc/caddy`, stat and remove repo `caddy-stable`.

- [ ] **Step 5: Run**

Run: `molecule test -s uninstall-ubuntu2404 && molecule test -s uninstall-ubuntu2204`
Expected: successful including idempotence.

- [ ] **Step 6: Lint and commit** `feat: remove proxy components on uninstall`

### Task 7: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the docs** per spec §8: variable table rows for every new variable (wording from the `defaults/main.yml` comments; mark `gateway_token` "Required when the proxy is disabled", `gateway_password` and `proxy_basic_users` "Required when the proxy is enabled"); a "Reverse proxy" section (how auth modes and TLS modes work, `caddy hash-password --plaintext '<pw>'`, admin users, opening `https://<hostname>`, local CLI as `sudo -u openclaw -H openclaw …`, Caddyfile owned by the role, `proxy_enabled: false` does not remove Caddy); a "Breaking change" note; testing table row for `install-oidc-ubuntu2404` and updated install/uninstall rows (proxy checks, TLS files).

- [ ] **Step 2: Check** every `slm_ansible_role_openclaw_*` key in `defaults/main.yml` appears in the README table:

Run: `for v in $(grep -oE '^slm_ansible_role_openclaw_[a-z0-9_]+' defaults/main.yml); do grep -q "\`$v\`" README.md || echo "missing $v"; done`
Expected: no output.

- [ ] **Step 3: Commit** `docs: document the reverse proxy`
