"""Pydantic data models shared across all modules."""

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


