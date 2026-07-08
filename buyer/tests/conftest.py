"""Test fixtures: spin up the faithful mock seller (real HTTP server) for the
streamable-HTTP MCP client to connect to. A real listening server is required —
the MCP streamable-HTTP transport is an HTTP client, not an in-process ASGI shim."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_BUYER_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def seller_base_url() -> str:
    """Launch `uvicorn tests.mock_seller:app` on a free port; tear down after the
    session. Yields the base URL (no /mcp path)."""
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(_BUYER_DIR)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.mock_seller:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(_BUYER_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    # wait for the port to accept connections
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"mock seller exited early:\n{out}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("mock seller did not start in time")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
