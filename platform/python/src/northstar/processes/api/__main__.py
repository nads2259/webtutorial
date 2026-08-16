"""``python -m northstar.processes.api`` — run the API process under uvicorn.

Host/port/reload are read from the environment so the same artifact serves every profile
(docs/18 §1); no environment-specific code branches. The app is imported lazily by uvicorn from
:mod:`northstar.processes.api.asgi` so configuration is resolved in the server process.
"""

from __future__ import annotations

import os


def main() -> None:
    """Start the uvicorn server for the API ASGI app."""
    import uvicorn

    uvicorn.run(
        "northstar.processes.api.asgi:app",
        host=os.environ.get("NORTHSTAR_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("NORTHSTAR_API_PORT", "8000")),
        log_level=os.environ.get("NORTHSTAR_API_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
