import logging
import socket

from ..services import network_scan
from .registry import CommandContext, command

logger = logging.getLogger("tox.fred")

# Fixed ports of the Fred apps (they run as the fred-* user services listed in services.json)
APPS = (("Frontend", 5200), ("Admin module", 5201))
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


def _status(host: str, port: int) -> str:
    """🟢 answers on the network address; 🟡 up, but only reachable from this PC (listening on 127.0.0.1); 🔴 down."""
    if _reachable(host, port):
        return "🟢"
    return "🟡" if _reachable("127.0.0.1", port) else "🔴"


@command(
    "fred",
    description="(Admin) URLs de Fred (frontend y admin-module) para abrirlas desde cualquier dispositivo de la red local",
    category="Administración",
    admin_only=True,
)
def fred(ctx: CommandContext) -> str:
    network = network_scan.primary_network()
    if network is None:
        return "No encontré la IP de esta PC en la red local (no tiene una IP privada: 192.168.x.x, 10.x.x.x...)."
    host = str(network.address)
    logger.info("FRED urls requested by %s (%s)", ctx.message.user_key, host)
    # A blank line between the title and each app, so every URL stands apart
    lines = [f"{_status(host, port)} {name}: http://{host}:{port}" + (LOCAL_ONLY if _status(host, port) == "🟡" else "")
             for name, port in APPS]
    return "\n\n".join(["🌐 Fred, para abrir desde cualquier dispositivo de la red local:", *lines])
