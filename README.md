# slm-ansible-role-openclaw

Ansible role that installs, configures and removes the
[OpenClaw](https://docs.openclaw.ai/) gateway on Ubuntu servers. The gateway
runs unattended as a systemd system service under a dedicated non-root user;
no interactive onboarding is needed.

What the role does with `slm_ansible_role_openclaw_state: present`:

1. Installs Node.js from the NodeSource apt repository (major version 24 by default).
2. Creates the system user `openclaw` with home `/var/lib/openclaw`.
3. Installs the pinned npm package `openclaw` globally and upgrades it when the version changes.
4. Renders `openclaw.json` from Ansible variables, validated with
   `openclaw config validate` **before** it replaces the live file, and a
   `.env` file with the gateway password (or token) and other secrets.
5. Installs and starts `openclaw-gateway.service` and waits until the gateway port is open.
6. Installs Caddy as a reverse proxy in front of the gateway (on by default,
   see [Reverse proxy](#reverse-proxy)), so browsers can use the Control UI
   without `openclaw devices approve`.

With `slm_ansible_role_openclaw_state: absent` it stops and removes the
service, package and oauth2-proxy, and optionally the state directory, user,
Node.js, Caddy and their repositories.

> Ansible owns `openclaw.json`. Changes made at runtime via the CLI or the
> Control UI are overwritten on the next role run. Put every setting into
> `slm_ansible_role_openclaw_config`.

## Requirements

- Target: Ubuntu 22.04 or 24.04 with systemd.
- Controller: ansible-core 2.15+.
- Internet access from the target to `deb.nodesource.com`, the npm registry,
  `dl.cloudsmith.io` (Caddy) and, for OIDC, `github.com` (oauth2-proxy release).

## Role variables

| Variable | Default | Description |
|---|---|---|
| `slm_ansible_role_openclaw_state` | `present` | `present` installs/configures, `absent` removes. |
| `slm_ansible_role_openclaw_nodejs_major` | `24` | NodeSource major version. openclaw needs `>=24.16 <25` or `>=26.1`. |
| `slm_ansible_role_openclaw_nodejs_manage` | `true` | Set `false` if Node.js is installed by other means; the version is still checked. |
| `slm_ansible_role_openclaw_version` | `2026.10.4` | Exact npm version of `openclaw`. Only upgrade it: once a newer release has migrated the state database, an older release refuses to start (exit code 78). |
| `slm_ansible_role_openclaw_user` | `openclaw` | Service user. |
| `slm_ansible_role_openclaw_group` | `openclaw` | Service group. |
| `slm_ansible_role_openclaw_home` | `/var/lib/openclaw` | Home of the service user. |
| `slm_ansible_role_openclaw_state_dir` | `<home>/.openclaw` | OpenClaw state directory (`OPENCLAW_STATE_DIR`), mode 0700. |
| `slm_ansible_role_openclaw_config_path` | `<state_dir>/openclaw.json` | Config file (`OPENCLAW_CONFIG_PATH`), mode 0600. |
| `slm_ansible_role_openclaw_env_path` | `<state_dir>/.env` | Secret env file, mode 0600. |
| `slm_ansible_role_openclaw_gateway_port` | `18789` | Gateway WebSocket port. |
| `slm_ansible_role_openclaw_gateway_token` | `""` | Gateway auth token. **Required** for `present` when the proxy is disabled. Keep it in Ansible Vault. |
| `slm_ansible_role_openclaw_gateway_password` | `""` | Password for local CLI calls on the host. **Required** for `present` when the proxy is enabled. Keep it in Ansible Vault. |
| `slm_ansible_role_openclaw_openai_providers` | `[]` | OpenAI-compatible model endpoints, see [OpenAI-compatible endpoints](#openai-compatible-endpoints). |
| `slm_ansible_role_openclaw_default_model` | `""` | Default model as `<provider id>/<model id>`; empty leaves OpenClaw's choice. |
| `slm_ansible_role_openclaw_config` | `{}` | Free-form `openclaw.json` content, merged recursively over the base config (below). |
| `slm_ansible_role_openclaw_env` | `{}` | Extra variables for the `.env` file, e.g. provider API keys. |
| `slm_ansible_role_openclaw_service_name` | `openclaw-gateway` | systemd unit name. |
| `slm_ansible_role_openclaw_service_enabled` | `true` | Start the service at boot. |
| `slm_ansible_role_openclaw_log_level` | `info` | `OPENCLAW_LOG_LEVEL` for the service. |
| `slm_ansible_role_openclaw_uninstall_purge` | `false` | With `absent`: also remove state directory, user and group. |
| `slm_ansible_role_openclaw_uninstall_nodejs` | `false` | With `absent`: also remove Node.js and the NodeSource repository. |
| `slm_ansible_role_openclaw_uninstall_caddy` | `false` | With `absent`: also remove the `caddy` package, `/etc/caddy` and the Caddy repository. |
| `slm_ansible_role_openclaw_proxy_enabled` | `true` | Install the reverse proxy and switch the gateway to trusted-proxy auth. |
| `slm_ansible_role_openclaw_proxy_auth` | `basic` | How the proxy logs users in: `basic` (Caddy basic auth) or `oidc` (oauth2-proxy). |
| `slm_ansible_role_openclaw_proxy_hostname` | default IPv4 address | Host name or IP in the proxy URL `https://<hostname>`. |
| `slm_ansible_role_openclaw_proxy_https_port` | `443` | HTTPS port of the proxy. |
| `slm_ansible_role_openclaw_proxy_tls` | `internal` | `internal` (Caddy's own CA, browser warning), `acme` (Let's Encrypt, needs a public DNS name) or `files`. |
| `slm_ansible_role_openclaw_proxy_tls_cert_file` | `""` | With `tls: files`: certificate path on the controller. |
| `slm_ansible_role_openclaw_proxy_tls_key_file` | `""` | With `tls: files`: key path on the controller. |
| `slm_ansible_role_openclaw_proxy_acme_email` | `""` | With `tls: acme`: ACME account email. |
| `slm_ansible_role_openclaw_proxy_admin_users` | `[]` | Users that get `operator.admin` per session (basic user names or OIDC emails). Must be allowed users. |
| `slm_ansible_role_openclaw_proxy_basic_users` | `[]` | With `auth: basic`: `[{name, password_hash}]`, bcrypt hashes from `caddy hash-password`. **At least one required.** |
| `slm_ansible_role_openclaw_proxy_oidc_issuer_url` | `""` | With `auth: oidc`: issuer URL (`https://`). |
| `slm_ansible_role_openclaw_proxy_oidc_client_id` | `""` | With `auth: oidc`: client ID. |
| `slm_ansible_role_openclaw_proxy_oidc_client_secret` | `""` | With `auth: oidc`: client secret. Keep it in Ansible Vault. |
| `slm_ansible_role_openclaw_proxy_oidc_cookie_secret` | `""` | With `auth: oidc`: oauth2-proxy cookie secret, 16, 24 or 32 bytes. Keep it in Ansible Vault. |
| `slm_ansible_role_openclaw_proxy_oidc_allowed_emails` | `[]` | With `auth: oidc`: emails allowed to log in. **At least one required.** |
| `slm_ansible_role_openclaw_proxy_oidc_extra_config` | `{}` | Extra oauth2-proxy settings merged into its config file. |
| `slm_ansible_role_openclaw_oauth2_proxy_version` | `7.15.5` | oauth2-proxy release; the download is checked against the release's sha256 file. |

Base config that `slm_ansible_role_openclaw_config` is merged over when the
proxy is disabled (with the proxy, see [Reverse proxy](#reverse-proxy)):

```json
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "port": 18789,
    "auth": { "mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}" }
  },
  "agents": { "defaults": { "workspace": "/var/lib/openclaw/.openclaw/workspace" } }
}
```

OpenClaw validates its config strictly: an unknown key makes the role fail at
the template task and leaves the running gateway and its config untouched.
Use `openclaw config schema` on a host to look up valid keys.

### Secrets

Secrets belong in the `.env` file only. Reference them from
`slm_ansible_role_openclaw_config` with `${VAR}`; OpenClaw substitutes them at
runtime, so they never appear in `openclaw.json` or the Ansible output.

Values are written single-quoted and taken literally, so characters such as
`"`, `$`, `#` and spaces are fine. Values must not contain a single quote or a
newline; the role rejects them before changing anything.

## OpenAI-compatible endpoints

Self-hosted or proxied models (vLLM, LiteLLM, Ollama, LM Studio and other
servers with an OpenAI-style `/v1` API) are added as OpenClaw custom providers:

```yaml
slm_ansible_role_openclaw_openai_providers:
  - id: vllm                          # model refs become "vllm/<model id>"
    base_url: http://10.0.0.5:8000/v1
    api_key: "{{ vault_vllm_api_key }}"  # optional
    api: openai-completions           # optional; openai-responses if the server has /v1/responses
    models:                           # optional, see below
      - id: qwen3-32b                 # required
        name: Qwen3 32B               # optional, defaults to the id
        context_window: 131072        # optional
        max_tokens: 8192              # optional
        input: [text]                 # optional; [text, image] for vision models
  - id: litellm                       # no models: looked up from the endpoint
    base_url: https://litellm.internal/v1
    api_key: "{{ vault_litellm_api_key }}"
    validate_certs: false             # optional, only for the lookup (self-signed endpoints)
slm_ansible_role_openclaw_default_model: vllm/qwen3-32b
```

Without `models` (or with an empty list) the role calls `GET <base_url>/models`
on the target host, with `Authorization: Bearer <api_key>` when a key is set,
and adds every returned `data[].id` as a model. The standard response has no
names, context sizes or input types, so list models by hand where those
matter. If the lookup fails or returns no models, the role stops before
`openclaw.json` is changed and names the provider and URL. A
`default_model` on such a provider must be one of the returned models.

Each provider becomes `models.providers.<id>` in `openclaw.json`. The API key
is written to `.env` as `OPENCLAW_PROVIDER_<ID>_API_KEY` (upper case, `-` →
`_`) and referenced as `${...}`; providers without `api_key` get the
placeholder `no-key`. Other provider fields (headers, TLS, `compat`, ...) can
be added through `slm_ansible_role_openclaw_config`, which is merged over
these entries. Provider ids must match `^[a-z0-9][a-z0-9-]*$`.

## Reverse proxy

With `slm_ansible_role_openclaw_proxy_enabled: true` (the default) the role
installs Caddy on the same host. Caddy terminates TLS, logs the user in and
passes the user name to the gateway in `X-Forwarded-User`. The gateway uses
OpenClaw's trusted-proxy auth and approves new browser devices automatically
(`deviceAutoApprove`), so no `openclaw devices approve` is needed. The gateway
itself stays on loopback; `https://<hostname>` is the only way in.

Rendered gateway config with the proxy (basic auth, user `alice` as admin):

```json
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "port": 18789,
    "publicOrigin": "https://192.0.2.10",
    "trustedProxies": ["127.0.0.1"],
    "auth": {
      "mode": "trusted-proxy",
      "password": "${OPENCLAW_GATEWAY_PASSWORD}",
      "identityScopes": { "alice": ["operator.admin"] },
      "trustedProxy": {
        "userHeader": "x-forwarded-user",
        "requiredHeaders": ["x-forwarded-proto", "x-forwarded-host"],
        "allowUsers": ["alice"],
        "allowLoopback": true,
        "deviceAutoApprove": { "enabled": true }
      }
    }
  }
}
```

- **Login.** `basic`: users and bcrypt hashes in
  `slm_ansible_role_openclaw_proxy_basic_users`; create a hash with
  `caddy hash-password --plaintext '<password>'`. `oidc`: oauth2-proxy runs on
  `127.0.0.1:4180` and Caddy asks it for every request; register
  `https://<hostname>/oauth2/callback` as redirect URI at the IdP.
- **Permissions.** Auto-approved devices get OpenClaw's default operator scopes
  (read, write, approvals, questions). Users in
  `slm_ansible_role_openclaw_proxy_admin_users` get `operator.admin` for their
  sessions only. Caddy sends `X-Openclaw-Scopes` on every request (overwriting
  any value from the client), because OpenClaw otherwise grants trusted-proxy
  HTTP callers such as `/tools/invoke` full admin.
- **Local processes are trusted like the proxy.** The gateway accepts identity
  headers from `127.0.0.1` (`allowLoopback`), so any user or process on the
  host can act as any allowed user, including admins. Only use the proxy on
  hosts where every local account is trusted.
- **TLS.** `internal` uses Caddy's own CA (browsers warn until you trust it),
  `acme` gets a Let's Encrypt certificate (DNS name, ports 80/443 reachable
  from the internet), `files` copies your certificate and key.
- **Local CLI.** OpenClaw does not allow a token in trusted-proxy mode; local
  CLI calls use the gateway password from `.env`. Run them as the service
  user: `sudo -u openclaw -H openclaw devices list`.
- The role owns `/etc/caddy/Caddyfile`. Setting `proxy_enabled: false` later
  does not remove Caddy; remove it by hand or with `state: absent` and
  `slm_ansible_role_openclaw_uninstall_caddy: true`.

> **Breaking change.** The proxy is on by default. Playbooks that only set
> `slm_ansible_role_openclaw_gateway_token` fail validation until they set
> `slm_ansible_role_openclaw_gateway_password` and at least one basic user, or
> `slm_ansible_role_openclaw_proxy_enabled: false`. Already paired browsers
> keep working; open the `https://` URL from then on.

## Example playbook

```yaml
- name: Deploy OpenClaw
  hosts: openclaw
  become: true
  vars:
    slm_ansible_role_openclaw_gateway_password: "{{ vault_openclaw_gateway_password }}"
    slm_ansible_role_openclaw_proxy_basic_users:
      - name: alice
        password_hash: "{{ vault_openclaw_alice_hash }}"
    slm_ansible_role_openclaw_proxy_admin_users:
      - alice
    slm_ansible_role_openclaw_env:
      ANTHROPIC_API_KEY: "{{ vault_anthropic_api_key }}"
    slm_ansible_role_openclaw_config:
      agents:
        defaults:
          heartbeat:
            every: 2h
  roles:
    - role: slm-ansible-role-openclaw
```

Open `https://<host>` and log in as `alice`. Without the proxy
(`proxy_enabled: false`, token auth) the gateway listens on loopback only;
reach it via SSH port forwarding (`ssh -L 18789:127.0.0.1:18789 <host>`).

Remove everything again:

```yaml
- name: Remove OpenClaw
  hosts: openclaw
  become: true
  vars:
    slm_ansible_role_openclaw_state: absent
    slm_ansible_role_openclaw_uninstall_purge: true
  roles:
    - role: slm-ansible-role-openclaw
```

## Testing

Fast local tests of input validation and the rendered gateway config (install nothing):

```bash
ansible-playbook -i tests/inventory tests/test_validation.yml
ansible-playbook -i tests/inventory tests/test_config.yml
yamllint . && ansible-lint
```

Molecule scenarios create real VMs through `eclipse-slm/molecule_generic`, which
dispatches to a backend role selected by `MOLECULE_DRIVER`. The scenarios default
to `kubevirt` (`molecule_kubevirt`, needs a kubeconfig: `KUBECONFIG` or
`~/.kube/config`). `openstack` (`molecule_openstack`, installed from
`ipa-big.molecule_openstack`) is only a fallback when no Kubernetes cluster is
available and needs the usual `OS_*` environment variables.

| Scenario | What it tests |
|---|---|
| `install-ubuntu2204`, `install-ubuntu2404` | Prepare installs without the proxy (token auth), converge switches to the proxy (basic auth, TLS internal); verify checks user, permissions, config (including an OpenAI-compatible provider, models looked up from a fake `/v1/models` endpoint, a failing lookup and the default model), secrets, rejection of an invalid config, running services, closed gateway port, proxy responses (401/200, forged identity header), auto-approval of a new browser device and the gateway probe; idempotence. |
| `install-oidc-ubuntu2404` | Proxy with OIDC against a fake IdP (config level): oauth2-proxy and Caddy running, private config, pinned version, redirect to sign-in for unauthenticated or forged requests; idempotence. |
| `uninstall-ubuntu2204`, `uninstall-ubuntu2404` | Install with OIDC and TLS files, then `absent` with purge and Node.js removal; verify the gateway, oauth2-proxy and copied certificates are gone and Caddy is kept; idempotence. |

```bash
molecule test -s install-ubuntu2404                           # KubeVirt
MOLECULE_DRIVER=openstack molecule test -s install-ubuntu2404  # OpenStack fallback
```

The scenarios' first model provider points at a dead port by default. Set
`OPENCLAW_OPENAI_ID`, `OPENCLAW_OPENAI_BASE_URL`, `OPENCLAW_OPENAI_API_KEY` and
`OPENCLAW_OPENAI_MODEL_ID` to test against a real OpenAI-compatible endpoint.

Run OpenStack scenarios one at a time: `molecule_openstack` names its SSH key
pair per project, so parallel OpenStack scenarios collide on it.

## License

MIT

## Author

Benjamin Goetz, Fraunhofer IPA
