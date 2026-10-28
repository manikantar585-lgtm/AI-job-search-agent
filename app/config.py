"""Settings from environment / .env."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "jobs.db"))
SAMPLE_JOBS = BASE_DIR / "data" / "sample_jobs.json"
# Comma-separated list of sources: remotive, arbeitnow, adzuna, sample
SOURCES = [s.strip() for s in os.getenv("SOURCES", "remotive,arbeitnow,sample").split(",") if s.strip()]

# Optional Adzuna API (has India listings): https://developer.adzuna.com
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "in")

# Optional LLM (OpenAI-compatible with tool calling: Groq, OpenAI, Together...)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
