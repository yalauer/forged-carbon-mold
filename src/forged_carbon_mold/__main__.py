from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Lokale CAD-Anwendung für Forged-Carbon-Pressformen")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.environ.setdefault("FORGED_CARBON_DATA", str(Path.home() / ".forged-carbon-mold"))
    uvicorn.run("forged_carbon_mold.web:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
