"""Pydantic models for OpenAI structured outputs.

Every field is required (nullable where optional) so the schemas work with strict structured outputs.
Model output is still untrusted: grounding.py checks it deterministically against the user's sources.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict

Support = Literal["verified", "partial", "unsupported"]
Ownership = Literal["individual", "team", "unclear"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(Strict):
    text: str
    evidence_ids: list[str]
    evidence_quotes: list[str]
    support: Support
    ownership: Ownership


class Skill(Strict):
    name: str
    evidence_ids: list[str]


class ExperienceEntry(Strict):
    employer: str
    title: str
    dates: str
    location: str | None
    source_ids: list[str]
    bullets: list[Claim]


class ResumeDraft(Strict):
    contact_lines: list[str]
    headline: str | None
    summary: list[Claim]
    skills: list[Skill]
    experience: list[ExperienceEntry]
    education: list[str]
    certifications: list[str]
    gaps: list[str]
    notes: list[str]


class SummarySection(Strict):
    summary: list[Claim]


class SkillsSection(Strict):
    skills: list[Skill]


class ExperienceSection(Strict):
    entry: ExperienceEntry


class Requirement(Strict):
    requirement: str
    evidence_ids: list[str]
    match: Literal["strong", "partial", "none"]
    rationale: str


class JobMatch(Strict):
    requirements: list[Requirement]
    ranked_evidence_ids: list[str]


class StarAnswer(Strict):
    question: str
    why_asked: str
    evidence_ids: list[str]
    situation: str
    task: str
    action: str
    result: str
    spoken_version: str
    follow_up_questions: list[str]
    missing_evidence: list[str]
    claims: list[Claim]


class InterviewPack(Strict):
    answers: list[StarAnswer]
    evidence_gaps: list[str]


class ClaimVerdict(Strict):
    claim_id: str
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reason: str


class EvidenceCheck(Strict):
    verdicts: list[ClaimVerdict]
