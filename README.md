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
   `.env` file with the gateway token and other secrets.
5. Installs and starts `openclaw-gateway.service` and waits until the gateway port is open.

With `slm_ansible_role_openclaw_state: absent` it stops and removes the
service and package, and optionally the state directory, user, Node.js and
the NodeSource repository.

> Ansible owns `openclaw.json`. Changes made at runtime via the CLI or the
> Control UI are overwritten on the next role run. Put every setting into
> `slm_ansible_role_openclaw_config`.

## Requirements

- Target: Ubuntu 22.04 or 24.04 with systemd.
- Controller: ansible-core 2.15+.
- Internet access from the target to `deb.nodesource.com` and the npm registry.

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
| `slm_ansible_role_openclaw_gateway_token` | `""` | Gateway auth token. **Required** for `present`. Keep it in Ansible Vault. |
| `slm_ansible_role_openclaw_config` | `{}` | Free-form `openclaw.json` content, merged recursively over the base config (below). |
| `slm_ansible_role_openclaw_env` | `{}` | Extra variables for the `.env` file, e.g. provider API keys. |
| `slm_ansible_role_openclaw_service_name` | `openclaw-gateway` | systemd unit name. |
| `slm_ansible_role_openclaw_service_enabled` | `true` | Start the service at boot. |
| `slm_ansible_role_openclaw_log_level` | `info` | `OPENCLAW_LOG_LEVEL` for the service. |
| `slm_ansible_role_openclaw_uninstall_purge` | `false` | With `absent`: also remove state directory, user and group. |
| `slm_ansible_role_openclaw_uninstall_nodejs` | `false` | With `absent`: also remove Node.js and the NodeSource repository. |

Base config that `slm_ansible_role_openclaw_config` is merged over:

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

## Example playbook

```yaml
- name: Deploy OpenClaw
  hosts: openclaw
  become: true
  vars:
    slm_ansible_role_openclaw_gateway_token: "{{ vault_openclaw_gateway_token }}"
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

The gateway listens on loopback only. Reach it via SSH port forwarding
(`ssh -L 18789:127.0.0.1:18789 <host>`) or set `gateway.bind` in
`slm_ansible_role_openclaw_config` deliberately (see the OpenClaw security docs).

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

Fast local input-validation test (runs only the validation tasks, installs nothing):

```bash
ansible-playbook -i tests/inventory tests/test_validation.yml
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
| `install-ubuntu2204`, `install-ubuntu2404` | Prepare installs `2026.9.33`, converge upgrades to the pinned version; verify checks user, permissions, config, secrets, rejection of an invalid config, running service and gateway probe; idempotence. |
| `uninstall-ubuntu2204`, `uninstall-ubuntu2404` | Install, then `absent` with purge and Node.js removal; verify everything is gone; idempotence. |

```bash
molecule test -s install-ubuntu2404                           # KubeVirt
MOLECULE_DRIVER=openstack molecule test -s install-ubuntu2404  # OpenStack fallback
```

Run OpenStack scenarios one at a time: `molecule_openstack` names its SSH key
pair per project, so parallel OpenStack scenarios collide on it.

## License

MIT

## Author

Benjamin Goetz, Fraunhofer IPA
