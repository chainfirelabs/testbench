import sys

import uvicorn

if len(sys.argv) > 1 and sys.argv[1] in {"ssh-worker", "mikrotik-worker"}:
    from .worker import main
    main()
else:
    uvicorn.run("reboot_plugin.server:app", host="0.0.0.0", port=8080)
