#!/usr/bin/env python3
"""Paramiko Lab.

SSH from this PC to every lab device (R0-R2, S0-S1) using public-key
authentication only -- no password is ever sent. After proving the logins,
the running-config of R0 is pulled and written into the repo (config/).

Requirements
------------
* paramiko 3.5.x  (the lab IOSv/IOSvL2 images only offer the legacy
  diffie-hellman-group14-sha1 KEX and ssh-rsa host keys, which paramiko 5.x
  has dropped -- 3.5.x still supports them).
      pip install "paramiko==3.5.1"
* Private key ~/.ssh/id_rsa_ipa whose public half is installed in each
  device's `ip ssh pubkey-chain` for user "admin".
* A route on this PC to the management subnet, e.g. (run as Administrator):
      route add 172.31.36.0 mask 255.255.255.0 192.168.1.186
"""

import os
import sys
import time
from pathlib import Path

import paramiko

USERNAME = "admin"
KEY_PATH = os.path.expanduser("~/.ssh/id_rsa_ipa")

REPO_DIR = Path(__file__).resolve().parent
CONFIG_DIR = REPO_DIR / "config"

# name -> SSH management IP reachable from this PC
DEVICES = [
    {"name": "R0", "host": "192.168.1.186"},   # edge router, on the LAN
    {"name": "R1", "host": "172.31.36.4"},      # mgmt VLAN behind R0
    {"name": "R2", "host": "172.31.36.5"},
    {"name": "S0", "host": "172.31.36.2"},
    {"name": "S1", "host": "172.31.36.3"},
]

# IOSv / IOSvL2 sign only with ssh-rsa (SHA-1). Disable the rsa-sha2 variants
# so paramiko negotiates the pubkey algorithm the device actually supports.
LEGACY_ALGOS = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}


def connect(host):
    """Open an SSH session using public-key auth only (no password)."""
    key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=USERNAME,
        pkey=key,
        look_for_keys=False,
        allow_agent=False,
        password=None,
        timeout=15,
        disabled_algorithms=LEGACY_ALGOS,
    )
    return client


def run_command(client, command):
    """Run one command over an interactive shell with paging disabled.

    IOS SSH does not give exec_command a reliable non-paged exec, so use a
    shell, turn paging off, send the command and read until the prompt.
    """
    shell = client.invoke_shell()
    time.sleep(1.0)
    _drain(shell)
    shell.send("terminal length 0\n")
    time.sleep(0.8)
    _drain(shell)

    shell.send(command + "\n")
    buf = ""
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(0.4)
        while shell.recv_ready():
            buf += shell.recv(65535).decode(errors="replace")
        # command finished when the device prompt ("<name>#") comes back
        if buf.rstrip().endswith("#") and command.split()[0] in buf:
            break
    shell.close()
    return buf


def _drain(shell):
    while shell.recv_ready():
        shell.recv(65535)


def clean_running_config(raw):
    """Trim shell echo/prompts, keep the config body from 'version' to 'end'."""
    lines = raw.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("version ")), 0)
    end = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip() == "end"), len(lines) - 1)
    return "\n".join(lines[start:end + 1]).strip() + "\n"


def main():
    all_ok = True
    r0_host = None

    print("== Public-key SSH login test (no password) ==")
    for dev in DEVICES:
        try:
            client = connect(dev["host"])
            out = run_command(client, "show running-config | include hostname")
            hostname = next(
                (l.strip() for l in out.splitlines() if l.strip().startswith("hostname")),
                "hostname ?",
            )
            client.close()
            print(f"  [OK]   {dev['name']:<3} {dev['host']:<15} -> {hostname}")
            if dev["name"] == "R0":
                r0_host = dev["host"]
        except Exception as exc:  # noqa: BLE001 - report every device, keep going
            all_ok = False
            print(f"  [FAIL] {dev['name']:<3} {dev['host']:<15} -> {type(exc).__name__}: {exc}")

    print("\n== Save R0 running-config into the repo ==")
    if r0_host is not None:
        # IOSv allows one channel per SSH session, so use a fresh connection.
        client = connect(r0_host)
        raw = run_command(client, "show running-config")
        client.close()
        CONFIG_DIR.mkdir(exist_ok=True)
        out_file = CONFIG_DIR / "R0_running-config.txt"
        out_file.write_text(clean_running_config(raw), encoding="utf-8")
        print(f"  saved -> {out_file.relative_to(REPO_DIR)}")
    else:
        print("  R0 not reachable; running-config not saved")
        all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
