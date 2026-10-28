"""FastAPI app: profile, search, agent, tracker."""
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, matcher, sources, writer
from .agent import JobAgent
from .models import Profile
from .resume import parse_resume, read_resume
from .store import STATUSES, Store

STATIC = Path(__file__).resolve().parent.parent / "static"


class ProfileIn(BaseModel):
    name: str = ""
    skills: list[str] = []
    target_roles: list[str] = []
    years_experience: float = Field(0, ge=0, le=50)
    locations: list[str] = []
    remote_ok: bool = True
    job_types: list[str] = []


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    sources: list[str] | None = None
    min_score: int = 0


class AgentIn(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    sources: list[str] | None = None


class TrackIn(BaseModel):
    job_id: str
    status: str = "saved"
    notes: str | None = None
    follow_up: date | None = None


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="AI Job Search Agent", version="1.0.0")
    store = Store(db_path)
    app.state.store = store

    def job_or_404(job_id: str):
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found (search first)")
        return job

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"ok": True, "llm": bool(config.LLM_API_KEY), "sources": config.SOURCES}

    # profile
    @app.get("/api/profile")
    def get_profile():
        return store.get_profile().to_dict()

    @app.put("/api/profile")
    def put_profile(body: ProfileIn):
        old = store.get_profile()
        p = Profile(**body.model_dump(), resume_text=old.resume_text)
        return store.save_profile(p).to_dict()

    @app.post("/api/profile/resume")
    async def upload_resume(file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(413, "Resume too large (max 5 MB)")
        try:
            text = read_resume(file.filename or "resume.txt", data)
        except Exception:
            raise HTTPException(400, "Could not read that file. Upload a PDF or TXT.")
        if len(text.strip()) < 50:
            raise HTTPException(400, "No text found in the resume (scanned PDF?).")
        p = parse_resume(text)
        old = store.get_profile()
        p.locations, p.remote_ok, p.job_types = old.locations, old.remote_ok, old.job_types
        return store.save_profile(p).to_dict()

    # search
    @app.post("/api/search")
    def search(body: SearchIn):
        p = store.get_profile()
        if not p.skills:
            raise HTTPException(400, "Add your resume or skills first.")
        jobs = sources.search_all(body.query, body.sources)
        store.cache_jobs(jobs)
        ranked = [r for r in matcher.rank(jobs, p) if r["match"]["score"] >= body.min_score]
        tracked = {a["job_id"]: a["status"] for a in store.list_applications()}
        for r in ranked:
            r["tracked"] = tracked.get(r["job"]["id"])
        return {"count": len(ranked), "results": ranked, "skill_gaps": matcher.skill_gap_report(ranked)[:8]}

    @app.get("/api/jobs/{job_id:path}/analysis")
    def analysis(job_id: str):
        job = job_or_404(job_id)
        m = matcher.score_job(job, store.get_profile())
        return {"job": job.to_dict(), "match": m, "resume_tips": writer.resume_tips(m)}

    @app.post("/api/jobs/{job_id:path}/cover-letter")
    def cover(job_id: str):
        job = job_or_404(job_id)
        p = store.get_profile()
        m = matcher.score_job(job, p)
        return {"cover_letter": writer.cover_letter(job, p, m), "recruiter_message": writer.recruiter_message(job, p, m)}

    # agent
    @app.post("/api/agent")
    def run_agent(body: AgentIn):
        return JobAgent(store, body.sources).run(body.goal)

    # tracker
    @app.get("/api/tracker")
    def tracker():
        return {"statuses": STATUSES, "items": store.list_applications(),
                "due_follow_ups": [a["job_id"] for a in store.due_follow_ups(date.today().isoformat())]}

    @app.post("/api/tracker")
    def track(body: TrackIn):
        job = job_or_404(body.job_id)
        score = matcher.score_job(job, store.get_profile())["score"]
        try:
            return store.upsert_application(body.job_id, body.status, score, body.notes,
                                            body.follow_up.isoformat() if body.follow_up else None)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/tracker/{job_id:path}")
    def untrack(job_id: str):
        if not store.delete_application(job_id):
            raise HTTPException(404, "Not in tracker")
        return {"ok": True}

    return app


app = create_app()
