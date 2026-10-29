# Work signals: local progress dashboard

Run `python3 -m harness.dashboard` from the repository with `AGENTS_ROOT` and
`AGENTS_VAULT_ROOT` configured. It binds only to `127.0.0.1` and defaults to
port `8765`; `--ui-dir` selects the built client and `--labels FILE` supplies
an exact `{task_id: {source_title, title}}` display-label map.

First install and build the selected Product Design client:

```sh
cd apps/progress-dashboard
npm ci --prefer-offline --no-audit --no-fund
npm test
npm run build
cd ../..
set -a
. "$HOME/.config/agents/environment.env"
set +a
python3 -m harness.dashboard
```

Open the printed local address. Choose another `--port` if the default is in
use; do not stop an unrelated service. The server lives while that process is
running. This release does not install a startup service or enable phone access.
After changing the UI, build again; after changing Python, restart this server.

The first screen shows at most three recently updated active tasks, explicit
user decisions and the observation age. Recent results, all records and source
details start collapsed. A 24-hour-old running record moves out of the current
view without changing its stored state. This is not a heartbeat or a live view
of every native app conversation. Records absent from the task store are absent
from this view. Evidence remains in the existing Vault.

The server is read-only. `GET /api/status` returns the dashboard projection.
All requests require a loopback Host. API requests additionally require the
per-process `X-Dashboard-Token`, inserted into the initial HTML page. Page assets
load normally so the browser can receive that token. This local boundary is not
remote-access authentication; never expose it through a public tunnel or bind
it to a network interface.
`GET /api/evidence/<id>` returns bounded text for known Vault files. Only
HTTPS links and safely rooted `vault://` files are represented as evidence.
All responses are `no-store`; there are no mutation endpoints or CORS headers.

The task/service databases are read through the existing stable snapshot
collector. On a failed task read, the process retains its last good snapshot
and original observation time in memory, with a warning. It stores no new task
database. Missing service or Vault sources remain explicit. Refresh occurs on
opening, manually, once per minute while visible, and on return after sleep.

Local evidence is restricted to linked Vault files, plain UTF-8 text, and at
most 1 MB per source. Missing, oversized, binary and unsupported references stay
unavailable. Read source reveals original evidence, which may be Japanese.
English display-label maps are optional, local presentation metadata; they
apply only while the original title matches exactly and never change records.

Validation: `python3 -m unittest tests.test_dashboard tests.test_status` plus
`npm test` and `npm run build` in the app directory. Browser interaction and
design comparison evidence belongs in the private Vault and the app's
`design-qa.md`. The UI uses the official [Phosphor React icons](https://github.com/phosphor-icons/react).
