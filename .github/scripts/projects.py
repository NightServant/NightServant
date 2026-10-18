"""Regenerate the README's Featured projects section from public GitHub data.

Every public, non-fork repo pushed in the last year is listed, newest first. Status comes from
GitHub itself: New (created in the last 30 days), Live (homepage URL set), Deployed (a successful
production deployment), Released <tag> (latest release), Completed (`completed` topic or archived).
Each project renders as a card (assets/projects/<repo>[-mobile].svg) in its own app's palette, since
GitHub strips CSS and an image is the only way to color a section. Titles, blurbs, stack lines,
homepage (live demo) URLs, and palettes can be overridden in .github/projects.json.
Usage:
  GITHUB_TOKEN=... python projects.py <login> README.md .github/projects.json
  python projects.py --check
"""
import datetime as dt
import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

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
NEUTRAL = ["#737373", "#404040", "#262626"]  # palette for repos without one in projects.json
FONT = "Geist, Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
CARD_DIR = "assets/projects"
RAW = "https://raw.githubusercontent.com/{login}/{login}/main/" + CARD_DIR


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


def palette(o):
    """[primary, secondary, surface]; surface (card background) defaults to secondary."""
    pal = o.get("palette", NEUTRAL)
    return pal[0], pal[1], pal[2] if len(pal) > 2 else pal[1]


def plain(md):
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)  # card text can't hold links


def card(title, status, blurb, stack, pal, mobile):
    """A self-contained SVG card on the app's surface color with its primary as the accent.
    It carries its own background, so it reads the same on GitHub's light and dark themes."""
    primary, _, surface = pal
    w, pad = (400, 24) if mobile else (840, 32)
    title_size, body = (20, 14) if mobile else (24, 15)
    wrap = 44 if mobile else 92                     # chars per line at the body size, conservatively
    out, y = [], pad + title_size
    out.append(f'<text x="{pad}" y="{y}" font-size="{title_size}" font-weight="700" fill="#fafafa">{escape(title)}</text>')
    pill_w = 7 * len(status) + 22
    if mobile:                                      # no room beside the title on a phone
        y += 14
        pill_x, pill_y = pad, y
        y += 22
    else:
        pill_x, pill_y = w - pad - pill_w, y - 19
    out.append(f'<rect x="{pill_x}" y="{pill_y}" width="{pill_w}" height="22" rx="11" fill="{primary}"/>'
               f'<text x="{pill_x + pill_w / 2}" y="{pill_y + 15}" font-size="12" font-weight="600" fill="#ffffff" '
               f'text-anchor="middle">{escape(status)}</text>')
    y += 14
    for line in textwrap.wrap(plain(blurb), wrap):
        y += round(body * 1.5)
        out.append(f'<text x="{pad}" y="{y}" font-size="{body}" fill="#ffffff" fill-opacity="0.84">{escape(line)}</text>')
    if stack:
        y += 20
        out.append(f'<line x1="{pad}" y1="{y}" x2="{w - pad}" y2="{y}" stroke="#ffffff" stroke-opacity="0.14"/>')
        for line in textwrap.wrap(stack, wrap + 6):
            y += 22
            out.append(f'<text x="{pad}" y="{y}" font-size="13" fill="#ffffff" fill-opacity="0.62">{escape(line)}</text>')
    h = y + pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{escape(title)}" font-family="{FONT}">\n<title>{escape(title)}</title>\n'
            f'<clipPath id="c"><rect width="{w}" height="{h}" rx="14"/></clipPath>\n<g clip-path="url(#c)">'
            f'<rect width="{w}" height="{h}" fill="{surface}"/><rect width="6" height="{h}" fill="{primary}"/></g>\n'
            + "\n".join(out) + "\n</svg>\n")


def render(repos, overrides, now, login):
    """Returns (markdown, {card path: svg}). Each project is a card in its own app's palette;
    its links are badges below it (text links in GitHub markdown are always blue)."""
    blocks, files = [], {}
    for r in repos:
        o = overrides.get(r["name"], {})
        r = {**r, "homepageUrl": o.get("homepage") or r["homepageUrl"]}  # override counts as Live
        title = o.get("title", r["name"])
        pal = palette(o)
        status = " · ".join(statuses(r, now))
        topics = [t["topic"]["name"] for t in r["repositoryTopics"]["nodes"] if t["topic"]["name"] != "completed"]
        stack = o.get("stack") or " · ".join(topics[:6]) or (r["primaryLanguage"] or {}).get("name", "")
        blurb = o.get("blurb") or r["description"] or ""
        for mobile in (False, True):
            files[f"{CARD_DIR}/{r['name']}{'-mobile' if mobile else ''}.svg"] = card(title, status, blurb, stack, pal, mobile)
        raw = RAW.format(login=login)
        alt = escape(f"{title} ({status}). {plain(blurb)} Built with {stack}.".replace("  ", " "), {'"': "&quot;"})
        links = []
        if r["homepageUrl"]:
            links.append(f'<a href="{r["homepageUrl"]}"><img src="{badge("Live demo ↗", pal[0])}" alt="{title} live demo" /></a>')
        links.append(f'<a href="{r["url"]}"><img src="{badge("Source", pal[1], "github")}" alt="{title} source code" /></a>')
        # A bare <picture> block: GitHub's sanitizer breaks a <picture> nested in a link, so the badges carry the links.
        blocks.append(f'''<picture>
  <source media="(max-width: 700px)" srcset="{raw}/{r["name"]}-mobile.svg" />
  <img src="{raw}/{r["name"]}.svg" width="100%" alt="{alt}" />
</picture>

{" ".join(links)}''')
    return "\n\n".join(blocks), files


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
    shown, files = render([{**base, "description": "Uses [A&B](https://l) <fast>"}], {"x": {"homepage": "https://h.dev", "palette": ["#c2410c", "#18181b"]}}, now, "me")
    assert '<a href="https://h.dev">' in shown and "Live%20demo%20%E2%86%97-c2410c" in shown and "Source-18181b" in shown
    assert "raw.githubusercontent.com/me/me/main/assets/projects/x-mobile.svg" in shown
    import xml.dom.minidom
    for svg in files.values():
        xml.dom.minidom.parseString(svg)                                           # well-formed despite & and <
        assert 'fill="#18181b"' in svg and 'fill="#c2410c"' in svg and "Uses A&amp;B &lt;fast&gt;" in svg
    assert 'fill="#262626"' in render([base], {}, now, "me")[1]["assets/projects/x.svg"]   # no palette -> neutral
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
    section, files = render(repos, config.get("projects", {}), now, login)
    os.makedirs(CARD_DIR, exist_ok=True)
    for name in os.listdir(CARD_DIR):               # drop cards for projects no longer listed
        if f"{CARD_DIR}/{name}" not in files:
            os.remove(f"{CARD_DIR}/{name}")
    changed = 0
    for path, svg in files.items():
        if not os.path.exists(path) or open(path).read() != svg:
            open(path, "w").write(svg)
            changed += 1
    readme = open(readme_path).read()
    updated = splice(readme, section)
    if updated != readme:
        open(readme_path, "w").write(updated)
    print(f"{len(repos)} projects:", ", ".join(r["name"] for r in repos),
          f"(README {'changed' if updated != readme else 'unchanged'}, {changed} cards written)")
