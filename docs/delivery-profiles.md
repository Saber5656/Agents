# Project delivery profiles

A delivery profile is an optional, repository-owned index of the exact checks
for one project. It is useful when a project has application or data-bearing
steps, but it is not a required application artifact and it does not authorize
deployment or data migration. When no profile exists, use only the concrete
commands and targets already documented by the repository's README, package
scripts, CI workflows, and project configuration. Do not invent a build,
startup, migration, or deployment command from a project type.

When present, keep the profile next to the project, for example
`docs/delivery-profile.json`. It may point to existing repository commands and
configuration rather than duplicating their contents. Commands are argument
arrays, run with the repository root as their working directory, and must use
disposable targets for mutation checks. Do not put credentials, personal
paths, or environment secrets in the file.

```json
{
  "profile_version": 1,
  "name": "example-service",
  "source": {"revision_command": ["git", "rev-parse", "HEAD"]},
  "build": {"command": ["./scripts/build-fixture.sh"], "artifact": "dist/app"},
  "test": {"command": ["python", "-m", "pytest", "-q"]},
  "service": {
    "start": ["./scripts/start-fixture.sh"],
    "health": ["./scripts/check-health.sh"],
    "behavior": ["./scripts/check-behavior.sh"]
  },
  "deploy": {
    "requested": false,
    "target": "disposable-test-target",
    "readback": ["./scripts/read-deployment.sh"]
  },
  "migration": {
    "requested": false,
    "apply": ["./scripts/migrate-fixture.sh"],
    "rollback": ["./scripts/rollback-fixture.sh"],
    "readback": ["./scripts/read-migration.sh"]
  }
}
```

The build and test commands may establish `packaged` evidence. `service.start`
must be followed by `health` and, when the project has a user path, a
`behavior` check against the running disposable service; a successful process
start alone is not `usable`. Record the artifact path and digest, source
revision, service endpoint, and observed output.

`deploy` and `migration` are opt-in. When `requested` is false or the section
is absent, do not run its commands. A deployment result is `deployed` only
after the designated target and endpoint are read back. A migration result is
`migrated` only after the target version and data outcome are read back. A
failed disposable operation must exercise the profile's rollback or recovery
command and verify that the previous usable state remains; that evidence is
`recovered`, not a successful deployment.

Validation reports each stage independently:

| Stage | Required evidence | Does not prove |
| --- | --- | --- |
| `packaged` | source revision, artifact identity, bytes or digest | deployed or usable |
| `deployed` | designated target mutation and endpoint read-back | migrated or usable |
| `migrated` | target/data version and migration read-back | deployed or usable |
| `recovered` | failed operation, bounded rollback, previous-state read-back | a new deployment |
| `usable` | user-visible health or behavior result from the real service | production availability |

For a no-deployment task, capture before/after release and tag refs and a
snapshot of each related disposable database resource. Do not read unrelated
databases merely to fill the report. Any unexpected change to a related ref or
database is a failure. For an explicitly requested deployment, keep the actual
target and read-back output; a plan, mock, provider success string, or
metadata-only result remains `incomplete`.
