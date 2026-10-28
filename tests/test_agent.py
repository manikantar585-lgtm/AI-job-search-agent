import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent as agent_mod
from app import cli, config, llm, sources
from app.agent import JobAgent
from app.main import create_app
from app.matcher import score_job
from app.models import Job, Profile
from app.resume import parse_resume, years_of_experience
from app.skills import extract_skills, required_years
from app.store import Store

TODAY = date(2026, 10, 28)
RESUME = (Path(__file__).parent.parent / "data" / "sample_resume.txt").read_text()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "SOURCES", ["sample"])


@pytest.fixture
def profile():
    p = parse_resume(RESUME, today=TODAY)
    p.locations = ["Hyderabad"]
    return p


def job(**kw):
    base = dict(id="t:1", title="ML Engineer", company="X", location="Hyderabad", url="u",
                description="", source="t", posted_at="2026-10-27")
    base.update(kw)
    return Job(**base)


# ---------------- skills / resume

def test_skill_extraction_handles_tricky_names():
    s = extract_skills("C++, C#, Node.js and CI/CD; RAG with FAISS; GitHub Actions; React.js")
    assert {"C++", "C#", "Node.js", "CI/CD", "RAG", "Vector Databases", "React", "Git"} <= s


def test_skill_extraction_avoids_false_positives():
    assert extract_skills("Send your CV. Let us rest. Go to the node. Express interest. R&D team.") == set()


def test_required_years():
    assert required_years("3+ years of experience") == 3
    assert required_years("2-4 yrs") == 2
    assert required_years("no experience needed") is None


def test_parse_resume(profile):
    assert profile.name == "Arjun Sharma"
    assert {"Python", "PyTorch", "LangChain", "RAG", "React"} <= set(profile.skills)
    assert "GenAI Engineer" in profile.target_roles
    assert profile.years_experience == pytest.approx(0.3, abs=0.05)  # Jul-Oct 2026 internship


def test_experience_present_and_multiple_ranges():
    text = "EXPERIENCE\nAnalyst, A  Jan 2020 - Dec 2021\nEngineer, B  Jan 2022 - Present\nEDUCATION\nB.Tech 2015 - 2019"
    assert years_of_experience(text, today=date(2026, 12, 1)) == pytest.approx(7.0, abs=0.1)


# ---------------- matching

def test_matching_rewards_skills_and_penalises_seniority(profile):
    fresher = job(title="GenAI Engineer", description="LLM, RAG, LangChain, Python, FastAPI. 0-1 years.")
    senior = job(title="Senior Data Scientist", description="Spark, XGBoost, statistics. 6+ years of experience.")
    a, b = score_job(fresher, profile, TODAY), score_job(senior, profile, TODAY)
    assert a["score"] >= 85 and b["score"] < 50
    assert "Asks for 6+ years" in " ".join(b["notes"])
    assert {"Spark", "XGBoost"} <= set(b["missing_skills"])


def test_location_preference(profile):
    profile.remote_ok = False
    near = score_job(job(location="Hyderabad"), profile, TODAY)["breakdown"]["location"]
    far = score_job(job(location="Berlin"), profile, TODAY)["breakdown"]["location"]
    assert near == 1.0 and far < 0.5


# ---------------- live sources (HTTP mocked)

class FakeResp:
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


def test_remotive_and_arbeitnow_parsing(monkeypatch):
    def fake_get(url, params=None, timeout=None, headers=None):
        if "remotive" in url:
            return FakeResp({"jobs": [{"id": 7, "url": "https://r/7", "title": "LLM Engineer", "company_name": "Acme",
                                       "tags": ["llm"], "job_type": "full_time", "publication_date": "2026-10-20T10:00:00",
                                       "candidate_required_location": "India", "salary": "",
                                       "description": "<p>Build <b>RAG</b> apps</p><ul><li>Python</li></ul>"}]})
        return FakeResp({"data": [
            {"slug": "ml-dev-1", "company_name": "Beta", "title": "ML Developer", "description": "<p>PyTorch models</p>",
             "remote": True, "url": "https://a/1", "tags": ["ml"], "job_types": ["full time"], "location": "Berlin",
             "created_at": 1792000000},
            {"slug": "chef", "company_name": "Cafe", "title": "Chef", "description": "Cooking", "remote": False,
             "url": "https://a/2", "tags": [], "job_types": [], "location": "Munich", "created_at": 1792000000}]})
    monkeypatch.setattr(sources.httpx, "get", fake_get)
    r = sources.remotive("llm")
    assert r[0].id == "remotive:7" and r[0].remote and "RAG apps" in r[0].description and "<" not in r[0].description
    a = sources.arbeitnow("ml")
    assert [j.title for j in a] == ["ML Developer"] and a[0].posted_at.startswith("2026")


