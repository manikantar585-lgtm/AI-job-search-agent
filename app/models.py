from dataclasses import asdict, dataclass, field


@dataclass
class Job:
    id: str                      # "<source>:<native id>"
    title: str
    company: str
    location: str
    url: str
    description: str             # plain text
    source: str
    remote: bool = False
    tags: list[str] = field(default_factory=list)
    salary: str = ""
    posted_at: str = ""          # ISO date
    job_type: str = ""           # full_time / internship / contract ...

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Profile:
    name: str = ""
    skills: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    years_experience: float = 0
    locations: list[str] = field(default_factory=list)   # e.g. ["Hyderabad", "Bengaluru"]
    remote_ok: bool = True
    job_types: list[str] = field(default_factory=list)   # empty = any
    resume_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("resume_text")
        return d
