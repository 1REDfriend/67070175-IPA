#!/usr/bin/env python3
"""Lab Netmiko-Jinja2.

Same result as netmiko.py, but the device configuration is generated from a
Jinja2 template (templates/device.j2) fed with structured per-device data.
This decouples the *intent* (data) from the *rendering* (template) from the
*delivery* (netmiko), which is the point of the refactor.

Devices are reached through R0's NAT port-forwards on 192.168.1.186 using SSH
public-key auth (password auth is disabled on the devices).

Requires: pip install netmiko jinja2
"""

import os
import sys

# Sibling file netmiko.py would shadow the installed netmiko package; drop this
# script's own directory from the import path so `import netmiko` finds the lib.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p not in ("", ".", _here) and os.path.abspath(p or ".") != _here]

from jinja2 import Environment, FileSystemLoader  # noqa: E402
from netmiko import ConnectHandler  # noqa: E402

KEY_FILE = os.path.expanduser("~/.ssh/id_rsa_ipa")
R0_IP = "192.168.1.186"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

LEGACY_ALGOS = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}

# Shared VTY access-control: management plane + Lab306 access network only.
VTY_ACL = {
    "name": "MGMT_ACCESS",
    "permit": [
        {"net": "172.31.36.0", "wild": "0.0.0.255"},
        {"net": "192.168.1.0", "wild": "0.0.0.255"},
    ],
}

# Structured intent per device -> rendered by templates/device.j2
DEVICES = [
    {
        "name": "S1",
        "port": 2203,
        "verify": "show vlan brief | include 101",
        "vars": {
            "vlans": [{"id": 101, "name": "CONTROL_DATA"}],
            "access_ports": [
                {"intf": "GigabitEthernet0/0", "vlan": 101},                     # -> Linux2
                {"intf": "GigabitEthernet0/3", "vlan": 101, "trunk_encap": True},  # -> R2
            ],
            "vty_acl": VTY_ACL,
        },
    },
    {
        "name": "R1",
        "port": 2204,
        "verify": "show ip ospf neighbor",
        "vars": {
            "ospf": {
                "process": 1,
                "vrf": "control-data",
                "networks": [
                    {"net": "10.1.1.0", "wild": "0.0.0.255", "area": 0},   # -> Linux1
                    {"net": "10.1.12.0", "wild": "0.0.0.3", "area": 0},    # R1-R2
                ],
                "passive": [],
                "default_originate": False,
            },
            "vty_acl": VTY_ACL,
        },
    },
    {
        "name": "R2",
        "port": 2205,
        "verify": "show ip ospf neighbor",
        "vars": {
            "ospf": {
                "process": 1,
                "vrf": "control-data",
                "networks": [
                    {"net": "10.1.2.0", "wild": "0.0.0.255", "area": 0},   # -> S1/Linux2
                    {"net": "10.1.12.0", "wild": "0.0.0.3", "area": 0},    # R1-R2
                ],
                "passive": ["GigabitEthernet0/3"],
                "default_originate": True,
            },
            "nat": {
                "acl_name": "NAT_INSIDE",
                "acl_permit": [{"net": "10.1.0.0", "wild": "0.0.255.255"}],
                "outside": ["GigabitEthernet0/0"],                          # NAT cloud
                "inside": ["GigabitEthernet0/1", "GigabitEthernet0/3"],
                "overload_intf": "GigabitEthernet0/0",
                "vrf": "control-data",
            },
            "vty_acl": VTY_ACL,
        },
    },
]


def render(env, device_vars):
    text = env.get_template("device.j2").render(**device_vars)
    return [line for line in text.splitlines() if line.strip()]


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
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    all_ok = True
    for dev in DEVICES:
        target = f"{R0_IP}:{dev['port']}"
        config_lines = render(env, dev["vars"])
        try:
            with ConnectHandler(**base_params(dev["port"])) as conn:
                conn.send_config_set(config_lines)
                conn.save_config()
                print(f"[OK]   {dev['name']:<3} ({target}) rendered {len(config_lines)} lines + saved")
                if dev.get("verify"):
                    out = conn.send_command(dev["verify"])
                    for line in out.splitlines():
                        print(f"        {line}")
        except Exception as exc:  # noqa: BLE001 - report each device, keep going
            all_ok = False
            print(f"[FAIL] {dev['name']:<3} ({target}) -> {type(exc).__name__}: {exc}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
