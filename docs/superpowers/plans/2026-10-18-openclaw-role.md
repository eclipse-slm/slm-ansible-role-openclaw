# OpenClaw Ansible Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ansible role that installs, configures (and removes) the OpenClaw gateway as a hardened systemd service on Ubuntu 22.04/24.04.

**Architecture:** `tasks/main.yml` validates input and dispatches to `install.yml` (nodejs → user → package → config → service) or `uninstall.yml`. Ansible owns `openclaw.json` (rendered from a dict, validated by the OpenClaw CLI before replacement) and a single-quoted `.env` with secrets. Tests: a fast local validation playbook plus Molecule scenarios on KubeVirt and OpenStack VMs.

**Tech Stack:** ansible-core 2.21, community.general, Molecule 26 (create/destroy via `eclipse-slm.molecule_kubevirt` and `ipa-big.molecule_openstack`), ansible-lint, yamllint, NodeSource Node.js 24, npm package `openclaw`.

**Spec:** `docs/superpowers/specs/2026-10-18-openclaw-role-design.md`

## Global Constraints

- Every role variable is prefixed `slm_ansible_role_openclaw_`; role-internal variables `__slm_ansible_role_openclaw_`.
- Supported: Ubuntu `22.04`, `24.04` only. States: `present`, `absent`.
- Default `slm_ansible_role_openclaw_version: "2026.10.4"`; Node major `24` (engines `>=24.16.0 <25 || >=26.1.0`).
- Service runs as non-root user `openclaw`, home `/var/lib/openclaw`, state dir `/var/lib/openclaw/.openclaw` (0700); `openclaw.json` and `.env` are 0600 owned by that user.
- Unit `/etc/systemd/system/openclaw-gateway.service`: `ExecStart=/usr/bin/openclaw gateway run --port <port>`, `Restart=always`, `RestartSec=5`, `RestartPreventExitStatus=78`, `TimeoutStopSec=330`, `Environment=OPENCLAW_SERVICE_REPAIR_POLICY=external`.
- Base config always contains `gateway.mode: local`, `gateway.bind: loopback`, `gateway.port: <port>`, `gateway.auth.mode: token`, `gateway.auth.token: "${OPENCLAW_GATEWAY_TOKEN}"`; user dict merged over it with `combine(recursive=True)`.
- Secrets appear only in `.env`, never in `openclaw.json` or task output (`no_log: true`).
- FQCN modules, task names start with a capitalised verb, no trailing period, 2-space indent (sibling-role `ansible.instructions.md`).
- No CI / Jenkinsfile.
- Molecule `molecule.yml` uses the `ansible:` layout of `slm-ansible-role-wazuh`; scenario `name:` equals directory name.

## Review Focus

- Unknown key in `slm_ansible_role_openclaw_config` on a re-run → play fails at the template task, existing `openclaw.json` untouched, running gateway unaffected. (Test: Task 4, Step 1, rescue-block check.)
- Gateway token or env value containing `"`, `$`, `#` or spaces → reaches the gateway literally; a `'` or newline is rejected up front. (Tests: Task 4 `.env` content assertion; Task 1 validation cases.)
- Upgrading `slm_ansible_role_openclaw_version` on a host that already runs an older version → package replaced, gateway restarted. (Test: Task 3, install prepare installs `2026.9.33` first; verify asserts `2026.10.4`.)
- Secrets leaking into `openclaw.json` → never; config holds `${OPENCLAW_GATEWAY_TOKEN}` only. (Test: Task 4 verify asserts token string absent from config.)
- Running `absent` twice, or on a host where nothing is installed → no failure, no change. (Test: Task 6 idempotence step of uninstall scenarios.)

---

### Task 1: Role skeleton, linting and input validation

**Files:**
- Create: `defaults/main.yml`, `vars/main.yml`, `meta/main.yml`, `handlers/main.yml` (empty list `[]` for now), `tasks/main.yml`, `tasks/validate.yml`, `tasks/install.yml` (empty `[]`), `tasks/uninstall.yml` (empty `[]`), `.yamllint`, `.ansible-lint`, `.gitignore`
- Test: `tests/test_validation.yml`, `tests/expect_failure.yml`, `tests/inventory`

