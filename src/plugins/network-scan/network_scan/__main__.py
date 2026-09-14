import sys

if len(sys.argv) > 1 and sys.argv[1] == "worker":
    from .worker import main
    main()
else:
    import uvicorn
    uvicorn.run("network_scan.server:app", host="0.0.0.0", port=8080)
