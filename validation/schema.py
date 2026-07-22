"""schema.py — Pydantic model of a structured configuration suggestion.

This is the contract between the (untrusted) LLM and the rest of the system.
The SAME file is imported by:
  - validation/static_gate.py  (Week 3: gate demo with example suggestions)
  - the Ollama client          (Week 4: format=ConfigSuggestion.model_json_schema())

Design choices (cite in Методологија):
  * extra='forbid'  -> unknown/hallucinated fields are REJECTED, not ignored.
  * flat structure  -> small local models degrade on deeply nested schemas.
  * rationale FIRST -> reasoning-before-answer preserves quality under
                       constrained decoding (Tam et al., 2024).
"""
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class StaticRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: Literal["r1", "r2"]
    prefix: str = Field(description="destination network, e.g. 10.0.2.0/24")
    next_hop: str = Field(description="next-hop IP address, e.g. 10.0.12.2")


class AccessRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: Literal["r1", "r2"]
    src: str = Field(description="source subnet, e.g. 10.0.1.0/24")
    dst: str = Field(description="destination subnet, e.g. 10.0.99.0/24")
    action: Literal["permit", "deny"]


class ConfigSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(
        min_length=1,
        description="short reasoning for the proposed change (comes FIRST)",
    )
    static_routes: List[StaticRoute] = []
    access_policy: List[AccessRule] = []
