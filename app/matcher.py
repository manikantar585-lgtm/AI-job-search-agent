"""Explainable job-fit scoring (0-100)."""
import re
from datetime import date

from .models import Job, Profile
from .skills import SKILLS, extract_skills, group_of, required_years

WEIGHTS = {"skills": 0.45, "title": 0.20, "experience": 0.15, "location": 0.12, "recency": 0.08}
SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head|manager|director|architect)\b", re.I)
JUNIOR_RE = re.compile(r"\b(intern|internship|fresher|junior|jr\.?|graduate|trainee|entry[- ]level|0-1|0-2)\b", re.I)
TITLE_SYNONYMS = {
    "genai": {"generative", "llm", "ai", "gen"}, "llm": {"genai", "generative", "ai"}, "ml": {"machine", "learning"},
    "ai": {"genai", "llm", "ml"}, "nlp": {"language"}, "developer": {"engineer"}, "engineer": {"developer"},
    "analyst": {"analytics"}, "scientist": {"science"},
}
TITLE_STOP = {"and", "the", "of", "a", "-", "/", "&", "(", ")", "years", "fresher", "0-1", "months", "6"}


def _title_tokens(s: str) -> set[str]:
    toks = {t for t in re.findall(r"[a-z0-9+#]+", s.lower()) if t not in TITLE_STOP}
    if "machine" in toks and "learning" in toks:
        toks.add("ml")
    if "generative" in toks:
        toks.add("genai")
    return toks


def title_score(job_title: str, targets: list[str]) -> float:
    jt = _title_tokens(job_title)
    best = 0.0
    for target in targets:
        tt = _title_tokens(target)
        if not tt:
            continue
        hit = 0.0
        for t in tt:
            if t in jt:
                hit += 1
            elif TITLE_SYNONYMS.get(t, set()) & jt:
                hit += 0.7
        best = max(best, hit / len(tt))
    return min(best, 1.0) if targets else 0.5


def experience_score(job: Job, years: float) -> tuple[float, str | None]:
    text = f"{job.title}\n{job.description}"
    req = required_years(text)
    if JUNIOR_RE.search(job.title) or job.job_type == "internship":
        return 1.0, None
    if req is None:
        if SENIOR_RE.search(job.title) and years < 4:
            return 0.2, "Senior-level title"
        return 0.8, None
    gap = req - years
    if gap <= 0:
        return 1.0, None
    note = f"Asks for {req}+ years (you have {years:g})"
    if gap <= 1:
        return 0.65, note
    if gap <= 2:
        return 0.35, note
    return 0.0, note


def location_score(job: Job, p: Profile) -> float:
    if job.remote and p.remote_ok:
        return 1.0
    if not p.locations:
        return 1.0
    loc = job.location.lower()
    if any(l.lower() in loc for l in p.locations):
        return 1.0
    return 0.6 if "india" in loc and any(l for l in p.locations) else 0.25


def recency_score(job: Job, today: date | None = None) -> float:
    try:
        age = ((today or date.today()) - date.fromisoformat(job.posted_at[:10])).days
    except (ValueError, TypeError):
        return 0.6
    return 1.0 if age <= 7 else 0.8 if age <= 14 else 0.6 if age <= 30 else 0.3


def score_job(job: Job, p: Profile, today: date | None = None) -> dict:
    job_skills = extract_skills(f"{job.title}\n{' '.join(job.tags)}\n{job.description}")
    mine = set(p.skills)
    matched = sorted(job_skills & mine)
    missing = sorted(job_skills - mine)
    if job_skills:
        my_groups = {group_of(s) for s in mine}
        partial = sum(0.3 for s in missing if group_of(s) in my_groups)  # related experience counts a bit
        skills = min(1.0, (len(matched) + partial) / len(job_skills))
    else:
        skills = 0.4
    exp, exp_note = experience_score(job, p.years_experience)
    parts = {
        "skills": skills,
        "title": title_score(job.title, p.target_roles),
        "experience": exp,
        "location": location_score(job, p),
        "recency": recency_score(job, today),
    }
    if p.job_types and job.job_type and not any(t.lower() in job.job_type.lower() for t in p.job_types):
        parts["title"] *= 0.5
    total = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()))
    notes = [n for n in [exp_note] if n]
    if missing:
        notes.append("Missing: " + ", ".join(missing[:5]))
    return {
        "score": total,
        "breakdown": {k: round(v, 2) for k, v in parts.items()},
        "matched_skills": matched,
        "missing_skills": missing,
        "notes": notes,
        "verdict": "Strong match" if total >= 75 else "Good match" if total >= 60 else "Stretch" if total >= 45 else "Weak match",
    }


def rank(jobs: list[Job], p: Profile, today: date | None = None) -> list[dict]:
    out = [{"job": j.to_dict(), "match": score_job(j, p, today)} for j in jobs]
    return sorted(out, key=lambda r: -r["match"]["score"])


def skill_gap_report(ranked: list[dict], top: int = 15) -> list[dict]:
    """Which missing skills show up most in your best-fitting jobs -> what to learn next."""
    counts: dict[str, int] = {}
    for r in ranked[:top]:
        for s in r["match"]["missing_skills"]:
            counts[s] = counts.get(s, 0) + 1
    return [{"skill": s, "jobs": n, "group": SKILLS[s]["group"]} for s, n in sorted(counts.items(), key=lambda x: -x[1])]
