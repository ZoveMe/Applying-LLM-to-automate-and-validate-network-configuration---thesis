"""Strict schema for structured LLM configuration decisions."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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