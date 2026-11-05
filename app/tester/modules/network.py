"""Network adapters: Wi-Fi and Ethernet present, optional Wi-Fi scan to prove the radio works.

Options: wifi_required (default: true on laptops), scan (bool, default true).
"""

from __future__ import annotations

from tester.core.module import Result
from tester.core.util import run as sh

ID = "network"
NAME = "Sieć (Wi-Fi / LAN)"
KIND = "auto"
CONTRACT = 1
ORDER = 70


def count_networks(nmcli_out: str) -> int:
    return len({line.strip() for line in nmcli_out.splitlines() if line.strip()})


def run(ctx) -> Result:
    from tester.modules.sysinfo import network_adapters

    adapters = network_adapters()
    wifi = [a for a in adapters if a["type"] == "wifi"]
    eth = [a for a in adapters if a["type"] == "ethernet"]
    data: dict = {"adapters": adapters, "wifi": bool(wifi), "ethernet": bool(eth)}
    wifi_required = ctx.options.get("wifi_required", ctx.machine.get("chassis") == "laptop")
    if wifi and ctx.options.get("scan", True):
        sh(["nmcli", "device", "wifi", "rescan"], timeout=15)
        out = sh(["nmcli", "-t", "-f", "BSSID", "device", "wifi", "list"], timeout=20)
        if out is not None:
            data["wifi_networks_seen"] = count_networks(out)
    if not adapters:
        return Result("fail", data, "Brak kart sieciowych")
    if wifi_required and not wifi:
        return Result("fail", data, "Brak karty Wi-Fi")
    if wifi and data.get("wifi_networks_seen") == 0:
        return Result("warn", data, "Karta Wi-Fi nie widzi żadnej sieci (antena?)")
    return Result("pass", data)
