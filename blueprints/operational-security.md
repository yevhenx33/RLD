# Operational Security Blueprint

This blueprint defines the minimum operational security standard for future AI agents working on RLD. Treat it as a quality gate before changing runtime, deployment, secrets, networking, monitoring, or incident-response behavior.

## Core Principles

- Default to read-only investigation until the requested change is clear.
- Never print, summarize, or copy secret values. Report only file paths, key names, fingerprints, lengths, or redacted classifications.
- Preserve public `/rpc` behavior unless explicitly asked to change it.
- Prefer one canonical runtime path over compatibility layers. Do not add duplicate Compose files, Nginx routes, env files, or aliases without a migration plan and removal date.
- Keep production-facing services loopback-bound or behind the edge proxy unless a public exposure is intentional and documented.
- Any hardening change must include verification commands and their results.

## Runtime Invariants

Future agents must preserve these invariants unless the user explicitly approves a change:

- ClickHouse host ports bind to `127.0.0.1` only, while app containers reach it on the internal Docker network as `rld_clickhouse`.
- Dashboard port `8090` binds to `127.0.0.1` only and requires Basic Auth.
- Plaintext `.env` files remain ignored and mode `600`.
- Mainnet execution JWT material lives outside the repo, e.g. `/etc/rld/mainnet/jwt.hex`, mode `600`, not under `docker/mainnet/`.
- Destructive simulation admin endpoints require `X-Admin-Token` and explicit allowed client addresses.
- Public edge routing remains deny-by-default for non-allowlisted `/api/*` paths.
- Analytics and simulation APIs must not return raw exception strings to public clients.
- Faucet health must not reveal privileged addresses or funding account details.
- CI deployment must use SSH key authentication only.

## Required Audit Workflow

Before making operational changes:

1. Check the git working tree and do not revert unrelated user changes.
2. Identify affected surfaces:
   - Docker Compose
   - Host Nginx and container Nginx
   - UFW/firewall rules
   - Secret files and env files
   - CI deploy workflows
   - Public API routes and health endpoints
3. Read the relevant files before editing.
4. Classify impact:
   - Public exposure
   - Secret handling
   - Authentication or authorization
   - Data durability
   - Service availability
5. Apply the smallest change that preserves the invariants above.

## Required Verification

After operational edits, run the checks that match the affected surface. Do not claim success without evidence.

```bash
git status --short
python3 -m py_compile <changed-python-files>
docker compose -f <changed-compose-file> --env-file <env-file> config --quiet
sudo nginx -t
ss -lntup
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
bash docker/scripts/stack.sh smoke --allow-not-ready
```

For secret-handling changes, also verify:

```bash
git check-ignore -v docker/.env data-pipeline/.env frontend/.env.production docker/mainnet/jwt.hex
stat -c '%a %U:%G %n' docker/.env data-pipeline/.env /etc/rld/mainnet/jwt.hex
```

For dashboard changes, verify:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/
curl -sS -o /dev/null -w '%{http_code}\n' -u 'admin:<redacted>' http://127.0.0.1:8090/
```

Expected result: unauthenticated requests return `401`; authenticated requests return `200`.

## Change Review Checklist

Before finalizing, confirm:

- No new secret is tracked by git.
- No secret value appears in logs, diffs, command output, or final response.
- No new service is bound to `0.0.0.0` without explicit approval.
- No new public route bypasses the edge allowlist.
- No health, status, metrics, or error endpoint leaks credentials, stack traces, private addresses, or privileged account details.
- No CI workflow reintroduces password SSH, `sshpass`, or auto-install of deploy keys via password.
- No operational path depends on a local, untracked file unless the final response calls it out.

## Incident Triggers

Escalate clearly in the final response if any of these are observed:

- A database, dashboard, control-plane API, or metrics endpoint is public.
- A credential-bearing file is tracked or found in git history.
- A running container has an empty or default password.
- Firewall state conflicts with Compose expectations.
- A required smoke check fails after changes.

When escalating, include the affected path or service, observed evidence, blast radius, and immediate containment step. Do not include secret values.

## Handoff Standard

Every operational-security final response should include:

- What changed or what was audited.
- What was verified.
- Any live runtime changes made outside git.
- Any remaining manual action, especially key rotation, history purge, or deploy coordination.

