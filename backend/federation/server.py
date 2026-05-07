"""
federation/server.py — Flower federated learning server for Sahayak.

Aggregates personalization model updates (intervention timing MLP)
from consenting user devices using FedAvg.
"""
from __future__ import annotations

import threading
from typing import Any

import flwr as fl
import structlog
from fastapi import APIRouter
from flwr.server.strategy import FedAvg
from pydantic import BaseModel

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level server-thread state
# ---------------------------------------------------------------------------

_server_thread: threading.Thread | None = None
_server_address: str = ""


# ---------------------------------------------------------------------------
# FL server launcher
# ---------------------------------------------------------------------------


def start_fl_server(num_rounds: int = 3, address: str = "localhost:9090") -> None:
    """
    Start a Flower federated learning server synchronously.

    Intended to be called from a background thread so the FastAPI event loop
    is not blocked.  Uses FedAvg with a minimum of 2 clients for both fitting
    and evaluation, matching the typical caregiver-device + patient-device
    deployment topology.

    Parameters
    ----------
    num_rounds:
        Number of federated training rounds to execute before the server stops.
    address:
        ``"host:port"`` string the server will bind to.
    """
    strategy = FedAvg(
        min_fit_clients=2,
        min_evaluate_clients=2,
        min_available_clients=2,
    )

    log.info(
        "fl_server.starting",
        address=address,
        num_rounds=num_rounds,
    )

    fl.server.start_server(
        server_address=address,
        config=fl.server.ServerConfig(num_rounds=num_rounds),
        strategy=strategy,
    )

    log.info("fl_server.stopped", address=address)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

federation_server_router = APIRouter(
    prefix="/federation-server",
    tags=["federation-server"],
)


class StartServerRequest(BaseModel):
    num_rounds: int = 3
    address: str = "localhost:9090"


@federation_server_router.post(
    "/start",
    summary="Start the Flower FL aggregation server in a background thread",
)
def start_server(body: StartServerRequest) -> dict[str, Any]:
    """
    Launch the Flower federated learning server.

    The server runs in a daemon background thread so that this endpoint returns
    immediately.  If a server thread is already running the request is rejected
    with a descriptive message rather than starting a second instance.
    """
    global _server_thread, _server_address

    if _server_thread is not None and _server_thread.is_alive():
        return {
            "started": False,
            "address": _server_address,
            "detail": "FL server is already running.",
        }

    _server_address = body.address

    _server_thread = threading.Thread(
        target=start_fl_server,
        kwargs={"num_rounds": body.num_rounds, "address": body.address},
        name="fl-server",
        daemon=True,
    )
    _server_thread.start()

    log.info(
        "fl_server.thread_launched",
        address=body.address,
        num_rounds=body.num_rounds,
    )

    return {"started": True, "address": body.address}


@federation_server_router.get(
    "/status",
    summary="Check whether the Flower FL server thread is alive",
)
def server_status() -> dict[str, Any]:
    """Return the current running state and bound address of the FL server."""
    running = _server_thread is not None and _server_thread.is_alive()
    return {
        "running": running,
        "address": _server_address if running else "",
    }
