"""Migrate the database, then run the API without a shell or console scripts."""
import os

from alembic import command
from alembic.config import Config
import uvicorn


def main() -> None:
    print("Running database migrations...", flush=True)
    command.upgrade(Config("alembic.ini"), "head")
    print("Starting API...", flush=True)
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000, proxy_headers=True,
        forwarded_allow_ips=os.environ.get("TB_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
