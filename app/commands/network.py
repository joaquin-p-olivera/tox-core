import logging

from ..services import network_scan
from .registry import CommandContext, command

logger = logging.getLogger("tox.network")

PRIVATE_MAC_NOTE = "MAC privada (celular o PC con privacidad)"


def _device_line(device: network_scan.Device) -> str:
    details = [device.name] if device.name else []
    details.append(device.mac)
    if device.vendor:
        details.append(device.vendor)
    elif device.private_mac:
        details.append(PRIVATE_MAC_NOTE)
    if device.is_gateway:
        details.append("router")
    return f"• {device.ip} — {' · '.join(details)}"


def _report_text(report: network_scan.NetworkReport) -> str:
    network = report.network
    lines = [f"🌐 Red local {network.subnet} ({network.interface})", f"🖥️ Esta PC: {network.address}"]
    if report.devices:
        lines += ["", f"📡 Otros dispositivos ({len(report.devices)}):", *(_device_line(d) for d in report.devices)]
    else:
        lines += ["", "📡 No encontré otros dispositivos."]
    return "\n".join(lines)


@command(
    "ipmaster",
    description="(Admin) IP de esta PC en la red local y los demás dispositivos conectados, con su nombre si se conoce",
    aliases=("ipm",),
    category="Administración",
    admin_only=True,
)
def local_ip(ctx: CommandContext) -> str:
    reports = network_scan.scan(device_names=network_scan.load_device_names(ctx.settings.DEVICES_FILE))
    logger.info("NETWORK scan by %s: %s", ctx.message.user_key,
                ", ".join(f"{r.network.subnet}: {len(r.devices)} devices" for r in reports) or "no local network")
    if not reports:
        return "No encontré una red local: esta PC no tiene una IP privada (192.168.x.x, 10.x.x.x...)."
    return "\n\n".join(_report_text(r) for r in reports) + "\n\nSolo aparecen los conectados actualmente a la red."
