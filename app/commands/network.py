import logging

from ..services import network_scan, speedtest, wake_on_lan
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


def _wol_list(devices: list[tuple[str, str]], prefix: str) -> str:
    lines = [f"{number}. {name} — {mac}" for number, (mac, name) in enumerate(devices, start=1)]
    return "\n".join([
        "💡 Dispositivos que puedo despertar (Wake-on-LAN):", "", *lines, "",
        f"Uso: {prefix}wol <número o nombre>",
        "Solo enciende los que tienen Wake-on-LAN activado (celulares y la mayoría de las TV no lo soportan).",
    ])


@command(
    "wol",
    description="(Admin) Enciende un dispositivo de la red con Wake-on-LAN; sin parámetros, lista los disponibles",
    usage="wol [número o nombre]",
    category="Administración",
    admin_only=True,
)
def wake(ctx: CommandContext) -> str:
    names = network_scan.load_device_names(ctx.settings.DEVICES_FILE)
    devices = [(mac, name) for mac, name in names.items() if wake_on_lan.is_valid_mac(mac)]
    if not devices:
        return "No hay dispositivos configurados: agregalos en devices.json (MAC → nombre)."
    query = ctx.raw_args.strip()
    if not query:
        return _wol_list(devices, ctx.prefix)

    if query.isdigit():
        index = int(query) - 1
        matches = [devices[index]] if 0 <= index < len(devices) else []
    else:
        wanted = query.lower().replace("-", ":")
        matches = [d for d in devices if wanted in (d[0], d[1].lower())] or [d for d in devices if wanted in d[1].lower()]
    if not matches:
        return f"No encontré ese dispositivo. Mirá la lista con {ctx.prefix}wol."
    if len(matches) > 1:
        return "Hay varios que coinciden, usá el número:\n" + "\n".join(
            f"{devices.index(d) + 1}. {d[1]}" for d in matches)

    mac, name = matches[0]
    broadcasts = [wake_on_lan.GLOBAL_BROADCAST]
    network = network_scan.primary_network()
    if network:
        broadcasts.insert(0, str(network.subnet.broadcast_address))
    try:
        wake_on_lan.send(mac, broadcasts)
    except OSError as error:
        logger.warning("WOL could not be sent to %s: %s", mac, error)
        return "No pude enviar el paquete por la red."
    logger.info("WOL sent by %s to %s (%s)", ctx.message.user_key, mac, name)
    return f"📡 Mandé la señal de encendido a {name}. Si tiene Wake-on-LAN activado, debería prenderse en unos segundos."


@command(
    "speedtest",
    description="(Admin) Mide la velocidad de internet de esta PC (tarda unos 15 segundos)",
    category="Administración",
    admin_only=True,
)
def speed(ctx: CommandContext) -> str:
    try:
        result = speedtest.run()
    except speedtest.SpeedtestError as error:
        return str(error)
    logger.info("SPEEDTEST by %s: %.1f down / %.1f up / %.0f ms", ctx.message.user_key,
                result.download_mbps, result.upload_mbps, result.latency_ms)
    return (f"🚀 Velocidad de internet (desde esta PC)\n"
            f"⬇️ Bajada: {result.download_mbps:.1f} Mbps\n"
            f"⬆️ Subida: {result.upload_mbps:.1f} Mbps\n"
            f"⏱️ Latencia: {result.latency_ms:.0f} ms")
