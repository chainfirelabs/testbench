"""What a role grants, and what every guarded endpoint asks for.

The API used to ask two questions: "is this an admin?" and "may this one
write?". That is a ladder with three rungs, and a ladder cannot express
"somebody who reads the audit log but does not reset passwords" or "somebody
who records test results but does not edit the inventory" — both of which are
ordinary things for a team to want, and both of which used to mean handing out
`admin`.

So each guard names a permission instead, and a role is a set of them. The
three built-in roles are seeded with the sets that reproduce the old behaviour
exactly, so nobody's access changes on upgrade; what changes is that the sets
are now editable and an installation can define its own roles beside them.

Two rules keep this from becoming a way to lock an installation out of itself:

* `users.manage` is the permission that edits roles, so only somebody who
  already has it can grant it. `api/roles.py` additionally refuses to remove it
  from the last role that has it.
* Read access is not a permission. Every authenticated caller can read what the
  API exposes to them; the permissions below are all about writing, or about
  reading the two collections that are privileged in themselves (the audit log
  and user accounts). Making reads a permission too would mean a role with none
  of these could not use the app at all, which is what `readonly` is for.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Role


@dataclass(frozen=True)
class PermissionDef:
    key: str
    label: str
    description: str
    # Grouped only for the sake of the screen that ticks them.
    group: str


DEVICES_EDIT = "devices.edit"
SOFTWARE_EDIT = "software.edit"
TESTS_EDIT = "tests.edit"
VIEWS_SAVE = "views.save"
AUDIT_VIEW = "audit.view"
USERS_MANAGE = "users.manage"
SCHEMA_MANAGE = "schema.manage"
PLUGINS_MANAGE = "plugins.manage"
SETTINGS_MANAGE = "settings.manage"

PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(
        DEVICES_EDIT, "Edit devices", "Add, change, delete, import, check out and scan devices.",
        "Inventory",
    ),
    PermissionDef(
        SOFTWARE_EDIT, "Edit software",
        "Add and change software and versions, and the vendor compatibility claims under them.",
        "Inventory",
    ),
    PermissionDef(
        TESTS_EDIT, "Record tests", "Add, change and delete test results.", "Inventory",
    ),
    PermissionDef(
        VIEWS_SAVE, "Save views",
        "Save named column, filter and sort layouts for the list pages.",
        "Inventory",
    ),
    PermissionDef(
        AUDIT_VIEW, "View the audit log",
        "Read and export the full audit log. A device's own changelog is visible "
        "to anyone who can see the device and does not need this.",
        "Administration",
    ),
    PermissionDef(
        USERS_MANAGE, "Manage users and roles",
        "Create accounts, reset passwords, assign roles, and define what roles grant.",
        "Administration",
    ),
    PermissionDef(
        SCHEMA_MANAGE, "Manage the schema",
        "Define device types, device fields and the fields on software, tests and vendor devices.",
        "Administration",
    ),
    PermissionDef(
        PLUGINS_MANAGE, "Manage plugins",
        "Enable plugins for device types and configure how they run.",
        "Administration",
    ),
    PermissionDef(
        SETTINGS_MANAGE, "Manage settings",
        "Configure AI provider profiles and other installation settings.",
        "Administration",
    ),
)

PERMISSION_KEYS = tuple(item.key for item in PERMISSIONS)

# The three roles that ship, and the ones an upgrade finds already in use.
# Their permission sets are exactly the old ladder: `admin` held every guard,
# `tester` passed `require_write`, and `readonly` passed neither.
BUILTIN_ROLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "readonly", "Read only",
        "Can see the fleet, the software catalogue and test results, and change nothing.",
        (),
    ),
    (
        "tester", "Tester",
        "Can edit the inventory, the software catalogue and test results.",
        (DEVICES_EDIT, SOFTWARE_EDIT, TESTS_EDIT, VIEWS_SAVE),
    ),
    (
        "admin", "Administrator",
        "Full access, including user accounts, the schema and the audit log.",
        PERMISSION_KEYS,
    ),
)


def seed_roles(db: Session) -> None:
    """Create the built-in roles if they are not there yet.

    Only creates. An installation that has edited what `tester` grants keeps
    its edit across restarts — the seed is how the rows come into existence,
    not a policy that reasserts itself every boot.
    """
    existing = {slug for slug in db.scalars(select(Role.slug))}
    for slug, name, description, permissions in BUILTIN_ROLES:
        if slug in existing:
            continue
        db.add(Role(
            slug=slug, name=name, description=description,
            permissions=list(permissions), is_builtin=True,
        ))
    db.commit()


def valid_permissions(values) -> list[str]:
    """Keep the permissions this build knows about, in catalogue order.

    Order first so a stored list reads the same as the screen that wrote it,
    and filtered because a role written by a newer build — or by hand — must
    not smuggle in a permission no guard checks and nobody can see.
    """
    wanted = set(values or [])
    return [key for key in PERMISSION_KEYS if key in wanted]


def grantable_roles(db: Session, held: frozenset[str]) -> list[str]:
    """The roles a holder of `held` may put on a key they mint.

    Any role that grants no more than they already have. On the old three-rung
    ladder that was "your role or a lower one", which is the same answer read
    as a subset — and the subset reading keeps working once roles stop being
    comparable, where "lower" has no meaning but "grants no more than this"
    still does.

    A key that could carry more than its owner would be a way to escalate by
    minting: revoke the owner's access and the key they left behind outranks it.
    """
    return sorted(
        role.slug for role in db.scalars(select(Role))
        if frozenset(valid_permissions(role.permissions)) <= held
    )


def caller_permissions(db: Session, user) -> frozenset[str]:
    """What one caller may do.

    An API key arrives with its set already resolved and capped against its
    owner's (`api/deps.py`), and carries it on the User object as
    `granted_permissions`. Everyone else's is their role's, read on each request
    rather than baked into their token, so a role edited in the UI takes effect
    on the next call instead of at the user's next login.
    """
    granted = getattr(user, "granted_permissions", None)
    if granted is not None:
        return granted
    return role_permissions(db, user.role)


def satisfies_role(db: Session, held: frozenset[str], required_slug: str | None) -> bool:
    """Whether `held` covers everything the named role grants.

    Plugin manifests name a role ("required_user_role": "admin") rather than a
    permission, because they are written against the application, not against
    one installation's role list. Read as "everything that role grants" rather
    than "is called that", a custom role with the same reach passes and one with
    less does not — which is what the manifest meant by naming admin.
    """
    if not required_slug:
        return True
    return role_permissions(db, required_slug) <= held


def role_permissions(db: Session, slug: str | None) -> frozenset[str]:
    """What a role grants right now.

    An unknown slug grants nothing rather than raising. A user can be left
    holding a role that has since been deleted, or one an OIDC group mapping
    names but this installation never defined; the safe reading of "I do not
    know what this role is" is "it grants nothing", not a 500 on every request.
    """
    if not slug:
        return frozenset()
    role = db.scalar(select(Role).where(Role.slug == slug))
    return frozenset(valid_permissions(role.permissions)) if role else frozenset()
