"""Render the nginx template and replace this process with nginx (no shell)."""
import os
from pathlib import Path


def main() -> None:
    template = Path("/etc/nginx/templates/default.conf.template").read_text()
    hsts = os.environ.get("TB_HSTS_MAX_AGE", "")
    if any(character in hsts for character in '\r\n"\\$'):
        raise ValueError("TB_HSTS_MAX_AGE contains invalid nginx string characters")
    Path("/tmp/default.conf").write_text(template.replace("${TB_HSTS_MAX_AGE}", hsts))
    os.execv("/usr/sbin/nginx", ["nginx", "-c", "/etc/nginx/nginx.conf", "-g", "daemon off;"])


if __name__ == "__main__":
    main()
