"""Cover letters and recruiter messages (LLM if configured, template otherwise)."""
import re

from .llm import chat
from .models import Job, Profile


def _highlights(resume: str, skills: list[str], n: int = 3) -> list[str]:
    """Pick resume bullet lines that mention the job's matched skills."""
    lines = [re.sub(r"^[\s•\-*–]+", "", ln).strip() for ln in resume.splitlines()]
    lines = [ln for ln in lines if 40 <= len(ln) <= 220 and ln.count(",") < 5]  # skip bare skill lists
    scored = []
    for ln in lines:
        hits = sum(1 for s in skills if s.lower() in ln.lower())
        if hits:
            scored.append((hits, ln))
    return [ln.rstrip(".") for _, ln in sorted(scored, key=lambda x: -x[0])[:n]]


def cover_letter(job: Job, p: Profile, match: dict) -> str:
    matched = match.get("matched_skills", [])
    llm = chat([
        {"role": "system", "content": (
            "Write a concise, specific cover letter (max 200 words) for the candidate. Use only facts from the "
            "resume; never invent experience, numbers or employers. Mention 2-3 concrete projects or experiences "
            "that match the job. Warm, confident, no clichés like 'I am writing to express'. Plain text.")},
        {"role": "user", "content": f"JOB: {job.title} at {job.company} ({job.location})\n{job.description[:3000]}\n\n"
                                    f"MATCHED SKILLS: {', '.join(matched)}\n\nRESUME:\n{p.resume_text[:5000]}"},
    ], max_tokens=500)
    if llm:
        return llm
    name = p.name or "[Your Name]"
    top = ", ".join(matched[:4]) or "the core skills in this role"
    bullets = _highlights(p.resume_text, matched)
    body = "\n".join(f"- {b}" for b in bullets) or "- [Add one project that matches this role]"
    if p.years_experience >= 1:
        exp = f"I bring {p.years_experience:g} years of hands-on experience"
    elif p.years_experience > 0:
        exp = "Through my internship and projects, I have worked hands-on"
    else:
        exp = "Through my projects, I have worked hands-on"
    return (
        f"Dear Hiring Team at {job.company},\n\n"
        f"I'd like to apply for the {job.title} role. {exp} with {top}, which line up closely with what you "
        f"are looking for.\n\nA few things I've done that are relevant:\n{body}\n\n"
        f"I'd welcome the chance to show how I can contribute to {job.company}. Thank you for your time.\n\n"
        f"Best regards,\n{name}"
    )


def recruiter_message(job: Job, p: Profile, match: dict) -> str:
    skills = ", ".join(match.get("matched_skills", [])[:3]) or "relevant projects"
    first = (p.name or "").split()[0] if p.name else "[Name]"
    msg = (f"Hi! I'm {first}, and I came across the {job.title} role at {job.company}. I've worked with {skills} "
           f"and would love to be considered. Could I share my resume with you?")
    return msg[:300]  # fits a LinkedIn connection note


def resume_tips(match: dict) -> list[str]:
    tips = []
    if match.get("missing_skills"):
        tips.append("If you have used any of these, add them to your resume in the job's exact words: "
                    + ", ".join(match["missing_skills"][:6]) + ".")
    if match.get("matched_skills"):
        tips.append("Move bullets that mention " + ", ".join(match["matched_skills"][:3]) + " to the top of each section.")
    for n in match.get("notes", []):
        if n.startswith("Asks for"):
            tips.append(n + ": lead with projects and internships to show equivalent experience.")
    return tips
