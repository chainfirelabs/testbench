# Roles and permissions

A role is a named set of permissions. Which roles exist, what each is called
and what each grants are all decided by the installation, on
**Users & Roles → Roles**. Assigning a role and deciding what it grants are the
same screen on purpose: they are two halves of one question.

## What ships

Three roles are seeded on first start. They exist as ordinary rows and are
editable like any other; they are marked built-in only in the sense that they
cannot be deleted and their identifiers cannot change.

| Role | Identifier | Grants |
|---|---|---|
| Read only | `readonly` | Nothing. Can see the fleet, the software catalogue and test results, and change none of it. |
| Tester | `tester` | Edit devices, edit software, record tests, save views. |
| Administrator | `admin` | Everything below. |

These sets reproduce exactly what the three roles meant before roles became
editable, so upgrading changes nobody's access.

## The permissions

| Permission | What it allows |
|---|---|
| `devices.edit` | Add, change, delete, import, check out and scan devices, and run plugin actions against them. |
| `software.edit` | Add and change software and versions, and the vendor compatibility claims under them. |
| `tests.edit` | Add, change and delete test results. |
| `views.save` | Save named column, filter and sort layouts for the list pages. |
| `audit.view` | Read and export the full audit log. |
| `users.manage` | Create accounts, reset passwords, assign roles, and define what roles grant. |
| `schema.manage` | Define device types, device fields, and the fields on software, tests and vendor devices. |
| `plugins.manage` | Enable plugins for device types and configure how they run. |
| `settings.manage` | Configure AI provider profiles and other installation settings. |

Reading is not a permission. Every signed-in user can read what the API exposes
to them; the list above is about writing, plus the two collections that are
privileged to read at all — the audit log and user accounts. A role with no
permissions is the read-only role.

A **device's own changelog** is deliberately not behind `audit.view`: it is
visible to anyone who can see the device, because "who changed this firmware
version?" is an inventory question. It is a redacted projection of the audit
log, not the log itself — see the Changelog tab on any device.

## Defining a role

**Users & Roles → Roles → + New role**. A role needs a name, an identifier
(the slug accounts and API keys carry, which cannot be changed afterwards) and
the permissions to tick. A role that reads the audit trail and nothing else,
for instance:

```
Name:        Auditor
Identifier:  auditor
Permissions: View the audit log
```

Editing a role takes effect on the next request — nobody has to sign out and
back in.

## Two rails you cannot remove

Both exist so that an installation cannot lock itself out:

* `users.manage` cannot be taken off the last role that has it, and the last
  active account holding such a role cannot be moved off it or deactivated.
* A role still held by an account cannot be deleted; move those accounts first.

## API keys

A key carries a role, and it never grants more than its owner holds. The
**Profile** page offers only the roles whose permissions are a subset of the
caller's own, and the API applies the same rule. The cap is re-applied on every
request against the owner's *current* role, so weakening somebody's role
weakens the keys they have already issued.

## Single sign-on

`TB_AUTHENTIK_GROUP_ROLE_MAP` maps an Authentik group to a role identifier, and
that identifier may name a role the installation defined — not just one of the
three built-ins. A mapping naming a role that does not exist is skipped, and
the login falls back to `TB_AUTHENTIK_DEFAULT_ROLE`. An SSO user's role is
re-resolved from their groups at every login, so it cannot be changed on the
Users page.
