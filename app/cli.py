"""Command line: search from the terminal or write a daily digest.

  python -m app.cli search "genai engineer" --resume my_resume.pdf --location Hyderabad
  python -m app.cli digest --resume my_resume.pdf --out digest.md
"""
import argparse
from datetime import date
from pathlib import Path

from . import matcher, sources
from .resume import parse_resume, read_resume


def _profile(args):
    path = Path(args.resume)
    p = parse_resume(read_resume(path.name, path.read_bytes()))
    if args.location:
        p.locations = [args.location]
    if args.roles:
        p.target_roles = [r.strip() for r in args.roles.split(",")]
    return p


def cmd_search(args):
    p = _profile(args)
    ranked = matcher.rank(sources.search_all(args.query, args.sources), p)
    for r in ranked[: args.top]:
        j, m = r["job"], r["match"]
        print(f"{m['score']:>3}  {j['title'][:55]:<55} {j['company'][:28]:<28} {j['location'][:22]}")
        if m["missing_skills"]:
            print(f"      missing: {', '.join(m['missing_skills'][:6])}")
    gaps = matcher.skill_gap_report(ranked)
    if gaps:
        print("\nSkills to learn next:", ", ".join(f"{g['skill']} ({g['jobs']})" for g in gaps[:5]))


def cmd_digest(args):
    p = _profile(args)
    queries = args.queries.split(",") if args.queries else (p.target_roles or ["python"])
    jobs = []
    for q in queries:
        jobs += sources.search_all(q.strip(), args.sources)
    seen, uniq = set(), []
    for j in jobs:
        if j.id not in seen:
            seen.add(j.id)
            uniq.append(j)
    ranked = [r for r in matcher.rank(uniq, p) if r["match"]["score"] >= args.min_score][: args.top]
    lines = [f"# Job digest for {p.name or 'you'} — {date.today():%d %b %Y}", "",
             f"{len(ranked)} matches scoring {args.min_score}+ for: {', '.join(queries)}", ""]
    for r in ranked:
        j, m = r["job"], r["match"]
        lines += [f"## {m['score']}/100 · [{j['title']}]({j['url']}) — {j['company']}",
                  f"{j['location']}{' · Remote' if j['remote'] else ''}{' · ' + j['salary'] if j['salary'] else ''}",
                  f"- Matches: {', '.join(m['matched_skills'][:8]) or '—'}",
                  f"- Missing: {', '.join(m['missing_skills'][:6]) or '—'}", ""]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out} ({len(ranked)} jobs)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="job-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("search", "digest"):
        sp = sub.add_parser(name)
        if name == "search":
            sp.add_argument("query")
        sp.add_argument("--resume", required=True)
        sp.add_argument("--location")
        sp.add_argument("--roles", help="comma-separated target roles")
        sp.add_argument("--sources", nargs="*", help="remotive arbeitnow adzuna sample")
        sp.add_argument("--top", type=int, default=15)
        if name == "digest":
            sp.add_argument("--queries")
            sp.add_argument("--min-score", type=int, default=60)
            sp.add_argument("--out", default="digest.md")
    args = ap.parse_args(argv)
    {"search": cmd_search, "digest": cmd_digest}[args.cmd](args)


if __name__ == "__main__":
    main()
