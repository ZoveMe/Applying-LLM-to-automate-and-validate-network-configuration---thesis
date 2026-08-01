"""Tests for the conversational reply parser used by the console/dashboard.

The model must be able to answer anything — greetings, questions, refusals —
and only sometimes propose commands. These tests pin that behaviour.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from live_freeform_ablation import parse_console_response  # noqa: E402


def test_prose_only_reply_is_all_answer():
    answer, commands = parse_console_response(
        "Hello. I can help you configure r1 and r2. What would you like to change?")
    assert "Hello" in answer
    assert commands == []


def test_marker_splits_answer_from_commands():
    answer, commands = parse_console_response(
        "I will add the return route on r2.\n"
        "COMMANDS\n"
        'r2 | vtysh -c "configure terminal" -c "ip route 10.0.1.0/24 10.0.12.1"\n'
    )
    assert answer == "I will add the return route on r2."
    assert commands == [
        ("r2", 'vtysh -c "configure terminal" -c "ip route 10.0.1.0/24 10.0.12.1"')
    ]


def test_marker_with_colon_accepted():
    _, commands = parse_console_response(
        "Adding a rule.\nCOMMANDS:\nr1 | iptables -F FORWARD\n")
    assert commands == [("r1", "iptables -F FORWARD")]


def test_commands_without_marker_still_found():
    """Models drift from the format; the command lines must still be picked up."""
    answer, commands = parse_console_response(
        "This will open the path.\n"
        "r1 | iptables -D FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP\n")
    assert answer == "This will open the path."
    assert len(commands) == 1
    assert commands[0][0] == "r1"


def test_clarifying_question_yields_no_commands():
    answer, commands = parse_console_response(
        "That request is ambiguous. Do you mean faster throughput or lower "
        "latency, and between which two segments?")
    assert "ambiguous" in answer
    assert commands == []


def test_answer_preserved_when_model_emits_only_commands():
    answer, commands = parse_console_response("r1 | ip route show\n")
    assert answer == ""
    assert commands == [("r1", "ip route show")]


def test_multiline_answer_preserved():
    answer, commands = parse_console_response(
        "First I check the route.\nThen I add the rule.\n"
        "COMMANDS\nr1 | ip route show\n")
    assert answer == "First I check the route.\nThen I add the rule."
    assert len(commands) == 1