**Interfaces:**
- Produces: all defaults exactly as spec §4 (with the renamed prefix); internal vars in `vars/main.yml`:
  - `__slm_ansible_role_openclaw_supported_versions: ['22.04', '24.04']`
  - `__slm_ansible_role_openclaw_states: ['present', 'absent']`
  - `__slm_ansible_role_openclaw_bin: /usr/bin/openclaw`
  - `__slm_ansible_role_openclaw_unit_path: "/etc/systemd/system/{{ slm_ansible_role_openclaw_service_name }}.service"`
  - `__slm_ansible_role_openclaw_nodesource_uri: "https://deb.nodesource.com/node_{{ slm_ansible_role_openclaw_nodejs_major }}.x"`
  - `__slm_ansible_role_openclaw_nodesource_key: https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key`
  - `__slm_ansible_role_openclaw_base_config` (gateway block per Global Constraints, `agents.defaults.workspace: "{{ slm_ansible_role_openclaw_state_dir }}/workspace"`)
  - `__slm_ansible_role_openclaw_effective_config: "{{ __slm_ansible_role_openclaw_base_config | combine(slm_ansible_role_openclaw_config, recursive=True) }}"`
  - `__slm_ansible_role_openclaw_env_all: "{{ {'OPENCLAW_GATEWAY_TOKEN': slm_ansible_role_openclaw_gateway_token} | combine(slm_ansible_role_openclaw_env) }}"`

- [ ] **Step 1: Write the failing validation test**

`tests/expect_failure.yml` (included per case): first `set_fact: __case_failed: false`, then runs `ansible.builtin.include_role: name: "{{ playbook_dir | dirname }}"`, `tasks_from: validate.yml` (never `main.yml`, so a regression can never install anything on the developer machine) inside `block`, with `rescue` setting `__case_failed: true` and capturing `ansible_failed_result.msg`; after the block, assert `__case_failed | default(false)` and that the msg contains `item.expect`.

`tests/test_validation.yml` — play `hosts: localhost`, `connection: local`, `gather_facts: true`, loops `include_tasks: expect_failure.yml` over (`vars:` per case applied via `item.vars` and `vars:` on include):

| case | vars | expect (substring of fail_msg) |
|---|---|---|
| invalid state | `slm_ansible_role_openclaw_state: purged` | `slm_ansible_role_openclaw_state must be one of` |
| missing token | `state: present`, token `''` | `slm_ansible_role_openclaw_gateway_token must be set` |
| quote in token | token `"ab'c"` | `must not contain single quotes or newlines` |
| newline in env | `slm_ansible_role_openclaw_env: {FOO: "a\nb"}` | `must not contain single quotes or newlines` |

`tests/inventory`: `localhost ansible_connection=local`.

- [ ] **Step 2: Run test to verify it fails**

Run: `ansible-playbook -i tests/inventory tests/test_validation.yml`
Expected: FAIL — assertions on `__case_failed` fail because `tasks/validate.yml` has no asserts yet.

- [ ] **Step 3: Implement asserts in `tasks/validate.yml`**

Assert tasks (each with `quiet: true` and a `fail_msg` starting with the expected substring above), in this order: distribution `Ubuntu` and `ansible_facts['distribution_version']` in supported list (`fail_msg` starts `Unsupported OS`); state in `__slm_ansible_role_openclaw_states`; when present: token length > 0; when present: no value of `__slm_ansible_role_openclaw_env_all` contains `'` or `\n` (`no_log: true` on this assert, fail_msg must not echo values). `tasks/main.yml`: `include_tasks: validate.yml`, then `include_tasks: install.yml` when present / `uninstall.yml` when absent.

`meta/main.yml`: copy layout of `slm-ansible-role-litellm` (author Benjamin Goetz, company Fraunhofer IPA, `role_name: openclaw`, `namespace: eclipse_slm`, license MIT, `min_ansible_version: "2.15"`, platforms Ubuntu `jammy`, `noble`, galaxy_tags `openclaw`, `ai`, `gateway`).

`.yamllint`: copy from `ipa-big/molecule_openstack`. `.ansible-lint`: `skip_list: [var-naming[no-role-prefix]]` with comment "prefix slm_ansible_role_openclaw_ differs from role_name by convention". `.gitignore`: `.idea`, `local.env`, `molecule/*/roles/*`, `__pycache__`.

- [ ] **Step 4: Run tests and linters**

