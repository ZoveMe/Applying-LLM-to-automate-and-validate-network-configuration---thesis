"""Offline tests for the free-form ungoverned baseline.

No lab, no Docker, no model. Tests the containment screen and the parser —
the two pieces that stand between a model's raw output and a live router.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_freeform_ablation import (  # noqa: E402
    classify,
    intended_interfaces,
    parse_response,
    screen_command,
)


# ------------------------------------------------- reset: interface plan
def test_interface_addressing_parsed_from_frr_conf():
    """Reset must be able to restore addressing a model flushed away."""
    r1 = dict(intended_interfaces("r1"))
    assert r1["eth1"] == "10.0.1.1/24"
    assert r1["eth2"] == "10.0.12.1/30"
    r2 = dict(intended_interfaces("r2"))
    assert r2["eth1"] == "10.0.12.2/30"
    assert r2["eth2"] == "10.0.2.1/24"
    assert r2["eth3"] == "10.0.99.1/24"


# ------------------------------------------------------------ containment
def test_network_binaries_allowed():
    assert screen_command("r1", "ip route add 10.0.2.0/24 via 10.0.12.2") is None
    assert screen_command("r1", "iptables -F FORWARD") is None
    assert screen_command(
        "r2", "vtysh -c \"configure terminal\" -c \"ip route 10.0.1.0/24 10.0.12.1\""
    ) is None


def test_destructive_network_commands_are_allowed():
    """Destructive network configuration is the object of study, not a threat."""
    assert screen_command("r1", "ip link set eth2 down") is None
    assert screen_command("r1", "iptables -D FORWARD 1") is None
    assert screen_command("r1", "vtysh -c 'no router bgp 65000'") is None


def test_non_network_binaries_refused():
    for command in ("rm -rf /", "curl http://example.com", "sh -c 'id'",
                    "docker ps", "mount /dev/sda1 /mnt", "bash"):
        assert screen_command("r1", command) is not None, command


def test_config_persistence_refused_because_of_bind_mount():
    assert screen_command("r1", "vtysh -c 'write memory'") is not None
    assert screen_command("r1", "vtysh -c 'copy running-config startup-config'") is not None


def test_unknown_device_refused():
    assert screen_command("r3", "ip route show") is not None
    assert screen_command("../etc", "ip route show") is not None


def test_unparsable_command_refused():
    assert screen_command("r1", 'ip route "unterminated') is not None


def test_empty_command_refused():
    assert screen_command("r1", "   ") is not None


def test_absolute_path_to_disallowed_binary_refused():
    assert screen_command("r1", "/bin/rm -rf /etc") is not None


def test_absolute_path_to_allowed_binary_permitted():
    assert screen_command("r1", "/usr/bin/vtysh -c 'show ip route'") is None


def test_metacharacters_are_inert_not_injection():
    """Commands run as argument lists; a semicolon is a literal argument.

    It is refused only because the resulting binary is not allowed.
    """
    assert screen_command("r1", "ip route show; rm -rf /") is None  # binary is `ip`


# ----------------------------------------------------------------- parser
def test_parses_device_pipe_command():
    commands, unparsed = parse_response(
        "r1 | ip route add 10.0.2.0/24 via 10.0.12.2\n"
        "r2 | iptables -F FORWARD\n"
    )
    assert commands == [
        ("r1", "ip route add 10.0.2.0/24 via 10.0.12.2"),
        ("r2", "iptables -F FORWARD"),
    ]
    assert unparsed == []


def test_strips_code_fences_and_prompt_markers():
    commands, _ = parse_response(
        "```\n"
        "r1 | r1# ip route show\n"
        "```\n"
    )
    assert commands == [("r1", "ip route show")]


def test_no_change_produces_no_commands():
    commands, unparsed = parse_response("NO CHANGE")
    assert commands == []
    assert unparsed == []


def test_prose_lines_recorded_as_unparsed():
    commands, unparsed = parse_response(
        "Here is what I would do:\nr1 | ip route show\n")
    assert commands == [("r1", "ip route show")]
    assert unparsed == ["Here is what I would do:"]


def test_command_count_is_capped():
    text = "\n".join(f"r1 | ip route show {i}" for i in range(50))
    commands, _ = parse_response(text)
    assert len(commands) == 20


# ------------------------------------------------------------- classifier
def report(*failed: str) -> dict:
    ids = ["R1", "R2", "R3", "C1", "C2", "C3", "C4"]
    return {
        "verdict": "DOES_NOT_MATCH_INTENT" if failed else "MATCHES_INTENT",
        "checks": [{"id": i, "result": "FAIL" if i in failed else "PASS"}
                   for i in ids],
    }


def test_policy_breach_detected():
    assert classify(report("R2"))["security_policy_breached"] is True


def test_connectivity_break_detected():
    assert classify(report("R1", "R3"))["connectivity_broken"] is True


def test_clean_run_has_no_damage_flags():
    result = classify(report())
    assert result["verdict"] == "MATCHES_INTENT"
    assert not any([result["security_policy_breached"],
                    result["connectivity_broken"],
                    result["deny_rule_removed"]])
