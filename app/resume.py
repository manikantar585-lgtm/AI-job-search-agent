"""Parse a resume (PDF/TXT/MD) into a Profile."""
import io
import re
from datetime import date

from .models import Profile
from .skills import extract_skills

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(\+?\d[\d\s-]{8,14}\d)")
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
RANGE_RE = re.compile(
    rf"(?:({MONTHS})[a-z]*\.?\s+)?(20\d\d|19\d\d)\s*(?:-|–|to)\s*(?:(?:({MONTHS})[a-z]*\.?\s+)?(20\d\d|19\d\d)|(present|current|now|till date))",
    re.I)
SECTION_RE = re.compile(r"^\s*(experience|work experience|professional experience|internships?|employment)\s*:?\s*$", re.I | re.M)
NEXT_SECTION_RE = re.compile(r"^\s*(education|projects?|skills|technical skills|certifications?|achievements|awards)\s*:?\s*$", re.I | re.M)

ROLE_HINTS = {
    "GenAI Engineer": ["LLM", "RAG", "Generative AI", "LangChain", "AI Agents"],
    "Machine Learning Engineer": ["Machine Learning", "Deep Learning", "PyTorch", "TensorFlow", "Scikit-learn"],
    "Data Scientist": ["Statistics", "Machine Learning", "Pandas", "XGBoost"],
    "Data Analyst": ["Data Analysis", "Power BI", "Tableau", "Excel", "SQL"],
    "Computer Vision Engineer": ["Computer Vision", "CNN", "Transformers"],
    "NLP Engineer": ["NLP", "Transformers", "Hugging Face"],
    "Full Stack Developer": ["React", "Node.js", "REST APIs"],
    "Python Developer": ["Python", "FastAPI", "Django", "Flask"],
}


def read_resume(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="replace")


_MONTH_N = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _months(mon: str | None) -> int:
    return _MONTH_N.get((mon or "jan")[:3].lower(), 1)


def years_of_experience(text: str, today: date | None = None) -> float:
    """Sum of date ranges inside the Experience section (internships count)."""
    today = today or date.today()
    m = SECTION_RE.search(text)
    if not m:
        return 0.0
    section = text[m.end():]
    nxt = NEXT_SECTION_RE.search(section)
    section = section[: nxt.start()] if nxt else section
    total = 0
    for r in RANGE_RE.finditer(section):
        start = int(r.group(2)) * 12 + _months(r.group(1))
        if r.group(5):
            end = today.year * 12 + today.month
        else:
            end = int(r.group(4)) * 12 + _months(r.group(3) or "dec")
        total += max(0, end - start + 1)
    return round(total / 12, 1)


def suggest_roles(skills: set[str]) -> list[str]:
    scored = sorted(((sum(s in skills for s in hints), role) for role, hints in ROLE_HINTS.items()), reverse=True)
    return [role for score, role in scored if score >= 2][:3]


def parse_resume(text: str, today: date | None = None) -> Profile:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = ""
    for ln in lines[:5]:
        if not EMAIL_RE.search(ln) and not PHONE_RE.search(ln) and 1 < len(ln.split()) <= 5 and not any(c.isdigit() for c in ln):
            name = ln.title() if ln.isupper() else ln
            break
    skills = extract_skills(text)
    return Profile(name=name, skills=sorted(skills), target_roles=suggest_roles(skills),
                   years_experience=years_of_experience(text, today), resume_text=text)
