import uvicorn

uvicorn.run("test_summary_plugin.server:app", host="0.0.0.0", port=8080)
