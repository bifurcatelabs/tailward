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
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