Run: `ansible-playbook -i tests/inventory tests/test_validation.yml && yamllint . && ansible-lint`
Expected: playbook `failed=0`; yamllint and ansible-lint report no errors.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: add role skeleton with input validation"
```

---

### Task 2: Molecule harness and Node.js installation

**Files:**
- Create: `requirements.yml`, `molecule/default/create-kv.yml`, `molecule/default/destroy-kv.yml`, `molecule/default/create-os.yml`, `molecule/default/destroy-os.yml`, `molecule/default/prepare.yml`, `molecule/default-install/converge.yml`, `molecule/default-install/verify.yml`, `molecule/install-ubuntu2204-kv/molecule.yml`, `molecule/install-ubuntu2404-kv/molecule.yml`, `molecule/install-ubuntu2204-os/molecule.yml`, `molecule/install-ubuntu2404-os/molecule.yml`, `tasks/install/nodejs.yml`
- Modify: `tasks/install.yml`

**Interfaces:**
- Consumes: Task 1 vars.
- Produces: converge vars used by all later verify steps — `slm_ansible_role_openclaw_gateway_token: 'molecule-t0ken"$#x y'`, `slm_ansible_role_openclaw_env: {DUMMY_PROVIDER_KEY: not-a-real-key}`, `slm_ansible_role_openclaw_config: {agents: {defaults: {heartbeat: {every: 2h}}}}`. Converge applies the role via `include_role: name: "{{ lookup('env', 'MOLECULE_PROJECT_DIRECTORY') | basename }}"` with `become: true`, `hosts: all`.

Harness details:
- `requirements.yml` (repo root, referenced by `dependency.options.role-file/requirements-file: requirements.yml`): roles `eclipse-slm.molecule_kubevirt` (`src: git+https://github.com/eclipse-slm/molecule_kubevirt`, `scm: git`, `version: main`) and `ipa-big.molecule_openstack` (`version: 1.0.11`); collections `community.general >=9.0.0`, `kubernetes.core >=2.4.0`, `openstack.cloud >=2.0.0`. (Spec §7 listed `molecule/default/requirements.yml`; the root file replaces it — one source.)
- `create-kv.yml`/`destroy-kv.yml`: identical to `slm-ansible-role-wazuh/molecule/default/*-kv.yml`. `create-os.yml`/`destroy-os.yml`: identical to `slm-ansible-role-docker/molecule/default/*-os.yml`. `prepare.yml`: identical to `slm-ansible-role-docker/molecule/default/prepare.yml` (SSH wait).
- KV scenarios: platform `name: openclaw-ubuntu2204` / `openclaw-ubuntu2404`, `image: quay.io/containerdisks/ubuntu:22.04|24.04`, `volume_size: 15Gi`. OS scenarios: `image_name: Ubuntu 22.04 64-bit|Ubuntu 24.04 64-bit`, `groups: ['ssh']`. Both: `ansible:` block copied from wazuh install scenario, `playbooks: {create, destroy, prepare: ../default/prepare.yml, converge: ../default-install/converge.yml, verify: ../default-install/verify.yml}`, full test_sequence incl. `idempotence`.

- [ ] **Step 1: Write the failing verify**

`molecule/default-install/verify.yml` — play `hosts: all`, `become: true`, `gather_facts: false`. First block "Node.js": run `node --version` (`changed_when: false`), assert parsed version satisfies `version('24.16.0', '>=') and version('25', '<')` or `>= 26.1.0`, success/fail msgs.

- [ ] **Step 2: Run to verify it fails**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv`
Expected: converge OK (role does nothing yet), verify FAIL — `node: not found`.

- [ ] **Step 3: Implement `tasks/install/nodejs.yml`**

When `slm_ansible_role_openclaw_nodejs_manage`: apt install `ca-certificates`, `curl`, `gnupg` (`update_cache: true`, `cache_valid_time: 3600`); `ansible.builtin.deb822_repository` name `nodesource`, `uris: "{{ __slm_ansible_role_openclaw_nodesource_uri }}"`, `suites: nodistro`, `components: main`, `signed_by: "{{ __slm_ansible_role_openclaw_nodesource_key }}"`; apt `nodejs` `state: present` with `update_cache: true` (only when repo task changed, else cache_valid_time). Always (also when unmanaged): read `node --version`, assert engines range from Global Constraints. `tasks/install.yml` includes `install/nodejs.yml`.

- [ ] **Step 4: Run to verify it passes**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv && molecule idempotence -s install-ubuntu2404-kv`
Expected: verify assertions pass; idempotence reports no changed tasks.

