"""Render the profile analytics dashboard as light/dark SVGs from public GitHub data.

Layout follows the github-dashboard skill (KPI strip, 2fr/1fr grid, provenance footer),
drawn as SVG because GitHub READMEs strip CSS/JS. Usage:
  GITHUB_TOKEN=... python dashboard.py <login> <out_dir>
  python dashboard.py --check
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from xml.sax.saxutils import escape

QUERY = """
query($login: String!, $pr: String!, $issue: String!) {
  user(login: $login) {
    name login
    followers { totalCount }
    following { totalCount }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name stargazerCount pushedAt
        primaryLanguage { name color }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name color } } }
      }
    }
    contributionsCollection {
      totalCommitContributions totalPullRequestReviewContributions
      contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
    }
  }
  prs: search(query: $pr, type: ISSUE) { issueCount }
  issues: search(query: $issue, type: ISSUE) { issueCount }
}"""

THEMES = {
    # Soft Paper tokens from the github-dashboard skill template.
    "light": dict(canvas="#f2f2f0", surface="#ffffff", border="#ececea", ink="#0a0a0a",
                  text2="#6b6b6b", text3="#9a9a95", accent="#1f7a73", track="#ececea",
                  pill_bg="#e6f4ea", pill_fg="#1f8a4c", blue_bg="#dcebff", blue_fg="#2f66c9",
                  amber_bg="#fff6d6", amber_fg="#9a7b12"),
    # Tokyo Night tokens, matching the rest of the README.
    "dark": dict(canvas="#1a1b27", surface="#1f2335", border="#2a2f45", ink="#c0caf5",
                 text2="#a9b1d6", text3="#737aa2", accent="#38b2ac", track="#2a2f45",
                 pill_bg="#1e3a33", pill_fg="#73daca", blue_bg="#1f2e4d", blue_fg="#7aa2f7",
                 amber_bg="#3a321c", amber_fg="#e0af68"),
}

W, PAD, GAP = 1000, 24, 16
FONT = "Geist, Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


def fetch(login, token):
    body = json.dumps({"query": QUERY, "variables": {
        "login": login,
        "pr": f"author:{login} is:pr is:public",
        "issue": f"author:{login} is:issue is:public",
    }}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", body, {
        "Authorization": f"bearer {token}", "Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    if data.get("errors"):
        sys.exit(f"GraphQL error: {data['errors']}")
    return data["data"]


def streaks(counts, today_counted=False):
    """(current, longest) runs of days with >0 contributions. counts are oldest->newest.
    Like GitHub, a zero today doesn't break the current streak until the day is over."""
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    tail = counts if (today_counted or counts[-1]) else counts[:-1]
    current = 0
    for c in reversed(tail):
        if not c:
            break
        current += 1
    return current, longest


def summarize(d):
    u = d["user"]
    repos = u["repositories"]["nodes"]
    cc = u["contributionsCollection"]
    days = [day for w in cc["contributionCalendar"]["weeks"] for day in w["contributionDays"]]
    current, longest = streaks([day["contributionCount"] for day in days])
    langs = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            n = e["node"]
            size, color = langs.get(n["name"], (0, n["color"]))
            langs[n["name"]] = (size + e["size"], color or "#9a9a95")
    total_bytes = sum(s for s, _ in langs.values()) or 1
    top_langs = sorted(langs.items(), key=lambda kv: -kv[1][0])[:5]
    return dict(
        name=u["name"] or u["login"], login=u["login"],
        followers=u["followers"]["totalCount"], following=u["following"]["totalCount"],
        repo_count=u["repositories"]["totalCount"],
        stars=sum(r["stargazerCount"] for r in repos),
        contributions=cc["contributionCalendar"]["totalContributions"],
        commits=cc["totalCommitContributions"], reviews=cc["totalPullRequestReviewContributions"],
        prs=d["prs"]["issueCount"], issues=d["issues"]["issueCount"],
        current=current, longest=longest,
        weeks=[sum(x["contributionCount"] for x in w["contributionDays"])
               for w in cc["contributionCalendar"]["weeks"]],
        first_day=days[0]["date"], last_day=days[-1]["date"],
        langs=[(n, s / total_bytes, c) for n, (s, c) in top_langs],
        repos=sorted(repos, key=lambda r: -r["stargazerCount"])[:5],  # stable: ties keep push order
    )


def fmt(n):
    return f"{n:,}"


def text(x, y, s, size=13, fill="ink", weight=400, anchor="start", extra=""):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{{{fill}}}" '
            f'text-anchor="{anchor}" {extra}>{escape(str(s))}</text>')


def card(x, y, w, h):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{{surface}}" stroke="{{border}}"/>'


