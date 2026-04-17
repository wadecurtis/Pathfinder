"""Pydantic data models shared across all modules."""

from typing import Literal

from pydantic import BaseModel


class JobListing(BaseModel):
    id: str = ""
    title: str
    company: str
    location: str = ""
    url: str = ""
    description: str = ""
    date_posted: str = ""
    source: str = ""
    salary: str = ""


class ScoringResult(BaseModel):
    decision: Literal["YES", "MAYBE", "NO"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str
    top_qualifier: str
    disqualifier: str
    evidence: str
    hypothesis_category: str = ""
    hypothesis_why: str = ""
    hypothesis_value: str = ""
