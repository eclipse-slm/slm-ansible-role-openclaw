# OpenClaw Ansible Role — Design

Date: 2026-10-18
Status: Draft for review

## 1. Goal

An Ansible role that installs, configures and (optionally) removes the
[OpenClaw](https://docs.openclaw.ai/) gateway on Ubuntu hosts of a **headless
server fleet**. Runs must be unattended (no `openclaw onboard` wizard),
idempotent, and fully driven by Ansible variables.

Success criteria:

- A single role run on a fresh Ubuntu 22.04 / 24.04 host yields a running,
  enabled `openclaw-gateway.service` listening on the configured port.
- A second run reports no changes (idempotence).
- `slm_ansible_role_openclaw_state: absent` removes the service and package cleanly.
- Molecule scenarios prove the above on KubeVirt and OpenStack VMs.

Out of scope: CI pipeline (Jenkinsfile), Docker-based install, non-Ubuntu
distributions, channel pairing (e.g. WhatsApp QR login), multiple gateway
profiles per host.

## 2. Decisions

| Topic | Decision |
|---|---|
| Install method | NodeSource apt repo for Node.js, then `npm install -g openclaw@<pinned>` |
| Service model | System-wide systemd unit running as dedicated non-root user (upstream recommendation for always-on hosts; root is unsupported) |
| Config ownership | Ansible owns `openclaw.json`; rendered from a dict on every run, runtime edits are overwritten |
| Secrets | Rendered into `<state_dir>/.env` (0600) from (vaulted) vars; referenced from config via `${VAR}` |
| States | `present` / `absent` |
| Testing | Molecule with create/destroy from `eclipse-slm.molecule_kubevirt` (git `main`) and `ipa-big.molecule_openstack` (Galaxy, pinned `1.0.11`) |
| Role name / prefix | Role `openclaw`, namespace `eclipse-slm`, variable prefix `slm_ansible_role_openclaw_` |
| Platforms | Ubuntu 22.04, 24.04 |

## 3. Upstream facts relied upon

From docs.openclaw.ai (verified 2026-10-18):

- npm package `openclaw` (latest `2026.10.4`, engines `>=24.16.0 <25 || >=26.1.0`, so Node 25 is not supported). npm 11.16+ / 12
  requires `--allow-scripts=openclaw`; older npm must omit it.
- Config: `~/.openclaw/openclaw.json`, JSON5 (plain JSON is valid), strictly
  validated — unknown keys prevent startup. Must be a regular file (no symlink).
- State dir overridable via `OPENCLAW_STATE_DIR`; global env file at
  `$OPENCLAW_STATE_DIR/.env` is loaded and is the recommended place for
  provider API keys. `${VAR}` substitution is supported in config strings.
- Gateway default port `18789`; precedence `--port` > `OPENCLAW_GATEWAY_PORT`
  > `gateway.port` > default. Token via `OPENCLAW_GATEWAY_TOKEN`.
- Service name `openclaw-gateway.service`. Manual system unit example uses
  `Restart=always`, `RestartSec=5`, `RestartPreventExitStatus=78` (78 = invalid
  config), `TimeoutStopSec=330`, and `User=<non-root>`.
- When a system unit exists, set `OPENCLAW_SERVICE_REPAIR_POLICY=external` so
  `openclaw doctor --fix` does not install a competing user unit.
- Health/status: `openclaw gateway status`, `openclaw health`.

To be confirmed against the real CLI during implementation (see §8):

- Exact foreground command for `ExecStart` (expected `openclaw gateway run`).
- Whether a non-mutating config validation command exists for use as the
  template `validate:` parameter.

## 4. Variables (`defaults/main.yml`)

```yaml
slm_ansible_role_openclaw_state: present                 # present | absent

# Node.js
slm_ansible_role_openclaw_nodejs_major: 24
slm_ansible_role_openclaw_nodejs_manage: true            # set false if Node is managed elsewhere

# Package
slm_ansible_role_openclaw_version: "2026.10.4"           # exact npm version (latest on 2026-10-18)

# Service user / paths
slm_ansible_role_openclaw_user: openclaw
slm_ansible_role_openclaw_group: openclaw
slm_ansible_role_openclaw_home: /var/lib/openclaw
slm_ansible_role_openclaw_state_dir: "{{ slm_ansible_role_openclaw_home }}/.openclaw"
slm_ansible_role_openclaw_config_path: "{{ slm_ansible_role_openclaw_state_dir }}/openclaw.json"
slm_ansible_role_openclaw_env_path: "{{ slm_ansible_role_openclaw_state_dir }}/.env"

# Gateway
slm_ansible_role_openclaw_gateway_port: 18789
slm_ansible_role_openclaw_gateway_token: ""              # required when state=present (vault it)

# Free-form openclaw.json content (merged over role base config)
slm_ansible_role_openclaw_config: {}

# Extra env vars for .env, e.g. ANTHROPIC_API_KEY (vault them)
slm_ansible_role_openclaw_env: {}

# Service
slm_ansible_role_openclaw_service_name: openclaw-gateway
slm_ansible_role_openclaw_service_enabled: true
slm_ansible_role_openclaw_log_level: info

# Uninstall
slm_ansible_role_openclaw_uninstall_purge: false         # remove state dir + user
slm_ansible_role_openclaw_uninstall_nodejs: false        # remove nodejs + NodeSource repo
```

`vars/main.yml` holds constants: supported Ubuntu versions, valid states,
NodeSource repo URL / key URL, base config
(`gateway.port` bound to loopback by default, `agents.defaults.workspace`
under the state dir). The effective config is
`base_config | combine(slm_ansible_role_openclaw_config, recursive=True)`.

## 5. Role structure

```
defaults/main.yml
vars/main.yml
handlers/main.yml           # Restart openclaw gateway (systemd, daemon_reload)
meta/main.yml               # galaxy_info, platforms Ubuntu jammy/noble
tasks/main.yml              # asserts, dispatch on slm_ansible_role_openclaw_state
tasks/install.yml           # includes the files below in order
tasks/install/nodejs.yml
tasks/install/user.yml
tasks/install/package.yml
tasks/install/config.yml
tasks/install/service.yml
tasks/uninstall.yml
templates/openclaw.json.j2
templates/openclaw.env.j2
templates/openclaw-gateway.service.j2
requirements.yml            # molecule roles + collections
.yamllint, .ansible-lint, .gitignore, README.md
```

## 6. Behaviour

### 6.1 `tasks/main.yml`

Assert: `ansible_facts['distribution'] == 'Ubuntu'` and version in supported
list; `slm_ansible_role_openclaw_state` in `[present, absent]`; when present,
`slm_ansible_role_openclaw_gateway_token | length > 0`. Then include `install.yml` or
`uninstall.yml`.

### 6.2 Install

1. **nodejs** (when `slm_ansible_role_openclaw_nodejs_manage`): install prerequisites
   (`ca-certificates`, `curl`, `gnupg`); add NodeSource repo via
   `ansible.builtin.deb822_repository` (`node_{{ slm_ansible_role_openclaw_nodejs_major }}.x`,
   suite `nodistro`); install `nodejs`. Assert installed Node version meets
   24.16+/26.1+.
2. **user**: system group + user `slm_ansible_role_openclaw_user`, home `slm_ansible_role_openclaw_home`,
   shell `/usr/sbin/nologin`; create `slm_ansible_role_openclaw_state_dir` (0700).
3. **package**: read npm version; install `openclaw@{{ slm_ansible_role_openclaw_version }}`
   globally (`community.general.npm` if it supports the needed flag, else
   `ansible.builtin.command` guarded by an installed-version check so it only
   runs on mismatch). Pass `--allow-scripts=openclaw` only for npm >= 11.16.
   Notifies restart.
4. **config**: template `openclaw.json` (owner `slm_ansible_role_openclaw_user`, 0600,
   `to_nice_json`), with `validate:` if a validation command is available.
   Template `.env` (0600, `no_log: true`) containing `OPENCLAW_GATEWAY_TOKEN`
   and every key of `slm_ansible_role_openclaw_env`. Both notify restart. Secrets must only
   appear in `.env`; config references them as `${VAR}`.
5. **service**: template unit to
   `/etc/systemd/system/{{ slm_ansible_role_openclaw_service_name }}.service`:
   `User`/`Group`, `WorkingDirectory={{ slm_ansible_role_openclaw_home }}`,
   `Environment=OPENCLAW_STATE_DIR=…`, `OPENCLAW_CONFIG_PATH=…`,
   `OPENCLAW_SERVICE_REPAIR_POLICY=external`, `OPENCLAW_LOG_LEVEL=…`,
   `ExecStart=<openclaw bin> gateway run --port {{ slm_ansible_role_openclaw_gateway_port }}`,
   restart policy per §3. Notify restart (with `daemon_reload`). Ensure
   service enabled + started. `meta: flush_handlers`, then
   `ansible.builtin.wait_for` on `127.0.0.1:{{ slm_ansible_role_openclaw_gateway_port }}`.

### 6.3 Uninstall

Stop + disable service (tolerate missing unit); remove unit file;
daemon-reload; `npm uninstall -g openclaw` (only if installed). If
`slm_ansible_role_openclaw_uninstall_purge`: remove `slm_ansible_role_openclaw_state_dir`, user and group. If
`slm_ansible_role_openclaw_uninstall_nodejs`: remove `nodejs` package and NodeSource repo.

### 6.4 Error handling

- Invalid input fails fast in asserts with clear `fail_msg`.
- Exit code 78 (invalid config) stops systemd restart loops; the post-start
  `wait_for` then fails the play with a message pointing to
  `journalctl -u openclaw-gateway`.
- All tasks touching secrets use `no_log: true`.

## 7. Testing (Molecule)

```
molecule/default/create-kv.yml       # include eclipse-slm.molecule_kubevirt create.yml
molecule/default/destroy-kv.yml      # include eclipse-slm.molecule_kubevirt destroy.yml
molecule/default/create-os.yml       # include ipa-big.molecule_openstack create.yml
molecule/default/destroy-os.yml      # include ipa-big.molecule_openstack destroy.yml
molecule/default/prepare.yml         # wait for SSH on all hosts
molecule/default/requirements.yml
molecule/default-install/converge.yml
molecule/default-install/verify.yml
molecule/default-uninstall/prepare.yml   # install role first
molecule/default-uninstall/converge.yml  # slm_ansible_role_openclaw_state: absent, purge: true
molecule/default-uninstall/verify.yml
molecule/install-ubuntu2204-kv/molecule.yml
molecule/install-ubuntu2404-kv/molecule.yml
molecule/install-ubuntu2204-os/molecule.yml
molecule/install-ubuntu2404-os/molecule.yml
molecule/uninstall-ubuntu2204-kv/molecule.yml
molecule/uninstall-ubuntu2404-kv/molecule.yml
molecule/uninstall-ubuntu2204-os/molecule.yml
molecule/uninstall-ubuntu2404-os/molecule.yml
```

- KubeVirt scenarios follow `slm-ansible-role-wazuh` `molecule.yml` layout
  (images `quay.io/containerdisks/ubuntu:22.04|24.04`); OpenStack scenarios
  follow `slm-ansible-role-docker` (`image_name: Ubuntu 22.04|24.04 64-bit`,
  `groups: ['ssh']`). All scenarios include the idempotence step.
- Converge uses a dummy gateway token and no provider API key; the gateway
  must still start.
- **Install verify**: `openclaw --version` equals `slm_ansible_role_openclaw_version`;
  `service_facts` shows service running + enabled; port listening
  (`wait_for`); `stat` on config and `.env` shows 0600 owned by
  `slm_ansible_role_openclaw_user`; `openclaw gateway status` exits 0.
- **Uninstall verify**: service absent from `service_facts`, unit file and
  `openclaw` binary absent, state dir absent (purge).
- Lint: `ansible-lint` and `yamllint` clean.

Switching OpenStack to `eclipse-slm/molecule_openstack` later only changes
`create-os.yml`, `destroy-os.yml` and `requirements.yml`.

## 8. Risks / open items

- **ExecStart command** and **config validation command** must be confirmed
  by installing the package in a test VM (`openclaw gateway --help`,
  `openclaw config --help`). If no validation command exists, rely on exit
  code 78 + `wait_for` failure.
- NodeSource availability of `node_24.x` for jammy/noble is assumed; verified
  during first converge.
- Gateway may refuse to start without any model provider configured; if so,
  converge supplies a minimal provider config with a dummy key referenced via
  `${VAR}`.
