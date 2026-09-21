import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .api import (
    audit,
    auth,
    devices,
    device_schema,
    device_types,
    notifications,
    saved_filters,
    search,
    suggestions,
    tests,
    software,
    users,
    vendor_devices,
    entity_fields,
    plugins,
    roles,
    ai_providers,
)
from .config import settings
from .core.security import hash_password
from .db import Base, SessionLocal, engine
from .models import User
from .services.checkout import start_checkout_sweep_loop
from .services.scan import start_interval_loop
from .services.entity_fields import initialize_entity_fields
from .services.permissions import seed_roles
from .services.device_schema import refresh_managed_indexes, seed_device_schema
from .services.device_schema_yaml import reconcile_from_settings
from .services.plugin_host import registry as plugin_registry, start_refresh_loop as start_plugin_refresh_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def seed_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.scalar(
            select(User).where(User.username == settings.admin_username, User.auth_provider == "local")
        )
        if existing is None:
            admin = User(
                username=settings.admin_username,
                role="admin",
                auth_provider="local",
                password_hash=hash_password(settings.admin_password),
            )
            db.add(admin)
            db.commit()
            logger.info("Seeded local admin user '%s'", settings.admin_username)
    finally:
        db.close()


INSECURE_DEFAULTS = {
    "jwt_secret": "change-me-in-production",
    "admin_password": "admin",
}


def warn_about_defaults() -> None:
    """Shout about credentials that ship in the compose file.

    The JWT secret signs every session token and the OIDC state; left at its
    default, anyone who has read this repository can mint an admin token.
    """
    for name, default in INSECURE_DEFAULTS.items():
        if getattr(settings, name) == default:
            logger.warning(
                "SECURITY: TB_%s is still the built-in default. Set it before "
                "exposing this service to anything but localhost.",
                name.upper(),
            )


def reconcile_plugin_fields() -> None:
    """Apply manifests discovered at startup or by the readiness retry loop."""
    with SessionLocal() as db:
        device_schema.reconcile_installed_plugin_fields(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_about_defaults()
    # create_all is a dev convenience; Alembic remains the source of truth for schema.
    Base.metadata.create_all(engine)
    initialize_entity_fields()
    with SessionLocal() as schema_db:
        # Definitions and default types first, so a YAML document has a catalog
        # to attach to and a bootstrap import has something to compare against.
        seed_device_schema(schema_db)
        reconcile_from_settings(schema_db)
        refresh_managed_indexes(schema_db)
    plugin_registry.refresh()
    reconcile_plugin_fields()
    start_plugin_refresh_loop(reconcile_plugin_fields)
    # Before the admin account: the role it is given has to exist first.
    with SessionLocal() as role_db:
        seed_roles(role_db)
    seed_admin()
    start_interval_loop()
    start_checkout_sweep_loop()
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(search.router, prefix=API_PREFIX)
app.include_router(suggestions.router, prefix=API_PREFIX)
app.include_router(devices.router, prefix=API_PREFIX)
app.include_router(device_types.router, prefix=API_PREFIX)
app.include_router(device_schema.router, prefix=API_PREFIX)
app.include_router(device_schema.fields_router, prefix=API_PREFIX)
app.include_router(software.router, prefix=API_PREFIX)
app.include_router(vendor_devices.router, prefix=API_PREFIX)
# The same rows at /vendor-devices, searchable without naming a software first.
app.include_router(vendor_devices.catalog_router, prefix=API_PREFIX)
app.include_router(tests.router, prefix=API_PREFIX)
app.include_router(saved_filters.router, prefix=API_PREFIX)
app.include_router(notifications.router, prefix=API_PREFIX)
app.include_router(audit.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(roles.router, prefix=API_PREFIX)
app.include_router(entity_fields.router, prefix=API_PREFIX)
app.include_router(plugins.router, prefix=API_PREFIX)
app.include_router(plugins.host_router, prefix=API_PREFIX)
app.include_router(ai_providers.router, prefix=API_PREFIX)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": settings.version}
