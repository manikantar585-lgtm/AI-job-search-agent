"""Job sources. Each returns a list[Job]; failures return [] so one dead API
never breaks a search."""
import html
import json
import re

import httpx

from .. import config
from ..models import Job


def strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text or "", flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _get(url: str, params: dict | None = None) -> dict | None:
    try:
        r = httpx.get(url, params=params, timeout=config.HTTP_TIMEOUT,
                      headers={"User-Agent": "ai-job-search-agent/1.0 (+github)"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def remotive(query: str, limit: int = 50) -> list[Job]:
    """Remote jobs worldwide. https://remotive.com/api/remote-jobs (no key)."""
    data = _get("https://remotive.com/api/remote-jobs", {"search": query, "limit": limit})
    jobs = []
    for j in (data or {}).get("jobs", [])[:limit]:
        jobs.append(Job(
            id=f"remotive:{j.get('id')}", title=j.get("title", ""), company=j.get("company_name", ""),
            location=j.get("candidate_required_location") or "Remote", url=j.get("url", ""),
            description=strip_html(j.get("description", "")), source="remotive", remote=True,
            tags=list(j.get("tags") or []), salary=j.get("salary") or "",
            posted_at=(j.get("publication_date") or "")[:10], job_type=j.get("job_type") or ""))
    return jobs


def arbeitnow(query: str, limit: int = 50) -> list[Job]:
    """Arbeitnow job board API (mostly Europe, many remote; no key). Filtered locally."""
    data = _get("https://www.arbeitnow.com/api/job-board-api")
    words = [w for w in query.lower().split() if len(w) > 1]
    jobs = []
    for j in (data or {}).get("data", []):
        blob = f"{j.get('title', '')} {' '.join(j.get('tags') or [])} {j.get('description', '')}".lower()
        if words and not all(w in blob for w in words):
            continue
        created = j.get("created_at")
        posted = ""
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone
            posted = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
        jobs.append(Job(
            id=f"arbeitnow:{j.get('slug')}", title=j.get("title", ""), company=j.get("company_name", ""),
            location=j.get("location") or "", url=j.get("url", ""), description=strip_html(j.get("description", "")),
            source="arbeitnow", remote=bool(j.get("remote")), tags=list(j.get("tags") or []),
            posted_at=posted, job_type=", ".join(j.get("job_types") or [])))
        if len(jobs) >= limit:
            break
    return jobs


def adzuna(query: str, limit: int = 50, where: str = "") -> list[Job]:
    """Adzuna (has India listings). Needs free ADZUNA_APP_ID / ADZUNA_APP_KEY."""
    if not (config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY):
        return []
    params = {"app_id": config.ADZUNA_APP_ID, "app_key": config.ADZUNA_APP_KEY, "what": query,
              "results_per_page": min(limit, 50), "content-type": "application/json"}
    if where:
        params["where"] = where
    data = _get(f"https://api.adzuna.com/v1/api/jobs/{config.ADZUNA_COUNTRY}/search/1", params)
    jobs = []
    for j in (data or {}).get("results", []):
        lo, hi = j.get("salary_min"), j.get("salary_max")
        jobs.append(Job(
            id=f"adzuna:{j.get('id')}", title=strip_html(j.get("title", "")),
            company=(j.get("company") or {}).get("display_name", ""),
            location=(j.get("location") or {}).get("display_name", ""), url=j.get("redirect_url", ""),
            description=strip_html(j.get("description", "")), source="adzuna",
            remote="remote" in (j.get("title", "") + j.get("description", "")).lower(),
            salary=f"{int(lo):,} - {int(hi):,}" if lo and hi else "", posted_at=(j.get("created") or "")[:10],
            job_type=j.get("contract_time") or ""))
    return jobs


def sample(query: str, limit: int = 50) -> list[Job]:
    """Bundled demo listings (fictional companies) so the app works offline."""
    if not config.SAMPLE_JOBS.exists():
        return []
    words = [w for w in query.lower().split() if len(w) > 1]
    out = []
    for j in json.loads(config.SAMPLE_JOBS.read_text(encoding="utf-8")):
        blob = f"{j['title']} {' '.join(j.get('tags', []))} {j['description']}".lower()
        if not words or any(w in blob for w in words):
            out.append(Job(**j))
    return out[:limit]


REGISTRY = {"remotive": remotive, "arbeitnow": arbeitnow, "adzuna": adzuna, "sample": sample}


def search_all(query: str, sources: list[str] | None = None, limit_per_source: int = 40) -> list[Job]:
    seen, jobs = set(), []
    for name in sources or config.SOURCES:
        fn = REGISTRY.get(name)
        if not fn:
            continue
        for job in fn(query, limit_per_source):
            key = (job.title.lower().strip(), job.company.lower().strip())
            if job.id in seen or key in seen:
                continue
            seen.update({job.id, key})
            jobs.append(job)
    return jobs