- [ ] **Step 5: Lint and commit**

```bash
yamllint . && ansible-lint
git add -A && git commit -m "feat: install Node.js from NodeSource, add molecule harness"
```

---

### Task 3: Service user and OpenClaw package (incl. upgrade path)

**Files:**
- Create: `tasks/install/user.yml`, `tasks/install/package.yml`, `molecule/default-install/prepare.yml`
- Modify: `tasks/install.yml`, `molecule/default-install/verify.yml`, the four `molecule/install-*/molecule.yml` (`prepare: ../default-install/prepare.yml`)

**Interfaces:**
- Consumes: Task 1 vars, Task 2 converge.
- Produces: handler notification name `Restart openclaw gateway` (handler itself added in Task 5; until then add a placeholder handler in `handlers/main.yml` with that `name` running `ansible.builtin.debug`, replaced in Task 5).

- [ ] **Step 1: Extend verify (failing)**

Add blocks: `ansible.builtin.getent` passwd `openclaw` → assert home `/var/lib/openclaw`, shell `/usr/sbin/nologin`; `stat` state dir → `isdir`, mode `0700`, owner `openclaw`; `command: /usr/bin/openclaw --version` → assert stdout contains `OpenClaw 2026.10.4`.

`molecule/default-install/prepare.yml`: `import_playbook: ../default/prepare.yml`, then a play applying the role with `slm_ansible_role_openclaw_version: "2026.9.33"` and the Task 2 converge vars (upgrade source).

- [ ] **Step 2: Run to verify it fails**

Run: `molecule destroy -s install-ubuntu2404-kv && molecule create -s install-ubuntu2404-kv && molecule prepare -s install-ubuntu2404-kv && molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv`
Expected: verify FAIL on getent `openclaw`.

- [ ] **Step 3: Implement user and package tasks**

`user.yml`: `ansible.builtin.group` (system) + `ansible.builtin.user` (system, home, `create_home: true`, shell `/usr/sbin/nologin`); `file` state dir `directory`, 0700, owned by user.

`package.yml`: get installed version with `command: npm ls -g --depth=0 --json openclaw` (`changed_when: false`, `failed_when: rc not in [0, 1]`), derive `__slm_ansible_role_openclaw_installed_version` (`(stdout | from_json).dependencies.openclaw.version | default('')`); get npm version (`npm --version`, `changed_when: false`); when installed != desired run `command: npm install -g openclaw@{{ slm_ansible_role_openclaw_version }}` plus `--allow-scripts=openclaw` only if npm version `>= 11.16`; `notify: Restart openclaw gateway`. Then `stat` `__slm_ansible_role_openclaw_bin` and assert it exists.

- [ ] **Step 4: Run to verify it passes**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv && molecule idempotence -s install-ubuntu2404-kv`
Expected: verify passes (version 2026.10.4 after upgrade from 2026.9.33); idempotence clean.

- [ ] **Step 5: Lint and commit**

```bash
yamllint . && ansible-lint
git add -A && git commit -m "feat: create service user and install pinned openclaw package"
```

---

### Task 4: Configuration and secrets

**Files:**
- Create: `tasks/install/config.yml`, `templates/openclaw.json.j2`, `templates/openclaw.env.j2`
- Modify: `tasks/install.yml`, `molecule/default-install/verify.yml`

**Interfaces:**
- Consumes: `__slm_ansible_role_openclaw_effective_config`, `__slm_ansible_role_openclaw_env_all`, Task 2 converge vars.
- Produces: `tasks/install/config.yml` must be runnable standalone via `include_role: tasks_from: install/config.yml` (used by the verify rescue test).

- [ ] **Step 1: Extend verify (failing)**

Add blocks:
- `stat` config and env paths → each `mode == '0600'`, `pw_name == 'openclaw'`.
- `slurp` config → `cfg = content | b64decode | from_json`; assert `cfg.gateway.mode == 'local'`, `cfg.gateway.bind == 'loopback'`, `cfg.gateway.port == 18789`, `cfg.gateway.auth.token == '${OPENCLAW_GATEWAY_TOKEN}'`, `cfg.agents.defaults.heartbeat.every == '2h'`, and raw text does not contain `molecule-t0ken`.
- `find` under the state dir (`recurse: true`, `file_type: any`) → every result `pw_name == 'openclaw'` (the root-run `validate:` must not leave root-owned files).
- `slurp` env (`no_log: true`) → assert lines include `OPENCLAW_GATEWAY_TOKEN='molecule-t0ken"$#x y'` and `DUMMY_PROVIDER_KEY='not-a-real-key'`.
- Rejection test: record config `checksum` via `stat`; `block` include_role `tasks_from: install/config.yml` with `slm_ansible_role_openclaw_config: {gateway: {bogus: 1}}` and the converge token; `rescue` sets `__bad_config_rejected: true`; assert flag set and checksum unchanged.

- [ ] **Step 2: Run to verify it fails**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv`
Expected: FAIL — config file missing.

