# GitHub credential context contract

The shared `scripts/gh_credential_context.py` diagnostic observes the executor,
not the credential secret. It never calls `gh auth token`, logs auth headers,
reads config contents or Keychain secret values, or hashes token values.

## Observation API

`describe_context(environ=..., surface=..., target_host=..., effective_user=...,
executor_host=..., gh_path=..., gh_version=...)` is the pure adapter entrypoint.
`observe_context(surface=..., target_host=...)` observes the local effective UID,
OS hostname, resolved executable and parsed `gh --version`. On Windows the local
observer uses `GetUserNameW` for the current OS thread user (including impersonation),
not USERNAME or another environment username. Unavailable OS user observation
fails with `effective_user_unavailable`. This observed name is diagnostic evidence,
not a SID, account authorization or a credential proof. Windows API contract
stubs on another OS are not native Windows acceptance. See the
[Microsoft API contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getusernamew).
Adapters must supply
their actual surface from trusted execution context; a caller label is not proof
of isolation or authenticated provider identity. The standalone helper records
`--executor-surface` (default `cli`) as a declared label.

The returned schema version 1 object contains:

| Fields | Meaning |
|---|---|
| `surface`, `effective_user`, `executor_host` | Private executor identity |
| `gh_path`, `gh_version` | Resolved CLI path and numeric version |
| `target_host`, `gh_host` | Actual API host and ambient GH_HOST value |
| `config_source`, `config_dir`, `config_resolved` | Directory selector, lexical absolute path and resolved path; no config contents |
| `selectors` | Nonempty presence booleans for four token variables |
| `credential_source` | Selected variable name or `stored` (possible stored source, not proof one exists) |

For github.com and subdomains of ghe.com, select GH_TOKEN, then GITHUB_TOKEN.
For GitHub Enterprise Server, select GH_ENTERPRISE_TOKEN, then
GITHUB_ENTERPRISE_TOKEN. Empty selectors do not override a nonempty selector.
A selected invalid credential is never bypassed by removing variables or trying
stored auth. Config directory precedence is GH_CONFIG_DIR, XDG_CONFIG_HOME/gh,
Windows AppData/GitHub CLI, then HOME/.config/gh.
If none is configured and a host-applicable environment token is selected,
`config_source` is `unavailable` and both config paths are null. This observation
can continue without inventing a home directory or reading stored credentials.
Stored-source observation with no config root still fails with
`config_home_unknown`. If a config root appears later, context equality rejects
the changed observation. Token presence is not authentication or permission
proof; a failed selected credential is still rejected without fallback.
Source: [GitHub CLI environment manual](https://cli.github.com/manual/gh_help_environment).

## Binding and limits

`require_same_context(reviewed, current)` rejects unequal objects with a fixed
`context_drift` error. It provides no grant, permission, token type, selected
repository list, expiry, or account proof. Read API success cannot establish
Administration: write. Token presence cannot establish fine-grained scope.

The helper uses a private `--context-out` file from dry-run and requires it with
`--context-in` and `--payload-in` for apply. The snapshot also binds repository,
operation, ruleset ID, selected mutation method/endpoint, canonical payload SHA256,
and explicit stored-auth/replacement flags. It creates the file exclusively with
mode 0600. Use a trusted private directory; no private snapshot belongs in Git,
Issues or PRs. A file is a reviewed precondition, not authenticated authorization.
The existing `--yes` confirms the reviewed operation; valid existing task authority
does not require a second generic human question. Only the selected GH_TOKEN retains the existing default apply eligibility.
GITHUB_TOKEN, Enterprise selectors and stored sources require the existing explicit
`--allow-stored-gh-auth` override in the reviewed snapshot and apply. Despite its
legacy name, that flag acknowledges the actual selected source; it never switches
to stored credentials. A GH_TOKEN that does not apply to the target host cannot
qualify by presence alone. Selection/override does not prove adequate scope.

The helper pins the target host and resolved gh executable for API calls, checks
context before discovery and again immediately before mutation, and supplies the
reviewed in-memory payload through a private temporary file. Unexpected observable
drift stops before mutation. Failed calls return fixed classifications, not raw
CLI error/output bodies. No automatic credential switching or login occurs.

Presence-only comparison **cannot detect** replacement of a token with another
nonempty value, a stored account/token change at the same config location, or a
binary replacement reporting the same path/version. It does not create an atomic
OS boundary against concurrent modification after a check. Callers requiring
stronger guarantees must use a trusted runtime lease/isolated execution and
independent authority validation; do not label this diagnostic an atomic merge
or authorization gate. No secret fingerprint is used to hide these limitations.

## Outcome classification

`classify_observation` returns bounded codes from explicit observations:

| Evidence | Code / limitation |
|---|---|
| Historical 401 | `historical_unresolved_401`; later success never changes its root cause |
| Current 401 | `authentication_failed`; manually check the selected credential/host in the same executor |
| Successful authenticated probe | `authentication_succeeded`; requires caller to identify a real auth probe |
| Ordinary successful API read | `request_succeeded`; no permission/grant inference |
| Verified insufficient permission | `insufficient_permissions` |
| HTTP 403 alone | `permission_or_policy_denied`; does not prove which permission is missing |
| HTTP 404 alone | `not_found_or_inaccessible`; not proof of feature ineligibility |
| Runtime denial | `runtime_denied` |
| Independently verified unavailable feature | `feature_unavailable` |
| Known transport failure | `transport_failure` |
| Other / ambiguous failure | `unknown` |

The helper extracts only an HTTP status from a failed gh call; absent a status it
reports unknown. Timeout/launch failure is a transport failure. The diagnostic
must not infer historical root causes from a later successful observation.

## Trusted publication/runtime integration

Saihai #131 / #133 adapters should import the pure API from this skill's verified
artifact, supply actual executor observations, keep snapshots in private task
evidence, and bind them to their existing reviewed grant's task/repo/payload and
lease. Reobserve before execution, reject drift, independently validate permission,
feature eligibility and runtime authority, and keep each outcome separate.
No new generic approval is needed when that existing grant remains valid.

This change supplies the source API, hardening consumer and offline adapter
contract tests. Saihai adapter wiring and real-surface acceptance belong to the
subsequent integration work; they are not claimed as implemented here.

## Validation

Run `python -m pytest -q github-oss-repo-hardening/tests` for offline synthetic
fixtures and `python -m pytest -q` for the repository Python suite. No credential,
network, account switch or live settings mutation is needed for these fixtures.
