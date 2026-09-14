import uvicorn
uvicorn.run("device_info_plugin.server:app", host="0.0.0.0", port=8080)
