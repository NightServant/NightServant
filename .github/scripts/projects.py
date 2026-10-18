"""Regenerate the README's Featured projects section from public GitHub data.

Every public, non-fork repo pushed in the last year is listed, newest first. Status comes from
GitHub itself: New (created in the last 30 days), Live (homepage URL set), Deployed (a successful
production deployment), Released <tag> (latest release), Completed (`completed` topic or archived).
Titles, blurbs, stack lines, and homepage (live demo) URLs can be overridden in .github/projects.json.
Usage:
  GITHUB_TOKEN=... python projects.py <login> README.md .github/projects.json
  python projects.py --check
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

QUERY = """
query($login: String!) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100,
                 orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        name url description homepageUrl createdAt pushedAt isArchived
        primaryLanguage { name }
        repositoryTopics(first: 10) { nodes { topic { name } } }
        latestRelease { tagName }
        deployments(last: 10) { nodes { environment latestStatus { state } } }
      }
    }
  }
}"""

START, END = "<!-- PROJECTS:START -->", "<!-- PROJECTS:END -->"
MAX_PROJECTS = 6
NEUTRAL = ["#737373", "#404040"]  # palette for repos without one in projects.json


def fetch(login, token):
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", body, {
        "Authorization": f"bearer {token}", "Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    if data.get("errors"):
        sys.exit(f"GraphQL error: {data['errors']}")
    return data["data"]["user"]["repositories"]["nodes"]


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def statuses(repo, now):
    topics = {t["topic"]["name"] for t in repo["repositoryTopics"]["nodes"]}
    deployed = any(re.search("production", d["environment"] or "", re.I)
                   and (d["latestStatus"] or {}).get("state") == "SUCCESS"
                   for d in repo["deployments"]["nodes"])
    out = []
    if now - parse_time(repo["createdAt"]) <= dt.timedelta(days=30):
        out.append("New")
    if repo["homepageUrl"]:
        out.append("Live")
    elif deployed:
        out.append("Deployed")
    if repo["latestRelease"]:
        out.append(f"Released {repo['latestRelease']['tagName']}")
    if "completed" in topics or repo["isArchived"]:
        out.append("Completed")
    if not set(out) - {"New"}:
        out.append("In development")
    return out


def select(repos, login, hidden, now):
    year_ago = now - dt.timedelta(days=365)
    keep = [r for r in repos
            if r["name"] != login and r["name"] not in hidden and parse_time(r["pushedAt"]) >= year_ago]
    return keep[:MAX_PROJECTS]  # already newest-created first


def badge(text, color, logo=None):
    label = urllib.parse.quote(text.replace("-", "--").replace("_", "__"), safe="")
    url = f"https://img.shields.io/badge/{label}-{color.lstrip('#')}?style=flat-square"
    return url + (f"&logo={logo}&logoColor=white" if logo else "")


def render(repos, overrides, now):
    """Each project is color-coded with its own app's palette: status in the primary color,
    links in the secondary. Links are badges because GitHub forces text links to blue."""
    blocks = []
    for r in repos:
        o = overrides.get(r["name"], {})
        r = {**r, "homepageUrl": o.get("homepage") or r["homepageUrl"]}  # override counts as Live
        title = o.get("title", r["name"])
        primary, secondary = o.get("palette", NEUTRAL)
        status = " · ".join(statuses(r, now))
        links = [f'<img src="{badge(status, primary)}" alt="Status: {status}" />']
        if r["homepageUrl"]:
            links.append(f'<a href="{r["homepageUrl"]}"><img src="{badge("Live demo ↗", secondary)}" alt="{title} live demo" /></a>')
        links.append(f'<a href="{r["url"]}"><img src="{badge("Source", secondary, "github")}" alt="{title} source code" /></a>')
        topics = [t["topic"]["name"] for t in r["repositoryTopics"]["nodes"] if t["topic"]["name"] != "completed"]
        stack = o.get("stack") or " · ".join(topics[:6]) or (r["primaryLanguage"] or {}).get("name", "")
        blurb = o.get("blurb") or r["description"] or ""
        blocks.append("\n\n".join(filter(None, [f"### {title}", blurb, " ".join(links), stack and f"<sub>{stack}</sub>"])))
    return "\n\n".join(blocks)


def splice(readme, section):
    if readme.count(START) != 1 or readme.count(END) != 1:
        sys.exit(f"README must contain exactly one {START} and one {END}")
    head, rest = readme.split(START)
    _, tail = rest.split(END)
    return f"{head}{START}\n{section}\n{END}{tail}"


def check():
    now = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc)
    base = dict(name="x", url="u", description="d", homepageUrl="", createdAt="2026-01-01T00:00:00Z",
                pushedAt="2026-09-01T00:00:00Z", isArchived=False, primaryLanguage={"name": "TypeScript"},
                repositoryTopics={"nodes": []}, latestRelease=None, deployments={"nodes": []})
    assert statuses(base, now) == ["In development"]
    assert statuses({**base, "createdAt": "2026-09-10T00:00:00Z"}, now) == ["New", "In development"]
    assert statuses({**base, "homepageUrl": "https://x.dev"}, now) == ["Live"]
    prod = {"nodes": [{"environment": "sunny / production", "latestStatus": {"state": "SUCCESS"}}]}
    assert statuses({**base, "deployments": prod}, now) == ["Deployed"]
    assert statuses({**base, "latestRelease": {"tagName": "v1.0"}, "isArchived": True}, now) == ["Released v1.0", "Completed"]
    old = {**base, "name": "old", "pushedAt": "2025-06-01T00:00:00Z"}
    assert [r["name"] for r in select([base, old, {**base, "name": "me"}], "me", [], now)] == ["x"]
    shown = render([base], {"x": {"homepage": "https://h.dev", "palette": ["#c2410c", "#18181b"]}}, now)
    assert '<a href="https://h.dev">' in shown and "badge/Live-c2410c" in shown and "Live%20demo%20%E2%86%97-18181b" in shown
    assert "badge/In%20development-737373" in render([base], {}, now)          # no palette -> neutral
    assert badge("New · v1-rc_2", "#000") == "https://img.shields.io/badge/New%20%C2%B7%20v1--rc__2-000?style=flat-square"
    assert splice(f"a\n{START}\nold\n{END}\nb", "new") == f"a\n{START}\nnew\n{END}\nb"
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        check()
        sys.exit()
    login, readme_path, overrides_path = sys.argv[1:4]
    config = json.load(open(overrides_path))
    now = dt.datetime.now(dt.timezone.utc)
    repos = select(fetch(login, os.environ["GITHUB_TOKEN"]), login, config.get("hidden", []), now)
    readme = open(readme_path).read()
    updated = splice(readme, render(repos, config.get("projects", {}), now))
    if updated != readme:
        open(readme_path, "w").write(updated)
    print(f"{len(repos)} projects:", ", ".join(r["name"] for r in repos), "(changed)" if updated != readme else "(unchanged)")
