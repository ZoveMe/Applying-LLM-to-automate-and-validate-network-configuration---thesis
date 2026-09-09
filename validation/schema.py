"""Strict schema for structured LLM configuration decisions.

`ConfigSuggestion` describes the original two-router laboratory and must not
change: the 360 preserved runs were validated against exactly this contract,
including the reported count of schema-invalid outputs.

Another laboratory calls `suggestion_model_for()` to obtain the same contract
with its own device names. The device field stays a closed enumeration either
way — that is what makes an unknown device a schema failure rather than
something the gate has to catch later.
"""

from functools import lru_cache
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator


Decision = Literal["PROPOSE", "CLARIFY", "REFUSE"]

ReasonCode = Literal[
    "ACTIONABLE_CHANGE",
    "AMBIGUOUS_REQUIREMENT",
    "POLICY_CONFLICT",
    "OUT_OF_SCOPE",
    "INVALID_INVENTORY",
    "UNSAFE_OPERATION",
]


class StaticRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: Literal["r1", "r2"]
    prefix: str = Field(description="destination network, e.g. 10.0.2.0/24")
    next_hop: str = Field(description="next-hop IP, e.g. 10.0.12.2")


class AccessRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: Literal["r1", "r2"]
    src: str = Field(description="source subnet")
    dst: str = Field(description="destination subnet")
    action: Literal["permit", "deny"]


class ConfigSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(
        min_length=1,
        description="short reasoning for the decision",
    )
    decision: Decision
    reason_code: ReasonCode
    static_routes: List[StaticRoute] = Field(default_factory=list)
    access_policy: List[AccessRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_payload(self):
        has_changes = bool(self.static_routes or self.access_policy)

        if self.decision == "PROPOSE":
            if self.reason_code != "ACTIONABLE_CHANGE":
                raise ValueError(
                    "PROPOSE requires reason_code ACTIONABLE_CHANGE"
                )
            if not has_changes:
                raise ValueError(
                    "PROPOSE must contain at least one configuration change"
                )

        elif self.decision == "CLARIFY":
            if self.reason_code != "AMBIGUOUS_REQUIREMENT":
                raise ValueError(
                    "CLARIFY requires reason_code AMBIGUOUS_REQUIREMENT"
                )
            if has_changes:
                raise ValueError(
                    "CLARIFY cannot contain configuration changes"
                )

        elif self.decision == "REFUSE":
            allowed_refusal_codes = {
                "POLICY_CONFLICT",
                "OUT_OF_SCOPE",
                "INVALID_INVENTORY",
                "UNSAFE_OPERATION",
            }
            if self.reason_code not in allowed_refusal_codes:
                raise ValueError(
                    "REFUSE requires a valid refusal reason_code"
                )
            if has_changes:
                raise ValueError(
                    "REFUSE cannot contain configuration changes"
                )

        return self


@lru_cache(maxsize=None)
def suggestion_model_for(devices: tuple[str, ...]) -> type[BaseModel]:
    """The same contract, with `node` restricted to `devices`.

    Called with ("r1", "r2") it produces a model equivalent to the one above,
    so nothing about the frozen campaign depends on which of the two is used.
    """
    if not devices:
        raise ValueError("at least one device is required")
    node_type = Literal[devices]  # type: ignore[valid-type]

    route = create_model(
        "StaticRoute",
        __config__=ConfigDict(extra="forbid"),
        node=(node_type, ...),
        prefix=(str, Field(description="destination network, e.g. 10.0.2.0/24")),
        next_hop=(str, Field(description="next-hop IP, e.g. 10.0.12.2")),
    )
    rule = create_model(
        "AccessRule",
        __config__=ConfigDict(extra="forbid"),
        node=(node_type, ...),
        src=(str, Field(description="source subnet")),
        dst=(str, Field(description="destination subnet")),
        action=(Literal["permit", "deny"], ...),
    )
    return create_model(
        "ConfigSuggestion",
        __base__=ConfigSuggestion,
        static_routes=(List[route], Field(default_factory=list)),
        access_policy=(List[rule], Field(default_factory=list)),
    )


def devices_in_intent(intent: dict) -> tuple[str, ...]:
    """Router names an intent file mentions, for building the contract."""
    names = set()
    for seg in (intent.get("segments") or {}).values():
        if seg.get("connected_to"):
            names.add(seg["connected_to"])
    for item in intent.get("transits", []):
        names.update(item.get("ips", {}))
    single = intent.get("transit")
    if isinstance(single, dict):
        names.update(k for k in single if k != "subnet")
    for fact in intent.get("config_facts", []):
        if fact.get("node"):
            names.add(fact["node"])
    return tuple(sorted(names))