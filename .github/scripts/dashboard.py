"""Render the profile analytics dashboard as SVGs from public GitHub data.

Layout follows the github-dashboard skill (KPI strip, 2fr/1fr grid, provenance footer),
drawn as SVG because GitHub READMEs strip CSS/JS. Outputs:
  dashboard-light.svg / dashboard-dark.svg  desktop, picked by GitHub's theme switcher
  dashboard-mobile.svg                      single column; themes itself via prefers-color-scheme
Usage:
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

GAP = 16
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


def clip(s, n):
    return s if len(s) <= n else s[:n - 1] + "…"


def text(x, y, s, size=13, fill="ink", weight=400, anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{{{fill}}}" '
            f'text-anchor="{anchor}" {extra}>{escape(str(s))}</text>')


def card(x, y, w, h):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="12" fill="{{surface}}" stroke="{{border}}"/>'


def pill(x, y, label, kind="pill"):
    w = 7 * len(label) + 16
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="20" rx="10" fill="{{{kind}_bg}}"/>'
            + text(x + w / 2, y + 14, label, 11, f"{kind}_fg", 500, "middle"))


def card_title(out, x, y, w, title, note=""):
    out.append(text(x + 20, y + 30, title, 14, "ink", 600))
    if note:
        out.append(text(x + w - 20, y + 30, note, 12, "text2", anchor="end"))


# Sections: each draws into `out` at (x, y) with width w.

def header(out, x, y, w, s, now, compact):
    out.append('<g data-od-id="repo-header">')
    if compact:
        out.append(text(x, y + 20, f"{clip(s['name'], 16)} · GitHub analytics", 18, "ink", 600))
        out.append(text(x, y + 42, f"@{s['login']} · {s['repo_count']} public repos", 13, "text2"))
        out.append(text(x, y + 61, f"{fmt(s['followers'])} followers · {fmt(s['following'])} following · "
                                   f"updated {now:%b %-d}", 13, "text2"))
        h = 76
    else:
        out.append(text(x, y + 20, f"{s['name']} · GitHub analytics", 20, "ink", 600))
        out.append(text(x, y + 42, f"@{s['login']} · {s['repo_count']} public repositories · "
                                   f"{fmt(s['followers'])} followers · {fmt(s['following'])} following", 13, "text2"))
        upd = f"Updated {now:%b %-d, %Y}"
        out.append(pill(x + w - (7 * len(upd) + 16), y + 6, upd))
        h = 64
    out.append("</g>")
    return h


def kpi_strip(out, x, y, w, s, cols):
    kpis = [
        ("Contributions", fmt(s["contributions"]), "past year"),
        ("Current streak", f"{s['current']} d", f"longest {s['longest']} d"),
        ("Commits", fmt(s["commits"]), "past year"),
        ("Pull requests", fmt(s["prs"]), f"{fmt(s['issues'])} issues opened"),
        ("Stars earned", fmt(s["stars"]), f"across {s['repo_count']} repos"),
    ]
    gap, ch = 12, 92
    cw = (w - (cols - 1) * gap) / cols
    out.append('<g data-od-id="kpi-strip">')
    for i, (label, value, sub) in enumerate(kpis):
        row, col = divmod(i, cols)
        last_alone = i == len(kpis) - 1 and col == 0 and cols > 1
        kx, ky = x + col * (cw + gap), y + row * (ch + gap)
        out.append(card(kx, ky, w if last_alone else cw, ch))
        out.append(text(kx + 16, ky + 24, label.upper(), 11, "text3", 600, extra='letter-spacing="0.6"'))
        out.append(text(kx + 16, ky + 58, value, 28, "ink", 600))
        out.append(text(kx + 16, ky + 80, sub, 12, "text2"))
    out.append("</g>")
    rows = -(-len(kpis) // cols)
    return rows * ch + (rows - 1) * gap


def activity(out, x, y, w, h, s):
    out.append('<g data-od-id="growth-chart">')
    out.append(card(x, y, w, h))
    card_title(out, x, y, w, "Contribution activity", f"{fmt(s['contributions'])} · weekly")
    weeks = s["weeks"]
    peak = max(weeks) or 1
    cx, cy, cw, ch = x + 20, y + 52, w - 40, h - 90
    step = cw / len(weeks)
    for i, v in enumerate(weeks):
        bh = max(2, v / peak * ch) if v else 2
        fill = "{accent}" if v else "{track}"
        out.append(f'<rect x="{cx + i * step:.1f}" y="{cy + ch - bh:.1f}" width="{max(step - 2, 1):.1f}" '
                   f'height="{bh:.1f}" rx="1.5" fill="{fill}"/>')
    first = dt.date.fromisoformat(s["first_day"])
    last = dt.date.fromisoformat(s["last_day"])
    out.append(text(cx, cy + ch + 22, f"{first:%b %Y}", 11, "text3"))
    out.append(text(cx + cw, cy + ch + 22, f"{last:%b %Y}", 11, "text3", anchor="end"))
    out.append(text(cx + cw / 2, cy + ch + 22, f"peak {peak} / week", 11, "text3", anchor="middle"))
    out.append("</g>")


def languages(out, x, y, w, h, s):
    out.append('<g data-od-id="languages">')
    out.append(card(x, y, w, h))
    card_title(out, x, y, w, "Top languages", "by code size")
    bx, bw = x + 20, w - 40
    out.append(f'<rect x="{bx:.1f}" y="{y + 48:.1f}" width="{bw:.1f}" height="8" rx="4" fill="{{track}}"/>')
    off = 0
    for name, share, color in s["langs"]:
        out.append(f'<rect x="{bx + off:.1f}" y="{y + 48:.1f}" width="{max(bw * share, 1):.1f}" height="8" fill="{color}"/>')
        off += bw * share
    for i, (name, share, color) in enumerate(s["langs"]):
        ly = y + 84 + i * 28
        out.append(f'<circle cx="{bx + 5:.1f}" cy="{ly - 4:.1f}" r="5" fill="{color}"/>')
        out.append(text(bx + 18, ly, name, 13))
        out.append(text(bx + bw, ly, f"{share * 100:.1f}%", 13, "text2", anchor="end"))
    out.append("</g>")


def repos(out, x, y, w, h, s, lang_col):
    out.append('<g data-od-id="activity">')
    out.append(card(x, y, w, h))
    card_title(out, x, y, w, "Top repositories")
    cols = [(x + 20, "REPOSITORY", "start"), (x + w - 120, "STARS", "end"), (x + w - 20, "LAST PUSH", "end")]
    if lang_col:
        cols.append((x + 310, "LANGUAGE", "start"))
    for cx, label, anchor in cols:
        out.append(text(cx, y + 58, label, 11, "text3", 600, anchor, 'letter-spacing="0.6"'))
    for i, r in enumerate(s["repos"]):
        ry = y + 66 + i * 32
        out.append(f'<line x1="{x + 20:.1f}" y1="{ry:.1f}" x2="{x + w - 20:.1f}" y2="{ry:.1f}" stroke="{{border}}"/>')
        out.append(text(x + 20, ry + 21, clip(r["name"], 34 if lang_col else 22), 13, "ink", 500))
        if lang_col:
            lang = r["primaryLanguage"]
            out.append(pill(x + 310, ry + 6, lang["name"], "blue") if lang else text(x + 310, ry + 21, "—", 13, "text3"))
        out.append(text(x + w - 120, ry + 21, fmt(r["stargazerCount"]), 13, "ink", anchor="end"))
        pushed = dt.datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00"))
        out.append(text(x + w - 20, ry + 21, f"{pushed:%b %-d, %Y}", 13, "text2", anchor="end"))
    out.append("</g>")


def mix(out, x, y, w, h, s):
    out.append('<g data-od-id="contributors">')
    out.append(card(x, y, w, h))
    card_title(out, x, y, w, "Activity mix")
    rows = [("Commits", s["commits"], "pill", "past year"), ("Pull requests", s["prs"], "blue", "all time"),
            ("Code reviews", s["reviews"], "blue", "past year"), ("Issues", s["issues"], "amber", "all time")]
    top = max(v for _, v, _, _ in rows) or 1
    bx, bw = x + 20, w - 40
    for i, (label, v, kind, span) in enumerate(rows):
        my = y + 62 + i * 40
        out.append(text(bx, my, label, 13))
        out.append(text(bx + bw, my, f"{fmt(v)} · {span}", 12, "text2", anchor="end"))
        out.append(f'<rect x="{bx:.1f}" y="{my + 9:.1f}" width="{bw:.1f}" height="6" rx="3" fill="{{track}}"/>')
        out.append(f'<rect x="{bx:.1f}" y="{my + 9:.1f}" width="{max(bw * v / top, 3 if v else 0):.1f}" '
                   f'height="6" rx="3" fill="{{{kind}_fg}}"/>')
    out.append("</g>")


def footer(out, x, y, now, lines):
    for i, line in enumerate(lines):
        out.append(text(x, y + 14 + i * 16, line, 11, "text3", extra='data-od-id="provenance"'))
    return 14 + (len(lines) - 1) * 16 + 18


def desktop(s, now):
    W, pad = 1000, 24
    inner = W - 2 * pad
    out = []
    y = pad + header(out, pad, pad, inner, s, now, compact=False)
    y += kpi_strip(out, pad, y, inner, s, cols=5) + GAP
    lw = (inner - GAP) * 2 / 3
    rx, rw = pad + lw + GAP, inner - lw - GAP
    activity(out, pad, y, lw, 220, s)
    languages(out, rx, y, rw, 220, s)
    y += 220 + GAP
    repos(out, pad, y, lw, 232, s, lang_col=True)
    mix(out, rx, y, rw, 232, s)
    y += 232 + 12
    y += footer(out, pad, y, now, [f"Source: GitHub GraphQL API · public data only · generated "
                                   f"{now:%Y-%m-%d %H:%M} UTC by .github/workflows/dashboard.yml"])
    return W, y, out


def mobile(s, now):
    W, pad = 400, 16
    inner = W - 2 * pad
    out = []
    y = pad + header(out, pad, pad, inner, s, now, compact=True) + 4
    y += kpi_strip(out, pad, y, inner, s, cols=2) + GAP
    for draw, h in ((activity, 200), (languages, 220), (lambda *a: repos(*a, lang_col=False), 232), (mix, 214)):
        draw(out, pad, y, inner, h, s)
        y += h + GAP
    y += footer(out, pad, y - 4, now, ["Source: GitHub GraphQL API · public data only",
                                       f"generated {now:%Y-%m-%d %H:%M} UTC · dashboard.yml"]) - 4
    return W, y, out


def alt_title(s):
    return (f"{s['name']} GitHub analytics: {fmt(s['contributions'])} contributions in the past year, "
            f"current streak {s['current']} days (longest {s['longest']}), {fmt(s['commits'])} commits, "
            f"{fmt(s['prs'])} pull requests, {fmt(s['stars'])} stars, top language "
            f"{s['langs'][0][0] if s['langs'] else 'n/a'}")


def to_svg(W, H, out, title, theme):
    """theme 'light'/'dark' bakes colors in; 'auto' bakes light and overrides via prefers-color-scheme."""
    body = "\n".join(out)
    t = THEMES["light" if theme == "auto" else theme]
    style = ""
    if theme == "auto":
        swaps = {}
        for k, light in THEMES["light"].items():
            assert swaps.setdefault(light, THEMES["dark"][k]) == THEMES["dark"][k], k  # token hexes map 1:1
        rules = " ".join(f'[fill="{a}"]{{fill:{b}}} [stroke="{a}"]{{stroke:{b}}}' for a, b in swaps.items())
        style = f"<style>@media (prefers-color-scheme: dark) {{ {rules} }}</style>\n"
    body = f'<rect width="{W}" height="{H:.0f}" rx="16" fill="{{canvas}}"/>\n' + body
    for k, v in t.items():
        body = body.replace("{" + k + "}", v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H:.0f}" viewBox="0 0 {W} {H:.0f}" '
            f'role="img" aria-label="{escape(title)}" font-family="{FONT}" '
            f'style="font-variant-numeric: tabular-nums">\n<title>{escape(title)}</title>\n{style}{body}\n</svg>\n')


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
    now = dt.datetime.now(dt.timezone.utc)
    title = alt_title(s)
    os.makedirs(out_dir, exist_ok=True)
    files = {"dashboard-light.svg": (desktop, "light"), "dashboard-dark.svg": (desktop, "dark"),
             "dashboard-mobile.svg": (mobile, "auto")}
    for name, (layout, theme) in files.items():
        with open(os.path.join(out_dir, name), "w") as f:
            f.write(to_svg(*layout(s, now), title, theme))
    print(title)
