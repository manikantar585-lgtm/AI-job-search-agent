"""The job-search agent.

With an LLM key, the model plans and calls tools itself (OpenAI-style function
calling) in a loop. Without one, a deterministic pipeline runs the same tools
in a fixed order. Both return {"summary", "steps", "shortlist"}, so the UI can
show what the agent did, step by step.
"""
import json
import re
from datetime import date, timedelta

from . import llm, matcher, sources, writer
from .models import Profile
from .store import Store

MAX_STEPS = 8

TOOLS = [
    {"type": "function", "function": {
        "name": "search_jobs",
        "description": "Search job boards and return the best matches for the candidate, ranked by fit score.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Short role/skill keywords, e.g. 'genai engineer' or 'machine learning intern'"},
            "top": {"type": "integer", "description": "How many results to return (default 8)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_job_details",
        "description": "Full description and fit analysis (matched/missing skills) for one job.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}}},
    {"type": "function", "function": {
        "name": "draft_cover_letter",
        "description": "Write a tailored cover letter for a job using only facts from the resume.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}}},
    {"type": "function", "function": {
        "name": "save_to_tracker",
        "description": "Save a job to the candidate's application tracker.",
        "parameters": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "notes": {"type": "string"}}, "required": ["job_id"]}}},
    {"type": "function", "function": {
        "name": "skill_gaps",
        "description": "Which missing skills appear most across the best-fitting jobs from the last search.",
        "parameters": {"type": "object", "properties": {}}}},
]


class JobAgent:
    def __init__(self, store: Store, source_names: list[str] | None = None, today: date | None = None):
        self.store = store
        self.source_names = source_names
        self.today = today
        self.last_ranked: list[dict] = []
        self.shortlist: dict[str, dict] = {}

    # ------------------------------------------------------------- tools
    @property
    def profile(self) -> Profile:
        return self.store.get_profile()

    def search_jobs(self, query: str, top: int = 8) -> list[dict]:
        jobs = sources.search_all(query, self.source_names)
        self.store.cache_jobs(jobs)
        ranked = matcher.rank(jobs, self.profile, self.today)
        known = {r["job"]["id"] for r in self.last_ranked}
        self.last_ranked += [r for r in ranked if r["job"]["id"] not in known]
        self.last_ranked.sort(key=lambda r: -r["match"]["score"])
        return [{"job_id": r["job"]["id"], "title": r["job"]["title"], "company": r["job"]["company"],
                 "location": r["job"]["location"], "score": r["match"]["score"], "verdict": r["match"]["verdict"]}
                for r in ranked[:top]]

    def get_job_details(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if not job:
            return {"error": "unknown job_id"}
        m = matcher.score_job(job, self.profile, self.today)
        return {"job_id": job_id, "title": job.title, "company": job.company, "location": job.location,
                "salary": job.salary, "url": job.url, "description": job.description[:1500], "match": m,
                "resume_tips": writer.resume_tips(m)}

    def draft_cover_letter(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if not job:
            return {"error": "unknown job_id"}
        m = matcher.score_job(job, self.profile, self.today)
        letter = writer.cover_letter(job, self.profile, m)
        self.shortlist.setdefault(job_id, {"job_id": job_id, "title": job.title, "company": job.company,
                                           "score": m["score"], "url": job.url})["cover_letter"] = letter
        return {"job_id": job_id, "cover_letter": letter, "recruiter_message": writer.recruiter_message(job, self.profile, m)}

    def save_to_tracker(self, job_id: str, notes: str = "") -> dict:
        job = self.store.get_job(job_id)
        if not job:
            return {"error": "unknown job_id"}
        m = matcher.score_job(job, self.profile, self.today)
        letter = self.shortlist.get(job_id, {}).get("cover_letter")
        follow = ((self.today or date.today()) + timedelta(days=7)).isoformat()
        self.store.upsert_application(job_id, "saved", m["score"], notes or None, follow, letter)
        self.shortlist.setdefault(job_id, {"job_id": job_id, "title": job.title, "company": job.company,
                                           "score": m["score"], "url": job.url})
        return {"saved": job_id, "score": m["score"]}

    def skill_gaps(self) -> list[dict]:
        return matcher.skill_gap_report(self.last_ranked)[:8]

    def _call(self, name: str, args: dict):
        fn = {t["function"]["name"]: getattr(self, t["function"]["name"]) for t in TOOLS}.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}

    # ------------------------------------------------------------- run
    def run(self, goal: str) -> dict:
        if not self.profile.skills:
            return {"summary": "Please upload your resume or add your skills first.", "steps": [], "shortlist": []}
        result = self._run_llm(goal)
        return result if result else self._run_pipeline(goal)

    def _system_prompt(self) -> str:
        p = self.profile
        return (
            "You are a job-search agent for an early-career candidate in India. Use the tools to find the best-fitting "
            "jobs for the goal, inspect the most promising ones, draft cover letters for the top 2-3, save them to the "
            "tracker, and check skill gaps. Prefer realistic matches (score >= 55) over prestige. Do not invent jobs. "
            "Finish with a short summary: the shortlist with scores and one-line reasons, and the top 2 skills to learn.\n"
            f"Candidate: {p.name or 'unknown'}; skills: {', '.join(p.skills)}; target roles: {', '.join(p.target_roles) or 'any'}; "
            f"experience: {p.years_experience:g} years; locations: {', '.join(p.locations) or 'any'}; remote ok: {p.remote_ok}."
        )

    def _run_llm(self, goal: str) -> dict | None:
        msgs = [{"role": "system", "content": self._system_prompt()}, {"role": "user", "content": goal}]
        steps = []
        for _ in range(MAX_STEPS):
            msg = llm.raw_chat(msgs, tools=TOOLS)
            if msg is None:
                return None if not steps else self._finish("Stopped early (LLM error).", steps)
            calls = msg.get("tool_calls") or []
            if not calls:
                return self._finish(msg.get("content") or "", steps)
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = self._call(name, args)
                steps.append({"tool": name, "args": args, "result": _brief(out)})
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "content": json.dumps(out)[:6000]})
        return self._finish("Reached the step limit; here is what I found so far.", steps)

    def _run_pipeline(self, goal: str) -> dict:
        """Offline plan: search each target role -> inspect top 3 -> letters -> save -> gaps."""
        steps = []
        queries = _queries_from_goal(goal, self.profile)
        for q in queries:
            out = self.search_jobs(q, top=5)
            steps.append({"tool": "search_jobs", "args": {"query": q}, "result": _brief(out)})
        top = [r for r in self.last_ranked if r["match"]["score"] >= 55][:3] or self.last_ranked[:3]
        for r in top:
            jid = r["job"]["id"]
            for name in ("get_job_details", "draft_cover_letter", "save_to_tracker"):
                out = self._call(name, {"job_id": jid})
                steps.append({"tool": name, "args": {"job_id": jid}, "result": _brief(out)})
        gaps = self.skill_gaps()
        steps.append({"tool": "skill_gaps", "args": {}, "result": _brief(gaps)})
        nq = len(queries)
        lines = [f"Searched {nq} {'query' if nq == 1 else 'queries'} and ranked {len(self.last_ranked)} jobs. "
                 f"Shortlisted {len(top)}:"]
        for r in top:
            m = r["match"]
            why = ", ".join(m["matched_skills"][:4]) or "role fit"
            lines.append(f"- {r['job']['title']} at {r['job']['company']}: {m['score']}/100 ({m['verdict']}). Matches {why}.")
        if gaps:
            lines.append("Skills to learn next: " + ", ".join(g["skill"] for g in gaps[:2]) + ".")
        return self._finish("\n".join(lines), steps)

    def _finish(self, summary: str, steps: list) -> dict:
        return {"summary": summary.strip(), "steps": steps, "shortlist": list(self.shortlist.values())}


def _queries_from_goal(goal: str, p: Profile) -> list[str]:
    g = goal.lower()
    found = [r for r in p.target_roles if r.lower() in g]
    m = re.search(r"(?:find|search|look for|get)\s+(?:me\s+)?(?:some\s+)?(.+?)(?:\s+(?:jobs?|roles?|internships?|in|near|at)\b|$)", g)
    queries = []
    if m and len(m.group(1).split()) <= 5:
        queries.append(m.group(1).strip(" ."))
    if "intern" in g:
        queries.append("intern")
    queries += [r.lower() for r in (found or p.target_roles[:2])]
    out = []
    for q in queries:
        if q and q not in out:
            out.append(q)
    return out[:3] or ["python"]


def _brief(out) -> str:
    """Short, human-readable trace line for the UI."""
    if isinstance(out, list):
        if out and isinstance(out[0], dict) and "title" in out[0]:
            return f"{len(out)} results; top: " + "; ".join(f"{o['title']} @ {o['company']} ({o['score']})" for o in out[:3])
        if out and isinstance(out[0], dict) and "skill" in out[0]:
            return "Gaps: " + ", ".join(f"{o['skill']} ({o['jobs']})" for o in out[:5])
        return f"{len(out)} items"
    if isinstance(out, dict):
        if "error" in out:
            return "Error: " + out["error"]
        if "cover_letter" in out:
            return f"Drafted cover letter ({len(out['cover_letter'].split())} words)"
        if "match" in out:
            return f"{out['title']}: {out['match']['score']}/100, missing {', '.join(out['match']['missing_skills'][:4]) or 'nothing'}"
        if "saved" in out:
            return f"Saved to tracker (score {out['score']})"
    return str(out)[:200]
