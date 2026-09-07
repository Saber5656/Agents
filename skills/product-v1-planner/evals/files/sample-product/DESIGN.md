# Sample Product v1

## Requirements

- `REQ-AUTH-1`: Every remote request must authenticate a user and deny cross-user access.
- `REQ-AUDIT-1`: Every create, update, and delete operation must append an attributable audit event.
- `REQ-EXPORT-1`: A user can export their own records as JSON.

## Non-goals

- Organization accounts and shared workspaces are deferred beyond v1.

## Completion

v1 is complete when authentication, attributable audit events, and user-scoped JSON export pass end-to-end validation.
