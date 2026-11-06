"""Deterministic grounding: source IDs, untrusted-input fencing, claim checks, approved output, retrieval.

These checks confirm that what the model wrote is traceable to the user's own saved sources (cited IDs
exist, quotes and numbers appear in them, team work is not claimed as individual work, employer/title/
dates match the master resume). They cannot confirm that the sources themselves are true.
"""
import math
import re
from dataclasses import dataclass, field

UNTRUSTED_TAGS = ("job_description", "interview_question", "master_resume", "verified_evidence",
                  "candidate_context", "current_draft", "instruction", "claims_to_check")
OWNERSHIP_VERBS = ("led", "owned", "spearheaded", "drove", "built", "created", "designed", "launched",
                   "founded", "architected", "directed", "headed", "managed", "delivered", "developed")
SPOKEN_WORDS_MIN, SPOKEN_WORDS_MAX = 150, 225  # about 60-90 seconds at ~150 words per minute
STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or our that the their this to
we with you your will can into about over under using use used able ability experience experienced work
working role team teams strong skills skill years year including across within such other more""".split())

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")


# --- sources ----------------------------------------------------------------------------------------

def normalize(text):
    text = (text or "").translate(_DASHES).translate(_QUOTES).lower()
    return re.sub(r"\s+", " ", text).strip()


def fence(text):
    """Neutralise anything in untrusted text that looks like one of our delimiter tags."""
    pattern = r"<(\s*/?\s*(?:%s)\b)" % "|".join(UNTRUSTED_TAGS)
    return re.sub(pattern, r"‹\1", text or "", flags=re.IGNORECASE)


def resume_sources(resume_text):
    """Each non-empty resume line becomes a citable source R1, R2, ..."""
    lines = [line.strip() for line in (resume_text or "").splitlines() if line.strip()]
    return {f"R{i}": line for i, line in enumerate(lines, 1)}


def evidence_text(record):
    header = " | ".join(str(record.get(k) or "").strip() for k in ("title", "employer", "role", "period")
                        if str(record.get(k) or "").strip())
    parts = [header, str(record.get("details") or "").strip(), str(record.get("outcome") or "").strip()]
    return "\n".join(p for p in parts if p)


def evidence_sources(records):
    """Only records marked verified are used as evidence."""
    return {r["id"]: evidence_text(r) for r in records if r.get("verified") and r.get("id")}


def ownership_map(records):
    return {r["id"]: (r.get("ownership") or "Unspecified").lower() for r in records if r.get("id")}


def render_sources(sources):
    return "\n".join(f"[{sid}] {fence(text)}" for sid, text in sources.items()) or "(none)"


def block(tag, body):
    return f"<{tag}>\n{body}\n</{tag}>"


# --- claim checks -----------------------------------------------------------------------------------

def numbers_in(text):
    return {m.group(0).replace(",", "").replace("$", "").rstrip(".") for m in _NUMBER.finditer(text or "")}


@dataclass
class ClaimCheck:
    claim_id: str
    text: str
    evidence_ids: list
    issues: list = field(default_factory=list)
    self_check: str = ""

    @property
    def approved(self):
        return not self.issues


def check_claim(claim_id, claim, sources, owners, self_check_verdicts=None):
    issues = []
    ids = [i.strip() for i in claim.evidence_ids if i.strip()]
    unknown = [i for i in ids if i not in sources]
    known = [i for i in ids if i in sources]
    cited_text = normalize(" ".join(sources[i] for i in known))
    if not ids:
        issues.append("No evidence cited")
    if unknown:
        issues.append(f"Cites unknown source(s): {', '.join(unknown)}")
    for quote in claim.evidence_quotes:
        if quote.strip() and normalize(quote) not in cited_text:
            issues.append("Quoted evidence not found in the cited sources")
            break
    missing_numbers = sorted(numbers_in(claim.text) - numbers_in(" ".join(sources[i] for i in known)))
    if missing_numbers:
        issues.append(f"Number(s) not in cited evidence: {', '.join(missing_numbers)}")
    team_cited = [i for i in known if owners.get(i) == "team"]
    first_word = normalize(claim.text).split(" ", 1)[0] if claim.text.strip() else ""
    if team_cited and (claim.ownership == "individual" or
                       (first_word in OWNERSHIP_VERBS and all(owners.get(i) == "team" for i in known))):
        issues.append("Claims personal ownership of work recorded as a team outcome")
    if claim.support != "verified":
        issues.append(f"Model labelled this claim '{claim.support}'")
    verdict = (self_check_verdicts or {}).get(claim_id)
    if verdict and verdict[0] != "supported":
        issues.append(f"AI self-check: {verdict[0].replace('_', ' ')} ({verdict[1]})")
    return ClaimCheck(claim_id, claim.text, ids, issues, verdict[0] if verdict else "")


def _in_resume(value, resume_norm):
    return bool(value and value.strip()) and normalize(value) in resume_norm


@dataclass
class EntryCheck:
    index: int
    label: str
    issues: list
    bullets: list


@dataclass
class ResumeReview:
    contact_lines: list
    headline: str
    summary: list
    skills: list            # (name, approved)
    entries: list           # EntryCheck
    education: list         # (text, approved)
    certifications: list
    warnings: list
    gaps: list

    def all_claims(self):
        claims = list(self.summary)
        for entry in self.entries:
            claims.extend(entry.bullets)
        return claims

    @property
    def excluded(self):
        excluded = [c for c in self.all_claims() if not c.approved]
        excluded += [ClaimCheck(f"X{e.index + 1}", e.label, [], e.issues) for e in self.entries if e.issues]
        return excluded


def claim_id_map(draft):
    """Stable IDs for every claim in a draft: S1.., X1.B1.."""
    ids = {f"S{i}": c for i, c in enumerate(draft.summary, 1)}
    for x, entry in enumerate(draft.experience, 1):
        ids.update({f"X{x}.B{b}": c for b, c in enumerate(entry.bullets, 1)})
    return ids


def review_resume(draft, resume_text, records, self_check_verdicts=None):
    sources = {**resume_sources(resume_text), **evidence_sources(records)}
    owners = ownership_map(records)
    resume_norm = normalize(resume_text)
    all_norm = normalize(" ".join(sources.values()))
    summary = [check_claim(f"S{i}", c, sources, owners, self_check_verdicts) for i, c in enumerate(draft.summary, 1)]
    skills = [(s.name, bool(s.name.strip()) and normalize(s.name) in all_norm) for s in draft.skills]
    entries, positions = [], []
    for x, entry in enumerate(draft.experience, 1):
        issues = [f"{name} '{value}' does not match the master resume"
                  for name, value in (("Employer", entry.employer), ("Title", entry.title), ("Dates", entry.dates))
                  if not _in_resume(value, resume_norm)]
        if _in_resume(entry.employer, resume_norm):
            positions.append(resume_norm.index(normalize(entry.employer)))
        bullets = [check_claim(f"X{x}.B{b}", c, sources, owners, self_check_verdicts)
                   for b, c in enumerate(entry.bullets, 1)]
        entries.append(EntryCheck(x - 1, f"{entry.title} | {entry.employer} | {entry.dates}", issues, bullets))
    warnings = []
    if positions != sorted(positions):
        warnings.append("Role order differs from the master resume. Check the chronology.")
    headline = draft.headline or ""
    if headline and not _in_resume(headline, resume_norm):
        warnings.append(f"Headline '{headline}' is not in the master resume, so it was left out of the approved resume.")
        headline = ""
    return ResumeReview(
        contact_lines=[line for line in draft.contact_lines if _in_resume(line, resume_norm)],
        headline=headline,
        summary=summary, skills=skills, entries=entries,
        education=[(t, _in_resume(t, resume_norm)) for t in draft.education],
        certifications=[(t, _in_resume(t, resume_norm)) for t in draft.certifications],
        warnings=warnings, gaps=list(draft.gaps),
    )


def approved_resume_text(draft, review):
    """ATS-friendly plain text containing only approved content."""
    out = list(review.contact_lines)
    if review.headline:
        out.append(review.headline)
    summary = [c.text for c in review.summary if c.approved]
    if summary:
        out += ["", "PROFESSIONAL SUMMARY", " ".join(summary)]
    skills = [name for name, ok in review.skills if ok]
    if skills:
        out += ["", "SKILLS", ", ".join(skills)]
    roles = []
    for entry, check in zip(draft.experience, review.entries):
        if check.issues:
            continue
        header = " | ".join(p for p in (entry.title, entry.employer, entry.location or "", entry.dates) if p)
        roles += ["", header] + [f"- {b.text}" for b in check.bullets if b.approved]
    if roles:
        out += ["", "PROFESSIONAL EXPERIENCE"] + roles[1:]
    for heading, items in (("EDUCATION", review.education), ("CERTIFICATIONS", review.certifications)):
        approved = [t for t, ok in items if ok]
        if approved:
            out += ["", heading] + approved
    return "\n".join(out).strip()


# --- interview checks -------------------------------------------------------------------------------

@dataclass
class AnswerReview:
    question: str
    claims: list
    issues: list
    spoken_words: int


def review_interview(pack, records, resume_text, self_check_verdicts=None):
    sources = {**resume_sources(resume_text), **evidence_sources(records)}
    owners = ownership_map(records)
    reviews = []
    for a, answer in enumerate(pack.answers, 1):
        claims = [check_claim(f"A{a}.C{c}", claim, sources, owners, self_check_verdicts)
                  for c, claim in enumerate(answer.claims, 1)]
        issues = [f"{c.claim_id}: {issue}" for c in claims for issue in c.issues]
        unknown = [i for i in answer.evidence_ids if i not in sources]
        if not answer.evidence_ids or unknown:
            issues.append("No valid evidence cited for this answer" if not answer.evidence_ids
                          else f"Cites unknown source(s): {', '.join(unknown)}")
        star_text = " ".join((answer.situation, answer.task, answer.action, answer.result, answer.spoken_version))
        cited = " ".join(sources.get(i, "") for i in answer.evidence_ids)
        missing_numbers = sorted(numbers_in(star_text) - numbers_in(cited))
        if missing_numbers:
            issues.append(f"Number(s) in the answer not in cited evidence: {', '.join(missing_numbers)}")
        issues += [f"Missing evidence: {m}" for m in answer.missing_evidence]
        words = len(answer.spoken_version.split())
        if not SPOKEN_WORDS_MIN <= words <= SPOKEN_WORDS_MAX:
            issues.append(f"Spoken version is {words} words (target {SPOKEN_WORDS_MIN}-{SPOKEN_WORDS_MAX} "
                          "for 60-90 seconds)")
        reviews.append(AnswerReview(answer.question, claims, issues, words))
    return reviews


def interview_text(pack, reviews):
    out = []
    for i, (answer, review) in enumerate(zip(pack.answers, reviews), 1):
        out += [f"Q{i}. {answer.question}", f"Why they ask: {answer.why_asked}",
                f"Evidence: {', '.join(answer.evidence_ids) or 'none'}",
                f"Situation: {answer.situation}", f"Task: {answer.task}", f"Action: {answer.action}",
                f"Result: {answer.result}", "", "60-90 second spoken version:", answer.spoken_version, "",
                "Follow-up questions:"] + [f"- {q}" for q in answer.follow_up_questions]
        if review.issues:
            out += ["", "NEEDS REVIEW BEFORE USE:"] + [f"- {issue}" for issue in review.issues]
        out.append("")
    if pack.evidence_gaps:
        out += ["Evidence gaps to prepare:"] + [f"- {g}" for g in pack.evidence_gaps]
    return "\n".join(out).strip()


# --- local retrieval (free, no API call) ------------------------------------------------------------

def _terms(text):
    return [t for t in re.findall(r"[a-z][a-z0-9+#.-]{2,}", normalize(text)) if t not in STOPWORDS]


def rank_evidence(query, records, limit=8):
    """Rank verified evidence records by TF-IDF-style keyword overlap with the query."""
    candidates = [r for r in records if r.get("verified") and r.get("id")]
    if not candidates:
        return []
    docs = {r["id"]: set(_terms(evidence_text(r))) for r in candidates}
    df = {}
    for terms in docs.values():
        for t in terms:
            df[t] = df.get(t, 0) + 1
    query_terms = set(_terms(query))
    n = len(docs)
    scores = {rid: sum(math.log(1 + n / df[t]) for t in query_terms & terms) for rid, terms in docs.items()}
    ranked = sorted(candidates, key=lambda r: (-scores[r["id"]], r["id"]))
    return [(r, round(scores[r["id"]], 2)) for r in ranked[:limit]]
