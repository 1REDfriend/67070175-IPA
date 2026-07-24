#!/usr/bin/env python3
"""Lab TextFSM / NTC-Template.

Auto-generate interface descriptions for the control/data-plane interfaces of
R1, R2 and S1 from `show cdp neighbors detail`, parsed with TextFSM via
ntc-templates.

Rules (see lab):
  * Cisco neighbor  -> "Connect to <remote-port> of <remote-device>"
                       e.g. R1 G0/1 -> "Connect to G0/1 of R2"
  * DHCP interface  -> "Connect to WAN"   (the NAT-cloud uplink)
  * no CDP neighbor -> "Connect to PC"

The pure helpers below are covered by tests/test_textfsm.py (TDD). netmiko is
imported for the live push only; the sibling netmiko.py is kept off the import
path so `import netmiko` resolves to the installed library.

Requires: pip install netmiko textfsm ntc-templates
"""

import os
import re
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p not in ("", ".", _here) and os.path.abspath(p or ".") != _here]

KEY_FILE = os.path.expanduser("~/.ssh/id_rsa_ipa")
R0_IP = "192.168.1.186"
LEGACY_ALGOS = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}

DEVICES = [
    {"name": "R1", "port": 2204, "switch": False},
    {"name": "R2", "port": 2205, "switch": False},
    {"name": "S1", "port": 2203, "switch": True},
]


# --- pure helpers (unit-tested) ------------------------------------------------

def normalize_port(port):
    """'GigabitEthernet0/1' / 'Gig 0/1' / 'Gi0/1' -> 'G0/1'."""
    m = re.match(r"\s*([A-Za-z])[A-Za-z]*\s*([\d/]+)", port)
    return f"{m.group(1).upper()}{m.group(2)}" if m else port.strip()


def short_name(device):
    """'R2.ipa.lab' -> 'R2'."""
    return device.split(".")[0].strip()


def cdp_local_map(cdp_rows):
    """{local_interface: (short_device, normalized_remote_port)} from ntc rows."""
    result = {}
    for row in cdp_rows:
        local = row.get("local_interface", "").strip()
        dev = row.get("neighbor_name", "").strip()
        rport = row.get("neighbor_interface", "").strip()
        if local and dev:
            result[local] = (short_name(dev), normalize_port(rport))
    return result


def parse_control_data_and_dhcp(running_config, switch=False):
    """Return (control_data_interfaces, dhcp_interfaces) from a running-config."""
    blocks = re.split(r"(?m)^interface ", running_config)
    control_data, dhcp = [], set()
    for block in blocks[1:]:
        name = block.splitlines()[0].strip()
        is_cd = ("switchport access vlan 101" in block) if switch else ("vrf forwarding control-data" in block)
        if is_cd:
            control_data.append(name)
        if re.search(r"(?m)^\s*ip address dhcp\b", block):
            dhcp.add(name)
    return control_data, dhcp


def build_description(local_intf, cdp_by_local, dhcp_intfs):
    if local_intf in dhcp_intfs:
        return "Connect to WAN"
    if local_intf in cdp_by_local:
        dev, port = cdp_by_local[local_intf]
        return f"Connect to {port} of {dev}"
    return "Connect to PC"


def descriptions_for_device(control_data_intfs, cdp_rows, dhcp_intfs):
    """{interface: description} for every control/data-plane interface."""
    cdp_by_local = cdp_local_map(cdp_rows)
    return {intf: build_description(intf, cdp_by_local, dhcp_intfs) for intf in control_data_intfs}


# --- live push -----------------------------------------------------------------

def _base_params(port):
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
    from netmiko import ConnectHandler  # local import so tests need no netmiko

    all_ok = True
    for dev in DEVICES:
        try:
            with ConnectHandler(**_base_params(dev["port"])) as conn:
                cdp_rows = conn.send_command("show cdp neighbors detail", use_textfsm=True)
                running = conn.send_command("show running-config")
                cd_intfs, dhcp = parse_control_data_and_dhcp(running, switch=dev["switch"])
                descs = descriptions_for_device(cd_intfs, cdp_rows, dhcp)

                cfg = []
                for intf, desc in descs.items():
                    cfg += [f"interface {intf}", f" description {desc}"]
                if cfg:
                    conn.send_config_set(cfg)
                    conn.save_config()
                print(f"[OK]   {dev['name']}:")
                for intf, desc in descs.items():
                    print(f"        {intf:<22} description: {desc}")
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            print(f"[FAIL] {dev['name']} -> {type(exc).__name__}: {exc}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
