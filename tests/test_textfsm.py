"""TDD tests for textfsmlab.py.

Covers the pure description-generation logic and the ntc-templates/TextFSM
parsing of a captured `show cdp neighbors detail`, checked against the actual
lab topology (see Topology-Actual note).
"""

import os
import sys

import pytest

# import textfsmlab from the repo root (this file lives in tests/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import textfsmlab  # noqa: E402

from ntc_templates.parse import parse_output  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "r2_cdp_detail.txt")


# --- helpers -------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("GigabitEthernet0/1", "G0/1"),
    ("Gig 0/1", "G0/1"),
    ("Gi0/1", "G0/1"),
    ("GigabitEthernet2/0", "G2/0"),
])
def test_normalize_port(raw, expected):
    assert textfsmlab.normalize_port(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("R2.ipa.lab", "R2"),
    ("S1.ipa.lab", "S1"),
    ("R1", "R1"),
])
def test_short_name(raw, expected):
    assert textfsmlab.short_name(raw) == expected


# --- ntc-templates / TextFSM parsing ------------------------------------------

def _r2_cdp_rows():
    with open(FIXTURE, encoding="utf-8") as f:
        return parse_output(platform="cisco_ios", command="show cdp neighbors detail", data=f.read())


def test_cdp_parsing_yields_three_neighbors():
    rows = _r2_cdp_rows()
    assert len(rows) == 3
    locals_ = {r["local_interface"] for r in rows}
    assert locals_ == {"GigabitEthernet0/2", "GigabitEthernet0/1", "GigabitEthernet0/3"}


def test_cdp_local_map():
    mapping = textfsmlab.cdp_local_map(_r2_cdp_rows())
    assert mapping["GigabitEthernet0/1"] == ("R1", "G0/1")
    assert mapping["GigabitEthernet0/3"] == ("S1", "G0/3")


# --- description rules ---------------------------------------------------------

def test_build_description_cisco_neighbor():
    cdp = {"GigabitEthernet0/1": ("R2", "G0/1")}
    assert textfsmlab.build_description("GigabitEthernet0/1", cdp, set()) == "Connect to G0/1 of R2"


def test_build_description_pc():
    assert textfsmlab.build_description("GigabitEthernet0/2", {}, set()) == "Connect to PC"


def test_build_description_wan_wins_over_cdp():
    dhcp = {"GigabitEthernet0/0"}
    cdp = {"GigabitEthernet0/0": ("X", "G9/9")}
    assert textfsmlab.build_description("GigabitEthernet0/0", cdp, dhcp) == "Connect to WAN"


# --- running-config parsing ----------------------------------------------------

def test_parse_control_data_and_dhcp_router():
    cfg = (
        "interface GigabitEthernet0/0\n vrf forwarding control-data\n ip address dhcp\n"
        "interface GigabitEthernet0/1\n vrf forwarding control-data\n ip address 10.1.12.2 255.255.255.252\n"
        "interface GigabitEthernet0/2\n vrf forwarding management\n"
        "interface GigabitEthernet0/3\n vrf forwarding control-data\n"
    )
    cd, dhcp = textfsmlab.parse_control_data_and_dhcp(cfg, switch=False)
    assert cd == ["GigabitEthernet0/0", "GigabitEthernet0/1", "GigabitEthernet0/3"]
    assert dhcp == {"GigabitEthernet0/0"}


def test_parse_control_data_and_dhcp_switch():
    cfg = (
        "interface GigabitEthernet0/0\n switchport access vlan 101\n"
        "interface GigabitEthernet0/2\n switchport access vlan 99\n"
        "interface GigabitEthernet0/3\n switchport access vlan 101\n"
    )
    cd, dhcp = textfsmlab.parse_control_data_and_dhcp(cfg, switch=True)
    assert cd == ["GigabitEthernet0/0", "GigabitEthernet0/3"]
    assert dhcp == set()


# --- end-to-end against the real topology (R2) --------------------------------

def test_descriptions_for_device_r2_matches_topology():
    rows = _r2_cdp_rows()
    control_data = ["GigabitEthernet0/0", "GigabitEthernet0/1", "GigabitEthernet0/3"]
    dhcp = {"GigabitEthernet0/0"}
    descs = textfsmlab.descriptions_for_device(control_data, rows, dhcp)
    assert descs == {
        "GigabitEthernet0/0": "Connect to WAN",
        "GigabitEthernet0/1": "Connect to G0/1 of R1",
        "GigabitEthernet0/3": "Connect to G0/3 of S1",
    }