- [ ] **Step 3: Implement config tasks and templates**

`openclaw.json.j2`: `{{ __slm_ansible_role_openclaw_effective_config | to_nice_json(indent=2) }}`. Template task: dest config path, owner/group service user, 0600, `validate:` — if the root-owned-files assertion fails, point `OPENCLAW_STATE_DIR` in the validate command at a throwaway dir (e.g. `/tmp/openclaw-validate`) instead — `validate: "env OPENCLAW_CONFIG_PATH=%s OPENCLAW_STATE_DIR={{ slm_ansible_role_openclaw_state_dir }} {{ __slm_ansible_role_openclaw_bin }} config validate"`, `notify: Restart openclaw gateway`.

`openclaw.env.j2`: header comment `# Managed by Ansible`, then for each key in `__slm_ansible_role_openclaw_env_all | dictsort`: `KEY='value'`. Template task: 0600, owned by service user, `no_log: true`, notify restart.

- [ ] **Step 4: Run to verify it passes**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv && molecule idempotence -s install-ubuntu2404-kv`
Expected: all config/env/rejection assertions pass; idempotence clean.

- [ ] **Step 5: Lint and commit**

```bash
yamllint . && ansible-lint
git add -A && git commit -m "feat: render validated openclaw.json and secret env file"
```

---

### Task 5: systemd service

**Files:**
- Create: `tasks/install/service.yml`, `templates/openclaw-gateway.service.j2`
- Modify: `tasks/install.yml`, `handlers/main.yml`, `molecule/default-install/verify.yml`

**Interfaces:**
- Consumes: handler name `Restart openclaw gateway` (Task 3), paths from Task 1.
- Produces: running `openclaw-gateway.service`.

- [ ] **Step 1: Extend verify (failing)**

Add: `service_facts` → assert `ansible_facts.services['openclaw-gateway.service'].state == 'running'` and `.status == 'enabled'`; `wait_for` `127.0.0.1:18789` `timeout: 120`; `stat` unit path exists; `command: /usr/bin/openclaw gateway status` with `become_user: openclaw` and `environment` `OPENCLAW_STATE_DIR`, `OPENCLAW_CONFIG_PATH` → assert `rc == 0` and stdout contains `Connectivity probe: ok`.

- [ ] **Step 2: Run to verify it fails**

Run: `molecule converge -s install-ubuntu2404-kv && molecule verify -s install-ubuntu2404-kv`
Expected: FAIL — service not in `service_facts`.

- [ ] **Step 3: Implement unit, service tasks and handler**

Unit template `[Unit]` `Description=OpenClaw Gateway`, `After=network-online.target`, `Wants=network-online.target`; `[Service]` `Type=simple`, `User`/`Group`, `WorkingDirectory={{ slm_ansible_role_openclaw_home }}`, `Environment=` lines for `OPENCLAW_STATE_DIR`, `OPENCLAW_CONFIG_PATH`, `OPENCLAW_SERVICE_REPAIR_POLICY=external`, `OPENCLAW_LOG_LEVEL`, `ExecStart` and restart values per Global Constraints; `[Install]` `WantedBy=multi-user.target`. Template task 0644 root, notify restart.

`service.yml`: template unit; `ansible.builtin.systemd_service` `daemon_reload: true`, `enabled: "{{ slm_ansible_role_openclaw_service_enabled }}"`, `state: started`; `ansible.builtin.meta: flush_handlers`; `wait_for` port on `127.0.0.1`, `timeout: 120`, `msg` pointing to `journalctl -u openclaw-gateway`.

`handlers/main.yml`: replace placeholder with `systemd_service` `state: restarted`, `daemon_reload: true`.

- [ ] **Step 4: Run full install scenarios**

Run: `molecule test -s install-ubuntu2404-kv && molecule test -s install-ubuntu2204-kv && molecule test -s install-ubuntu2404-os && molecule test -s install-ubuntu2204-os`
Expected: each completes with converge, idempotence and verify green.

- [ ] **Step 5: Lint and commit**

```bash
yamllint . && ansible-lint
git add -A && git commit -m "feat: run openclaw gateway as systemd system service"
```

---

### Task 6: Uninstall

**Files:**
- Create: `molecule/default-uninstall/prepare.yml`, `molecule/default-uninstall/converge.yml`, `molecule/default-uninstall/verify.yml`, `molecule/uninstall-ubuntu2204-kv/molecule.yml`, `molecule/uninstall-ubuntu2404-kv/molecule.yml`, `molecule/uninstall-ubuntu2204-os/molecule.yml`, `molecule/uninstall-ubuntu2404-os/molecule.yml`
- Modify: `tasks/uninstall.yml`

**Interfaces:**
- Consumes: paths and service name from Task 1; install path from Tasks 2–5 (prepare runs a full default install with the Task 2 converge vars after `import_playbook: ../default/prepare.yml`).
- Produces: converge with `slm_ansible_role_openclaw_state: absent`, `slm_ansible_role_openclaw_uninstall_purge: true`, `slm_ansible_role_openclaw_uninstall_nodejs: true`.

- [ ] **Step 1: Write failing uninstall verify and scenarios**

Scenario `molecule.yml` files as in Task 2 but pointing to `../default-uninstall/{prepare,converge,verify}.yml`, names `uninstall-ubuntu2204-kv` etc.
`verify.yml`: `service_facts` → `'openclaw-gateway.service' not in ansible_facts.services`; `stat` unit path, `/usr/bin/openclaw`, `/var/lib/openclaw` → all `not exists`; `getent` passwd `openclaw` with `fail_key: false` → absent; `package_facts` → `'nodejs' not in ansible_facts.packages`; `stat /etc/apt/sources.list.d/nodesource.sources` → not exists.

- [ ] **Step 2: Run to verify it fails**

Run: `molecule converge -s uninstall-ubuntu2404-kv && molecule verify -s uninstall-ubuntu2404-kv`
Expected: FAIL — service still present (uninstall.yml is empty).

- [ ] **Step 3: Implement `tasks/uninstall.yml`**

`stat` unit; when it exists: `systemd_service` stopped + disabled; `file` unit absent; `systemd_service daemon_reload: true` (only when unit removal changed). `npm ls -g` check as in Task 3 (skip if `npm` missing); `command: npm uninstall -g openclaw` only when installed. When purge: `file` state dir absent, `user` absent (`remove: true`), `group` absent. When uninstall_nodejs: apt `nodejs` absent (`purge: true`), `deb822_repository` `nodesource` absent.

- [ ] **Step 4: Run full uninstall scenarios**

Run: `molecule test -s uninstall-ubuntu2404-kv && molecule test -s uninstall-ubuntu2204-kv && molecule test -s uninstall-ubuntu2404-os && molecule test -s uninstall-ubuntu2204-os`
Expected: all green, including idempotence (second `absent` run reports no changes).

- [ ] **Step 5: Lint and commit**

```bash
yamllint . && ansible-lint
git add -A && git commit -m "feat: support openclaw_state absent with optional purge"
```

---

### Task 7: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write README**

Sections: description; requirements (Ubuntu 22.04/24.04, collections from `requirements.yml`); variables table generated from `defaults/main.yml` (name, default, description); secrets guidance (vault `slm_ansible_role_openclaw_gateway_token` and provider keys in `slm_ansible_role_openclaw_env`, reference them in `slm_ansible_role_openclaw_config` as `${VAR}`); example playbook with an Anthropic key and `agents.defaults.model`; uninstall example; testing (`ansible-playbook -i tests/inventory tests/test_validation.yml`, `molecule test -s <scenario>` list, KubeVirt needs kubeconfig, OpenStack needs `OS_*` env); note that Ansible overwrites runtime config edits; license MIT; author.

- [ ] **Step 2: Verify every default is documented**

Run: `for v in $(grep -oE '^slm_ansible_role_openclaw_[a-z_]+' defaults/main.yml); do grep -q "$v" README.md || echo "MISSING $v"; done`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add README.md && git commit -m "docs: document role usage, variables and testing"
```