def pill(x, y, label, kind="pill"):
    w = 7 * len(label) + 16
    return (f'<rect x="{x}" y="{y}" width="{w}" height="20" rx="10" fill="{{{kind}_bg}}"/>'
            + text(x + w / 2, y + 14, label, 11, f"{kind}_fg", 500, "middle"))


def render(s, now):
    out = []
    add = out.append
    inner = W - 2 * PAD

    # Header
    add('<g data-od-id="repo-header">')
    add(text(PAD, 44, f"{s['name']} · GitHub analytics", 20, "ink", 600))
    add(text(PAD, 66, f"@{s['login']} · {s['repo_count']} public repositories · "
                      f"{fmt(s['followers'])} followers · {fmt(s['following'])} following", 13, "text2"))
    upd = f"Updated {now:%b %-d, %Y}"
    add(pill(W - PAD - (7 * len(upd) + 16), 30, upd))
    add("</g>")

    # KPI strip
    kpis = [
        ("Contributions", fmt(s["contributions"]), "past year"),
        ("Current streak", f"{s['current']} d", f"longest {s['longest']} d"),
        ("Commits", fmt(s["commits"]), "past year"),
        ("Pull requests", fmt(s["prs"]), f"{fmt(s['issues'])} issues opened"),
        ("Stars earned", fmt(s["stars"]), f"across {s['repo_count']} repos"),
    ]
    kw = (inner - 4 * 12) / 5
    add('<g data-od-id="kpi-strip">')
    for i, (label, value, sub) in enumerate(kpis):
        x = PAD + i * (kw + 12)
        add(card(x, 88, kw, 92))
        add(text(x + 16, 112, label.upper(), 11, "text3", 600, extra='letter-spacing="0.6"'))
        add(text(x + 16, 146, value, 28, "ink", 600))
        add(text(x + 16, 168, sub, 12, "text2"))
    add("</g>")

    left_w = (inner - GAP) * 2 / 3
    right_x = PAD + left_w + GAP
    right_w = inner - left_w - GAP

    # Contribution activity (weekly bars)
    y0, h = 196, 220
    add('<g data-od-id="growth-chart">')
    add(card(PAD, y0, left_w, h))
    add(text(PAD + 20, y0 + 30, "Contribution activity", 14, "ink", 600))
    add(text(PAD + left_w - 20, y0 + 30, f"{fmt(s['contributions'])} contributions · weekly", 12, "text2", anchor="end"))
    weeks = s["weeks"]
    peak = max(weeks) or 1
    cx, cy, cw, ch = PAD + 20, y0 + 52, left_w - 40, 130
    step = cw / len(weeks)
    for i, v in enumerate(weeks):
        bh = max(2, v / peak * ch) if v else 2
        fill = "{accent}" if v else "{track}"
        add(f'<rect x="{cx + i * step:.1f}" y="{cy + ch - bh:.1f}" width="{step - 2:.1f}" height="{bh:.1f}" rx="1.5" fill="{fill}"/>')
    first = dt.date.fromisoformat(s["first_day"])
    last = dt.date.fromisoformat(s["last_day"])
    add(text(cx, cy + ch + 22, f"{first:%b %Y}", 11, "text3"))
    add(text(cx + cw, cy + ch + 22, f"{last:%b %Y}", 11, "text3", anchor="end"))
    add(text(cx + cw / 2, cy + ch + 22, f"peak {peak} / week", 11, "text3", anchor="middle"))
    add("</g>")

    # Top languages
    add('<g data-od-id="languages">')
    add(card(right_x, y0, right_w, h))
    add(text(right_x + 20, y0 + 30, "Top languages", 14, "ink", 600))
    add(text(right_x + right_w - 20, y0 + 30, "by code size", 12, "text2", anchor="end"))
    bx, bw = right_x + 20, right_w - 40
    add(f'<rect x="{bx}" y="{y0 + 48}" width="{bw}" height="8" rx="4" fill="{{track}}"/>')
    off = 0
    for name, share, color in s["langs"]:
        add(f'<rect x="{bx + off:.1f}" y="{y0 + 48}" width="{max(bw * share, 1):.1f}" height="8" fill="{color}"/>')
        off += bw * share
    for i, (name, share, color) in enumerate(s["langs"]):
        ly = y0 + 84 + i * 28
        add(f'<circle cx="{bx + 5}" cy="{ly - 4}" r="5" fill="{color}"/>')
        add(text(bx + 18, ly, name, 13))
        add(text(bx + bw, ly, f"{share * 100:.1f}%", 13, "text2", anchor="end"))
    add("</g>")

    # Top repositories table
    y1, h1 = y0 + h + GAP, 232
    add('<g data-od-id="activity">')
    add(card(PAD, y1, left_w, h1))
    add(text(PAD + 20, y1 + 30, "Top repositories", 14, "ink", 600))
    cols = [(PAD + 20, "REPOSITORY", "start"), (PAD + 330, "LANGUAGE", "start"),
            (PAD + left_w - 120, "STARS", "end"), (PAD + left_w - 20, "LAST PUSH", "end")]
    for x, label, anchor in cols:
        add(text(x, y1 + 58, label, 11, "text3", 600, anchor, 'letter-spacing="0.6"'))
    for i, r in enumerate(s["repos"]):
        ry = y1 + 66 + i * 32
        add(f'<line x1="{PAD + 20}" y1="{ry}" x2="{PAD + left_w - 20}" y2="{ry}" stroke="{{border}}"/>')
        name = r["name"] if len(r["name"]) <= 34 else r["name"][:33] + "…"
        add(text(PAD + 20, ry + 21, name, 13, "ink", 500))
        lang = r["primaryLanguage"]["name"] if r["primaryLanguage"] else "—"
        add(pill(PAD + 330, ry + 6, lang, "blue") if r["primaryLanguage"] else text(PAD + 330, ry + 21, lang, 13, "text3"))
        add(text(PAD + left_w - 120, ry + 21, fmt(r["stargazerCount"]), 13, "ink", anchor="end"))
        pushed = dt.datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00"))
        add(text(PAD + left_w - 20, ry + 21, f"{pushed:%b %-d, %Y}", 13, "text2", anchor="end"))
    add("</g>")

    # Activity mix
    add('<g data-od-id="contributors">')
    add(card(right_x, y1, right_w, h1))
    add(text(right_x + 20, y1 + 30, "Activity mix", 14, "ink", 600))
    mix = [("Commits", s["commits"], "pill", "past year"), ("Pull requests", s["prs"], "blue", "all time"),
           ("Code reviews", s["reviews"], "blue", "past year"), ("Issues", s["issues"], "amber", "all time")]
    top = max(v for _, v, _, _ in mix) or 1
    for i, (label, v, kind, span) in enumerate(mix):
        my = y1 + 62 + i * 40
        add(text(bx, my, label, 13))
        add(text(bx + bw, my, f"{fmt(v)} · {span}", 12, "text2", anchor="end"))
        add(f'<rect x="{bx}" y="{my + 9}" width="{bw}" height="6" rx="3" fill="{{track}}"/>')
        add(f'<rect x="{bx}" y="{my + 9}" width="{max(bw * v / top, 3 if v else 0):.1f}" height="6" rx="3" fill="{{{kind}_fg}}"/>')
    add("</g>")

    # Provenance footer
    fy = y1 + h1 + 30
    add(text(PAD, fy, f"Source: GitHub GraphQL API · public data only · generated {now:%Y-%m-%d %H:%M} UTC "
                      "by .github/workflows/dashboard.yml", 11, "text3", extra='data-od-id="provenance"'))
    H = fy + 18
    body = "\n".join(out)
    title = (f"{s['name']} GitHub analytics: {fmt(s['contributions'])} contributions in the past year, "
             f"current streak {s['current']} days (longest {s['longest']}), {fmt(s['commits'])} commits, "
             f"{fmt(s['prs'])} pull requests, {fmt(s['stars'])} stars, top language "
             f"{s['langs'][0][0] if s['langs'] else 'n/a'}")
    return H, body, title


def to_svg(H, body, title, theme):
    t = THEMES[theme]
    for k, v in t.items():
        body = body.replace("{" + k + "}", v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="{escape(title)}" font-family="{FONT}" '
            f'style="font-variant-numeric: tabular-nums">\n<title>{escape(title)}</title>\n'
            f'<rect width="{W}" height="{H}" rx="16" fill="{t["canvas"]}"/>\n{body}\n</svg>\n')


def check():
    assert streaks([1, 1, 0, 1, 1, 1]) == (3, 3)
    assert streaks([1, 1, 1, 0]) == (3, 3)          # today still open: streak holds
    assert streaks([1, 0, 0]) == (0, 1)
    assert streaks([0, 1, 1, 0, 1]) == (1, 2)
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        check()
        sys.exit()
    login, out_dir = sys.argv[1], sys.argv[2]
    s = summarize(fetch(login, os.environ["GITHUB_TOKEN"]))
    H, body, title = render(s, dt.datetime.now(dt.timezone.utc))
    os.makedirs(out_dir, exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(out_dir, f"dashboard-{theme}.svg"), "w") as f:
            f.write(to_svg(H, body, title, theme))
    print(title)
