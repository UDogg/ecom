"""Starts the service: uvicorn with uvloop and httptools, one process (the webhook deliverer runs in it)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.asgi:app", host="0.0.0.0", port=8003, loop="uvloop", http="httptools", workers=1,
                server_header=False, access_log=False, timeout_keep_alive=30)
