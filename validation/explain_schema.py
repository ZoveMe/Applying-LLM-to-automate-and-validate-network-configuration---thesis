"""Strict schema for structured LLM configuration explanations.

Covers the second assistive role from the thesis description:
"објаснување на постоечки конфигурации" — the model receives raw device
configuration and must return a structured, checkable explanation.

The explanation is UNTRUSTED. Every claimed fact is verified afterwards
against intent/intended_state.yaml by experiments/evaluate_explain.py.
"""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExplainedInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="interface name as written in the config, e.g. eth1")
    ip_address: str = Field(description="address with prefix length, e.g. 10.0.1.1/24")
    role: str = Field(
        min_length=1,
        description="short purpose, e.g. 'gateway for the client segment'",
    )


class ExplainedRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(description="destination network, e.g. 10.0.2.0/24")
    next_hop: str = Field(description="next-hop IP, e.g. 10.0.12.2")
    purpose: str = Field(min_length=1, description="why this route exists")


class ExplainedAccessRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str = Field(description="source subnet")
    dst: str = Field(description="destination subnet")
    action: Literal["permit", "deny"]
    purpose: str = Field(min_length=1, description="why this rule exists")


class ConfigExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: Literal["r1", "r2", "r3"]
    summary: str = Field(
        min_length=1,
        description="2-3 sentence summary of the device's role in the network",
    )
    interfaces: List[ExplainedInterface] = Field(default_factory=list)
    static_routes: List[ExplainedRoute] = Field(default_factory=list)
    access_rules: List[ExplainedAccessRule] = Field(default_factory=list)
