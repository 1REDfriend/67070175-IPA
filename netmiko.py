#!/usr/bin/env python3
"""Lab Netmiko.

Configure the control/data plane and management access-control on R1, R2 and
S1 with netmiko:

  * S1  - VLAN 101 for the control/data plane (ports to Linux2 and to R2)
  * R1  - OSPF (VRF control-data) for 10.1.1.0/24 and the R1-R2 link
  * R2  - OSPF (VRF control-data), advertise a default route into OSPF, and
          PAT (overload) out of the NAT cloud on G0/0
  * R1/R2/S1 - restrict VTY (telnet/ssh) to the management network and the
          Lab306 access network only

All device logins use SSH *public-key* auth (password auth is disabled on the
devices). The four inner devices are reached through R0's NAT port-forwards on
192.168.1.186. The config sets are idempotent - re-running is safe.

Requires: pip install netmiko   (netmiko pulls paramiko 3.5.x, which still
supports the legacy diffie-hellman-group14-sha1 KEX these IOSv images offer).
"""

import os
import sys

# This file is named netmiko.py (as the lab requires), which would otherwise
# shadow the installed netmiko package. Drop this script's own directory from
# the import path so `import netmiko` resolves to the real library.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p not in ("", ".", _here) and os.path.abspath(p or ".") != _here]

from netmiko import ConnectHandler  # noqa: E402

KEY_FILE = os.path.expanduser("~/.ssh/id_rsa_ipa")
R0_IP = "192.168.1.186"

# IOSv signs only with ssh-rsa (SHA-1); disable rsa-sha2 so paramiko negotiates
# the algorithm the device supports.
LEGACY_ALGOS = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}

# Networks allowed to reach the VTY lines: the management plane and the access
# network we manage from ("Lab306" equivalent in this topology).
MGMT_NET = ("172.31.36.0", "0.0.0.255")
LAB306_NET = ("192.168.1.0", "0.0.0.255")

VTY_ACL = [
    "ip access-list standard MGMT_ACCESS",
    f" permit {MGMT_NET[0]} {MGMT_NET[1]}",
    f" permit {LAB306_NET[0]} {LAB306_NET[1]}",
    "exit",
    "line vty 0 4",
    " access-class MGMT_ACCESS in",
    "line vty 5 15",
    " access-class MGMT_ACCESS in",
]

DEVICES = [
    {
        "name": "S1",
        "port": 2203,
        "config": [
            # VLAN 101 = control/data plane
            "vlan 101",
            "name CONTROL_DATA",
            "exit",
            "interface GigabitEthernet0/0",   # -> Linux2
            " switchport mode access",
            " switchport access vlan 101",
            "exit",
            "interface GigabitEthernet0/3",   # -> R2
            " switchport trunk encapsulation dot1q",
            " switchport mode access",
            " switchport access vlan 101",
            "exit",
        ] + VTY_ACL,
    },
    {
        "name": "R1",
        "port": 2204,
        "config": [
            "router ospf 1 vrf control-data",
            " network 10.1.1.0 0.0.0.255 area 0",    # -> Linux1
            " network 10.1.12.0 0.0.0.3 area 0",     # R1-R2 link
            "exit",
        ] + VTY_ACL,
    },
    {
        "name": "R2",
        "port": 2205,
        "config": [
            "router ospf 1 vrf control-data",
            " network 10.1.2.0 0.0.0.255 area 0",    # -> S1/Linux2
            " network 10.1.12.0 0.0.0.3 area 0",     # R1-R2 link
            " passive-interface GigabitEthernet0/3",
            " default-information originate",         # advertise default into OSPF
            "exit",
            # PAT / overload out of the NAT cloud (G0/0)
            "ip access-list standard NAT_INSIDE",
            " permit 10.1.0.0 0.0.255.255",
            "exit",
            "interface GigabitEthernet0/0",           # NAT cloud (WAN)
            " ip nat outside",
            "exit",
            "interface GigabitEthernet0/1",           # -> R1
            " ip nat inside",
            "exit",
            "interface GigabitEthernet0/3",           # -> S1 (10.1.2.0/24)
            " ip nat inside",
            "exit",
            "ip nat inside source list NAT_INSIDE interface GigabitEthernet0/0 vrf control-data overload",
        ] + VTY_ACL,
    },
]

# Post-config verification command per device.
VERIFY = {
    "S1": "show vlan brief | include 101",
    "R1": "show ip ospf neighbor",
    "R2": "show ip ospf neighbor",
}


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
    for dev in DEVICES:
        target = f"{R0_IP}:{dev['port']}"
        try:
            with ConnectHandler(**base_params(dev["port"])) as conn:
                conn.send_config_set(dev["config"])
                conn.save_config()
                print(f"[OK]   {dev['name']:<3} ({target}) configured + saved")
                verify = VERIFY.get(dev["name"])
                if verify:
                    out = conn.send_command(verify)
                    for line in out.splitlines():
                        print(f"        {line}")
        except Exception as exc:  # noqa: BLE001 - report each device, keep going
            all_ok = False
            print(f"[FAIL] {dev['name']:<3} ({target}) -> {type(exc).__name__}: {exc}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
