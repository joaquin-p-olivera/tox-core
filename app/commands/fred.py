import logging
import socket

from ..services import network_scan
from .registry import CommandContext, command

logger = logging.getLogger("tox.fred")

# The Fred apps: name, fixed port (they run as the fred-* user services listed in services.json) and whether the app
# is meant to listen on this PC only (fred-studio: its Vite dev server binds to 127.0.0.1 on purpose).
APPS = (("Frontend", 5200, False), ("Admin module", 5201, False), ("Studio", 5203, True))
CONNECT_TIMEOUT_SECONDS = 1.0


def _reachable(host: str, port: int) -> bool:
    """Whether something accepts connections at host:port, using the LAN address: exactly what another device on the
    network would do. An app listening only on 127.0.0.1 (a Vite dev server without --host) answers on localhost but
    NOT here, which is the case worth telling apart from an app that is simply off."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


LOCAL_ONLY = " (solo desde esta PC)"
LOCALHOST = "127.0.0.1"


def _status(host: str, port: int, local_only: bool = False) -> str:
    """🟢 answers on the network address (or, for an app that is local-only by design, on this PC); 🟡 up, but only
    reachable from this PC when it should be reachable from the network (listening on 127.0.0.1); 🔴 down."""
    if local_only:
        return "🟢" if _reachable(LOCALHOST, port) else "🔴"
    if _reachable(host, port):
        return "🟢"
    return "🟡" if _reachable(LOCALHOST, port) else "🔴"


@command(
    "fred",
    description="(Admin) Estado y URLs de Fred (frontend, admin-module y studio), una URL por mensaje",
    category="Administración",
    admin_only=True,
)
def fred(ctx: CommandContext) -> str | list[str]:
    """One message with each app's state, then one message per app with nothing but its URL (so it can be copied or
    tapped on its own)."""
    network = network_scan.primary_network()
    if network is None:
        return "No encontré la IP de esta PC en la red local (no tiene una IP privada: 192.168.x.x, 10.x.x.x...)."
    host = str(network.address)
    logger.info("FRED urls requested by %s (%s)", ctx.message.user_key, host)
    states, urls = [], []
    for name, port, local_only in APPS:
        status = _status(host, port, local_only)
        # An app that only listens on 127.0.0.1 can't be opened through the LAN address, not even from this PC
        on_this_pc_only = local_only or status == "🟡"
        states.append(f"{status} {name}" + (LOCAL_ONLY if on_this_pc_only and status != "🔴" else ""))
        urls.append(f"http://{'localhost' if on_this_pc_only else host}:{port}")
    return ["\n".join(states), *urls]