def test_dead_source_does_not_break_search(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(sources.httpx, "get", boom)
    jobs = sources.search_all("genai", ["remotive", "arbeitnow", "sample"])
    assert jobs and all(j.source == "sample" for j in jobs)


# ---------------- agent

def _agent(profile):
    s = Store(":memory:")
    s.save_profile(profile)
    return s, JobAgent(s, ["sample"], today=TODAY)


def test_offline_pipeline(profile):
    store, a = _agent(profile)
    r = a.run("Find me GenAI engineer jobs in Hyderabad or remote")
    tools = [s["tool"] for s in r["steps"]]
    assert tools[0] == "search_jobs" and "draft_cover_letter" in tools and tools[-1] == "skill_gaps"
    assert len(r["shortlist"]) == 3 and r["shortlist"][0]["title"].startswith("GenAI Engineer")
    assert all("Dear Hiring Team" in x["cover_letter"] for x in r["shortlist"])
    assert len(store.list_applications()) == 3


def test_llm_tool_calling_loop(profile, monkeypatch):
    """The model asks for tools; the agent executes them and feeds results back."""
    script = [
        {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "search_jobs", "arguments": json.dumps({"query": "nlp"})}}]},
        {"content": "", "tool_calls": [{"id": "c2", "function": {"name": "save_to_tracker", "arguments": json.dumps({"job_id": "sample:11", "notes": "Telugu NLP!"})}}]},
        {"content": "Saved the Bhasha Tech NLP role for you."},
    ]
    calls = []

    def fake_raw_chat(messages, tools=None, max_tokens=800):
        calls.append(messages)
        return script[len(calls) - 1]

    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(agent_mod.llm, "raw_chat", fake_raw_chat)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: None)
    store, a = _agent(profile)
    r = a.run("find NLP roles")
    assert r["summary"] == "Saved the Bhasha Tech NLP role for you."
    assert [s["tool"] for s in r["steps"]] == ["search_jobs", "save_to_tracker"]
    assert calls[1][-1]["role"] == "tool" and "sample:11" in calls[1][-1]["content"]
    assert store.get_application("sample:11")["notes"] == "Telugu NLP!"


def test_agent_needs_profile():
    a = JobAgent(Store(":memory:"), ["sample"])
    assert "upload your resume" in a.run("find jobs")["summary"]


# ---------------- API + CLI

def test_api_end_to_end(tmp_path):
    c = TestClient(create_app(str(tmp_path / "t.db")))
    assert c.post("/api/search", json={"query": "python"}).status_code == 400  # no profile yet
    p = c.post("/api/profile/resume", files={"file": ("cv.txt", RESUME.encode())}).json()
    assert "PyTorch" in p["skills"]
    p["locations"] = ["Hyderabad"]
    assert c.put("/api/profile", json=p).status_code == 200
    r = c.post("/api/search", json={"query": "genai"}).json()
    assert r["count"] > 0 and r["results"][0]["match"]["score"] >= r["results"][-1]["match"]["score"]
    jid = r["results"][0]["job"]["id"]
    assert "Dear Hiring Team" in c.post(f"/api/jobs/{jid}/cover-letter").json()["cover_letter"]
    assert c.post("/api/tracker", json={"job_id": jid, "status": "applied", "follow_up": "2026-01-01"}).status_code == 200
    t = c.get("/api/tracker").json()
    assert t["items"][0]["status"] == "applied" and jid in t["due_follow_ups"]
    assert c.post("/api/tracker", json={"job_id": jid, "status": "dreaming"}).status_code == 400
    assert c.post("/api/agent", json={"goal": "find machine learning intern roles"}).json()["shortlist"]
    assert c.get("/").status_code == 200


def test_cli_digest(tmp_path):
    out = tmp_path / "d.md"
    cli.main(["digest", "--resume", str(Path(__file__).parent.parent / "data" / "sample_resume.txt"),
              "--sources", "sample", "--min-score", "50", "--out", str(out)])
    text = out.read_text()
    assert text.startswith("# Job digest for Arjun Sharma") and "/100" in text
