"""``python -m tailward.daemon`` entry point (spawned by lifecycle.start).

Imports are intentionally absolute (``from tailward.daemon.app import
create_app``) rather than relative (``from .app``) so this module can
also serve as a PyInstaller bundle entry point — relative imports
break when PyInstaller treats ``__main__.py`` as a top-level script.
Absolute imports work for both ``python -m tailward.daemon`` (the
``-m`` switch sets up the package context correctly) and the
PyInstaller-bundled ``tailward-daemon.exe`` path.
"""

from __future__ import annotations

import argparse

import uvicorn

from tailward.daemon.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7878)
    args = parser.parse_args()

    app = create_app()
    # Build Server+Config explicitly (instead of uvicorn.run) so the
    # /shutdown route can flip ``server.should_exit`` for cooperative
    # teardown from the v3 Tauri shell. Functionally equivalent to
    # ``uvicorn.run(app, host=..., port=...)`` otherwise.
    config = uvicorn.Config(app=app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    # Opt the lifespan teardown into ``os._exit`` so we bypass
    # asyncio's loop-teardown-blocks-on-executor-shutdown defect.
    # Only set in this entrypoint — TestClient and other in-process
    # users keep the safe default (lifespan returns normally).
    app.state.exit_on_lifespan_close = True
    server.run()
    # ``server.run()`` doesn't return on the production path: the
    # lifespan teardown calls ``os._exit(0)`` once worker stops +
    # ledger close complete (gated by ``exit_on_lifespan_close``).
    # We only land here in test/embedded contexts where the flag
    # wasn't set; nothing to do.


if __name__ == "__main__":
    main()
