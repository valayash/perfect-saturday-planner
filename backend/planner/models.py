"""Shared data shapes for the planner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------- request ----------


class Clarification(BaseModel):
    question: str
    answer: str


class PlanRequest(BaseModel):
    """What the browser sends. Either the structured fields or free_text (or both)."""

    city: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=80)
    budget: float | None = Field(default=None, ge=0, le=10_000_000)
    available_time: str | None = Field(default=None, max_length=60)
    start_time: str | None = Field(default=None, max_length=10)
    mood: str | None = Field(default=None, max_length=200)
    interests: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    free_text: str | None = Field(default=None, max_length=1500)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=4)
    run_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")  # lets the browser re-fetch a run


# ---------- parsed preferences ----------


Energy = Literal["low", "medium", "high"]


class Preferences(BaseModel):
    """Normalized preferences produced by parse_preferences (LLM or heuristic)."""

    city: str | None = Field(description="City name in English, e.g. 'Bengaluru'. null if unknown.")
    country_code: str | None = Field(description="ISO-3166 alpha-2 country code guess, e.g. 'IN'. null if unknown.")
    area: str | None = Field(description="Neighbourhood to stay around, if the user named one, else null.")
    budget: float = Field(description="Total budget per person for the day in local currency. Use 1500 if not given.")
    currency: str = Field(description="ISO currency code, e.g. 'INR'.")
    available_minutes: int = Field(description="How long the outing can last, in minutes.")
    start_time: str = Field(description="24h 'HH:MM' start time on Saturday.")
    mood: str = Field(description="The user's mood in a few words.")
    energy: Energy = Field(description="Energy level implied by the mood.")
    interests: list[str] = Field(description="Interests, lower-case, e.g. ['food','music','walks'].")
    hard_constraints: list[str] = Field(description="Non-negotiable constraints, e.g. ['vegetarian','wheelchair accessible'].")
    soft_constraints: list[str] = Field(description="Preferences that can be traded off, e.g. ['avoid crowded places'].")
    assumptions: list[str] = Field(description="Short notes on anything you assumed or defaulted.")
    needs_clarification: bool = Field(description="True only if the input is too vague to plan (e.g. no city).")
    clarifying_questions: list[str] = Field(description="At most 2 short questions when needs_clarification is true, else [].")


# ---------- places ----------


class Place(BaseModel):
    id: str
    name: str
    category: str
    kind: Literal["activity", "food"]
    lat: float
    lon: float
    distance_km: float = 0.0
    indoor: bool = False
    est_cost: float = 0.0
    crowd: Literal["low", "medium", "high"] = "medium"
    opening_hours: str | None = None
    cuisine: str | None = None
    veg: Literal["yes", "only", "unverified", "no"] | None = None
    website: str | None = None
    address: str | None = None
    source: Literal["openstreetmap", "curated"] = "openstreetmap"
    note: str | None = None

    def brief(self) -> dict:
        """Compact view handed to the model (keeps tool results small)."""
        d = {
            "place_id": self.id,
            "name": self.name,
            "category": self.category,
            "distance_km": round(self.distance_km, 1),
            "indoor": self.indoor,
            "est_cost_per_person": round(self.est_cost),
            "crowd_estimate": self.crowd,
        }
        for key in ("address", "opening_hours", "cuisine", "veg", "note"):
            val = getattr(self, key)
            if val:
                d[key] = val
        return d


class Location(BaseModel):
    name: str
    lat: float
    lon: float
    country_code: str
    timezone: str
    label: str
