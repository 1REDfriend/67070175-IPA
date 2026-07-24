#!/usr/bin/env python3
"""Lab Netmiko-RE.

Check and display all the ACTIVE interfaces (line protocol up) on routers
R1-R2 together with uptime, parsing the CLI output with regular expressions.

`show interfaces` on IOSv has no per-interface up-timer, so per interface we
report the "Last input" activity, and per router we report the device uptime
from `show version` -- both extracted with regex.

Devices are reached through R0's NAT port-forwards on 192.168.1.186 using SSH
public-key auth (password auth is disabled on the devices).

Requires: pip install netmiko
"""

import os
import re
import sys

# Sibling file netmiko.py would shadow the installed netmiko package.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p not in ("", ".", _here) and os.path.abspath(p or ".") != _here]

from netmiko import ConnectHandler  # noqa: E402

KEY_FILE = os.path.expanduser("~/.ssh/id_rsa_ipa")
R0_IP = "192.168.1.186"
LEGACY_ALGOS = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}

ROUTERS = [
    {"name": "R1", "port": 2204},
    {"name": "R2", "port": 2205},
]

# One interface header + (optionally) its "Last input" line inside the block.
IFACE_RE = re.compile(
    r"^(?P<intf>\S+) is (?P<status>administratively down|up|down), "
    r"line protocol is (?P<proto>up|down)",
    re.MULTILINE,
)
LASTIN_RE = re.compile(r"Last input (?P<lastin>[\w:.]+)")
UPTIME_RE = re.compile(r"^(?P<host>\S+) uptime is (?P<uptime>.+)$", re.MULTILINE)


def parse_active_interfaces(show_interfaces_output):
    """Return [(intf, last_input)] for interfaces whose line protocol is up."""
    matches = list(IFACE_RE.finditer(show_interfaces_output))
    active = []
    for i, m in enumerate(matches):
        if m.group("proto") != "up":
            continue
        block = show_interfaces_output[m.start():(matches[i + 1].start() if i + 1 < len(matches) else len(show_interfaces_output))]
        li = LASTIN_RE.search(block)
        active.append((m.group("intf"), li.group("lastin") if li else "n/a"))
    return active


def base_params(port):
    return {
        "device_type": "cisco_ios",
        "host": R0_IP,
        "port": port,
        "username": "admin",
        "use_keys": True,
        "key_file": KEY_FILE,
        "allow_agent": False,
        "disabled_algorithms": LEGACY_ALGOS,
        "fast_cli": False,
        "conn_timeout": 20,
    }


def main():
    all_ok = True
    for r in ROUTERS:
        try:
            with ConnectHandler(**base_params(r["port"])) as conn:
                ver = conn.send_command("show version")
                intf_out = conn.send_command("show interfaces")
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            print(f"[FAIL] {r['name']} -> {type(exc).__name__}: {exc}")
            continue

        um = UPTIME_RE.search(ver)
        uptime = um.group("uptime") if um else "unknown"
        active = parse_active_interfaces(intf_out)

        print(f"\n=== {r['name']} (uptime: {uptime}) ===")
        print(f"  {'Interface':<26} {'Last input':<12}")
        print(f"  {'-'*26} {'-'*12}")
        for intf, lastin in active:
            print(f"  {intf:<26} {lastin:<12}")
        print(f"  ({len(active)} active interfaces)")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
