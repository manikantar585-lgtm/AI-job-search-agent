"""Skill taxonomy + extraction from free text (resumes and job descriptions)."""
import re

# canonical skill -> aliases (lowercase). Grouped so related skills give partial credit.
SKILLS: dict[str, dict] = {}


def _add(group: str, items: dict[str, list[str]]):
    for name, aliases in items.items():
        SKILLS[name] = {"group": group, "aliases": [name.lower(), *aliases]}


_add("languages", {
    "Python": [], "Java": [], "JavaScript": ["js", "es6"], "TypeScript": ["ts"], "C++": ["cpp"], "C#": ["c sharp"],
    "Go": ["golang"], "Rust": [], "R": ["r language", "r programming"], "Scala": [], "Kotlin": [], "SQL": ["t-sql", "pl/sql"],
    "Bash": ["shell scripting", "shell"],
})
_add("ml", {
    "Machine Learning": ["ml", "machine-learning"], "Deep Learning": ["dl", "neural networks", "neural network"],
    "NLP": ["natural language processing"], "Computer Vision": ["image processing", "opencv"],
    "Scikit-learn": ["sklearn", "scikit learn"], "TensorFlow": ["tf", "keras"], "PyTorch": ["torch"],
    "XGBoost": ["lightgbm", "gradient boosting"], "Statistics": ["statistical", "hypothesis testing", "a/b testing"],
    "Time Series": ["forecasting"], "Recommendation Systems": ["recommender", "recommendation system"],
    "Reinforcement Learning": ["rl"], "Transformers": ["transformer", "bert", "vision transformer", "vit"],
    "CNN": ["convolutional neural network", "cnns"], "Model Deployment": ["model serving", "inference optimization"],
})
_add("genai", {
    "LLM": ["llms", "large language model", "large language models", "gpt", "llama", "mistral", "gemini"],
    "Generative AI": ["genai", "gen ai", "generative ai"], "RAG": ["retrieval augmented generation", "retrieval-augmented"],
    "LangChain": [], "LlamaIndex": ["llama index", "llama-index"], "Prompt Engineering": ["prompting", "prompt design"],
    "Fine-tuning": ["fine tuning", "finetuning", "lora", "qlora", "peft", "sft"], "Hugging Face": ["huggingface", "hf"],
    "Vector Databases": ["vector db", "vector database", "pinecone", "faiss", "chroma", "chromadb", "weaviate", "qdrant", "milvus", "pgvector"],
    "Embeddings": ["embedding", "sentence transformers", "sentence-transformers"],
    "AI Agents": ["agents", "agentic", "tool calling", "function calling", "langgraph", "crewai", "autogen"],
    "LLM Evaluation": ["evals", "llm evaluation", "ragas"], "OpenAI API": ["openai"],
})
_add("data", {
    "Pandas": [], "NumPy": ["numpy"], "Data Analysis": ["data analytics", "eda", "exploratory data analysis"],
    "Data Visualization": ["matplotlib", "seaborn", "plotly"], "Power BI": ["powerbi"], "Tableau": [], "Excel": ["ms excel", "advanced excel"],
    "Spark": ["pyspark", "apache spark"], "Airflow": ["apache airflow"], "ETL": ["data pipelines", "data pipeline", "elt"],
    "Data Engineering": [], "dbt": [], "Kafka": [],
})
_add("web", {
    "React": ["react.js", "reactjs"], "Node.js": ["node", "nodejs", "express", "express.js"], "Next.js": ["nextjs"],
    "Django": [], "Flask": [], "FastAPI": [], "HTML": ["html5"], "CSS": ["css3", "tailwind", "tailwindcss"],
    "REST APIs": ["rest", "restful", "rest api", "api development"], "GraphQL": [], "Angular": [], "Vue": ["vue.js", "vuejs"],
})
_add("db", {
    "PostgreSQL": ["postgres"], "MySQL": [], "MongoDB": ["mongo"], "SQL Server": ["mssql", "microsoft sql server"],
    "Redis": [], "Elasticsearch": ["opensearch"], "SQLite": [],
})
_add("cloud", {
    "AWS": ["amazon web services", "sagemaker", "ec2", "s3", "lambda", "bedrock"], "Azure": ["azure ml", "azure openai"],
    "GCP": ["google cloud", "vertex ai", "bigquery"], "Docker": ["containers"], "Kubernetes": ["k8s"],
    "MLOps": ["mlflow", "kubeflow", "model monitoring", "ml ops"], "CI/CD": ["github actions", "jenkins", "ci cd"],
    "Git": ["github", "gitlab", "version control"], "Linux": ["unix"],
})

# Names that are too ambiguous to match on their own ("R", "Go") - only their aliases count.
_NO_BARE = {"R", "Go"}
# Short tokens that only count when written in capitals ("ML", not "ml" inside a word list).
_UPPER_ONLY = {"ml", "dl", "cv", "rl", "tf", "ts", "hf", "s3"}
# Common English words that only count near a technical context word.
_CONTEXT = {"rest", "node", "shell", "agents", "express", "lambda", "openai", "containers", "forecasting", "evals"}
_CONTEXT_RE = re.compile(r"api|\.js|script|ai\b|llm|server|aws|backend|model|data|ml|python", re.I)

_PATTERNS: list[tuple[re.Pattern, str]] = []
for _name, _info in SKILLS.items():
    aliases = set(_info["aliases"])
    if _name in _NO_BARE:
        aliases.discard(_name.lower())
    for _alias in sorted(aliases, key=len, reverse=True):
        # boundaries that also work for c++, c#, node.js, ci/cd
        _PATTERNS.append((re.compile(rf"(?<![\w+#.]){re.escape(_alias)}(?![\w+#])", re.I), _name))


def extract_skills(text: str) -> set[str]:
    found: set[str] = set()
    for pat, name in _PATTERNS:
        if name in found:
            continue
        for m in pat.finditer(text):
            token = m.group(0)
            low = token.lower()
            if low in _UPPER_ONLY and token != token.upper():
                continue
            if low in _CONTEXT and not _CONTEXT_RE.search(text[max(0, m.start() - 40): m.end() + 40].replace(token, " ")):
                continue
            found.add(name)
            break
    return found


def group_of(skill: str) -> str | None:
    info = SKILLS.get(skill)
    return info["group"] if info else None


YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)


def required_years(text: str) -> int | None:
    """Minimum years of experience a job asks for, e.g. '3+ years', '2-4 yrs'."""
    vals = [int(m.group(1)) for m in YEARS_RE.finditer(text) if int(m.group(1)) <= 20]
    return min(vals) if vals else None
