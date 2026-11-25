"""The public documents must match the code, the record files and each other."""

import ast
import functools
import html
import json
import re
import struct
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PAGE = DOCS / "index.html"
GUIDES = ("getting-started", "model-sources", "project-setup", "sessions", "troubleshooting", "security-and-data", "reference")
GUIDE_FILES = ["docs/README.md", *(f"docs/{name}.md" for name in GUIDES)]
POLICY = ["SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"]
PUBLIC = ["README.md", *POLICY, "CHANGELOG.md", *GUIDE_FILES]
NOTES = "docs/launch-posts.md"
# Files written by later documentation packages: a link to one of them may point ahead until it exists.
PENDING = {"CHANGELOG.md", *GUIDE_FILES}

IMAGES = ("guide-computer", "guide-model", "launch", "project-setup", "overview", "attach", "terminal", "run-summary", "recent-runs", "folder-default", "settings-places", "machines")
IMAGE_FILES = {f"docs/images/{name}{theme}.png" for name in IMAGES for theme in ("", "-dark")}
OLD_IMAGES = ("launcher.png", "launcher-light.png", "workbench.png", "project-launch.png", "project-terminal.png", "ssh-machine.png")
IMAGE_BUDGET = 400 * 1024
PAGE_BUDGET = 80 * 1024

CHECKS = {
    "unit-tests": "Unit tests",
    "desktop-smoke": "Desktop smoke test",
    "ci": "Continuous integration job on this system",
    "install-script": "Install with the install script",
    "install-manual": "Install by hand from a browser download",
    "first-start": "First start (Python packages and desktop runtime)",
    "guided-setup": "Guided setup to a running agent",
    "engine-install": "Managed llama.cpp install",
    "model-download": "Recommended model download with checksum",
    "model-serve": "Local model start and stop",
    "attach-ollama": "Attach Ollama",
    "attach-lmstudio": "Attach LM Studio",
    "attach-other": "Attach another local server",
    "hosted": "Session with a hosted provider",
    "ssh-pair": "Pair and check an SSH machine",
    "ssh-managed": "Managed model on an SSH machine",
    "ssh-attach": "Attach a server on an SSH machine",
    "agent-omp": "OMP session answered",
    "agent-pi": "Pi session answered",
    "agent-claude-code": "Claude Code session answered",
    "agent-codex": "Codex session answered",
    "agent-hermes": "Hermes session answered",
    "agent-opencode": "opencode session answered",
    "agent-window": "Agent in a native terminal window",
    "terminal-dock": "Terminal dock: open, type, close",
    "sessions-resume": "Resume a recent session",
    "project-setup": "Project setup: plan and apply",
    "starter-kit": "Starter kit applied",
    "doctor": "Doctor",
    "web-tools": "Web search and page crawling",
    "sandbox": "Docker sandbox",
    "network-first-start": "Network capture of a first start",
}
MAC_GATE = ("unit-tests", "desktop-smoke", "ci", "install-script", "install-manual", "first-start", "engine-install", "model-serve", "agent-window", "terminal-dock", "doctor")
PLATFORM_NAMES = {"linux": "Linux", "macos": "macOS", "windows": "Windows"}

SECTIONS = ["install", "folders", "how", "features", "kits", "runs", "portable", "tested", "status", "privacy", "fit", "faq"]
HEADINGS = [
    ("h1", "Pick where your model runs and which coding agent you use, remembered per project folder."),
    ("h2", "Install"),
    ("h3", "Linux x64"),
    ("h3", "macOS on Apple Silicon (preview)"),
    ("h3", "Windows x64"),
    ("h2", "One folder, one setup"),
    ("h2", "How it works"),
    ("h3", "Run the setup guide"),
    ("h3", "Choose where the model runs"),
    ("h3", "Launch the agent in your folder"),
    ("h2", "What it does"),
    ("h3", "Model sources"),
    ("h3", "Project setup you can read first"),
    ("h3", "Terminals and sessions"),
    ("h3", "The right API for each agent"),
    ("h3", "Doctor"),
    ("h3", "Measure your own setup"),
    ("h3", "Inkling 2 Mini with Unsloth's build"),
    ("h3", "Roblox Studio in the Game development kit"),
    ("h3", "Claude Code and Codex on local Qwen models"),
    ("h2", "Starter kits"),
    ("h3", "General software"),
    ("h3", "Web front end"),
    ("h3", "Python and data"),
    ("h3", "3D scenes and assets"),
    ("h3", "Research and writing"),
    ("h3", "DevOps and infrastructure"),
    ("h3", "Game development"),
    ("h3", "Blank"),
    ("h2", "Run summaries"),
    ("h3", "What a summary holds"),
    ("h3", "What you can do with it"),
    ("h2", "Settings that travel with the folder"),
    ("h3", "From one machine to the next"),
    ("h3", "Where settings live"),
    ("h2", "Tested on real machines"),
    ("h3", "Test suites on three systems"),
    ("h3", "Memory each model used"),
    ("h3", "Agents that wrote a file"),
    ("h2", "What was run for 0.5.0"),
    ("h2", "What leaves your computer"),
    ("h3", "No telemetry, no account"),
    ("h3", "Keys stay in your credential vault"),
    ("h3", "Checked downloads, per-user install"),
    ("h3", "The agents themselves"),
    ("h2", "What it is not"),
    ("h3", "Use something else when"),
    ("h3", "Workbench fits when you want to"),
    ("h2", "Questions"),
]
QUESTIONS = [
    "What does it cost, and what are you selling?",
    "Does it keep per-folder config in plain files?",
    "Can I rerun an old task after the folder's setup has changed?",
    "I already use Ollama or LM Studio. What does this add?",
    "Do I need a GPU?",
    "Which agents can it start, and with which models?",
    "The download is not signed. How do I know it is the real one?",
    'What does "preview" mean for macOS?',
    "Where do my keys and data live?",
    "Why Electron?",
    "Where are the benchmark numbers?",
    "Does it run several agents in parallel or manage git worktrees?",
    "How do I remove it?",
    "Something does not work. Where do I start?",
]
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
README_HEADINGS = [
    "Is this for you?",
    "Install",
    "What leaves your computer",
    "Tested configurations",
    "First run",
    "Run summaries",
    "Where settings live",
    "Guides",
    "Command line",
    "Building from source",
    "Licence and credits",
]
SECURITY_HEADINGS = [
    "Supported versions",
    "Reporting a vulnerability",
    "Security model",
    "Outbound hosts",
    "How downloads are verified",
    "Local data and credentials",
    "Stopping what Workbench started",
]
CONTRIBUTING_HEADINGS = [
    "Before you start",
    "Set up from source",
    "Tests",
    "Rules for code",
    "Documentation claims",
    "Reports from your hardware",
    "Changes made with a coding agent",
    "Licence of contributions",
    "Releasing (maintainer)",
]
CONDUCT_HEADINGS = ["Expected", "Not acceptable", "Where it applies", "Reporting", "Enforcement"]
CHANGELOG_HEADINGS = ["Added", "Changed", "Fixed", "Security", "Removed", "Known issues"]

CLAIM_BANS = (
    r"\b(the|a) (first|only) (app|tool|desktop app|launcher|product|project|one)\b",
    r"\b(first|only) of its kind\b",
    r"\bonly (app|tool|launcher|desktop app) (that|to|which)\b",
    r"\b(ultimate|world-class|best-in-class|blazing|lightning[- ]fast|revolutionary|seamless(ly)?|effortless(ly)?|"
    r"game[- ]chang\w+|cutting[- ]edge|state[- ]of[- ]the[- ]art|unmatched|unrivall?ed|fastest|most (powerful|advanced))\b",
    r"\bsimply\b",
)
NUMBER_BANS = (
    r"\d[\d.,]*\s*(tok(en)?s?\s*/\s*s(ec)?|tokens per second|t/s)\b",
    r"\b\d+\s*/\s*89\b",
    r"\b\d+(\.\d+)?\s*x faster\b",
)
EVIDENCE_WORDS = (
    r"\b(tested|verified|validated|confirmed) (on|with|against|under) (Windows|Linux|macOS|Ubuntu|Debian|Fedora|WSL)"
    r"|\bworks on (Windows|Linux|macOS)\b"
)
TRACKERS = (
    r"posthog|sentry|segment\.(io|com)|google-analytics|googletagmanager|gtag\(|mixpanel|amplitude|bugsnag|datadoghq"
    r"|autoUpdater|electron-updater|update-electron-app|crashReporter\.start"
)
PLACEHOLDERS = r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b|example\.(com|org)|you@|<your[- ]"
ATTRIBUTION = r"Co-Authored-By|Generated with|noreply@anthropic|\bAnthropic's Claude\b|\bChatGPT wrote\b|\U0001F916"
PERSONAL = r"/Users/[A-Za-z]|/home/[a-z]|[A-Za-z]:\\Users\\|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]{2,}\b"
MISSPELT = ("OpenCode", "Opencode", "Open Code", "Claude code", "ClaudeCode", "Oh My Pi", "oh-my-pi", "Local Agent Workbench", "AgentWorkbench")
IGNORED_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "www.w3.org", "example.org", "example.com", "mcp.example.com", "private.invalid", "playwright-service", "key"}
INTERNAL_VARIABLES = {"WORKBENCH_TEST_CLIPBOARD", "WORKBENCH_ELECTRON", "WORKBENCH_CALLER_CWD", "WORKBENCH_PATH_ADDED", "WORKBENCH_SESSION_KEY"}
SKIP_DIRS = {".git", "node_modules", "private", "results", "vendor", "__pycache__", ".venv", ".venv-linux", ".local", "dist"}


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def exists(rel):
    return (ROOT / rel).is_file()


@functools.lru_cache(maxsize=None)
def site():
    return json.loads(read("docs/site.json"))


@functools.lru_cache(maxsize=None)
def record():
    return json.loads(read("docs/verification.json"))


def repo_url():
    return "https://github.com/" + site()["repo"]


def strip_code(markdown):
    """Markdown without fenced code blocks and inline code spans."""
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", markdown, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", "", text)


def prose(markdown):
    text = strip_code(markdown)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"<[^>\n]+>", "", text)
    return text


def headings(markdown):
    return [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", markdown, flags=re.S | re.M), re.M)]


def slug(title):
    title = re.sub(r"[*_`]", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title))
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def anchors(markdown):
    seen, out = {}, set()
    for _, title in headings(markdown):
        base = slug(title)
        count = seen.get(base, 0)
        out.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    out |= set(re.findall(r'<a[^>]*\bid="([^"]+)"', markdown))
    return out


def links(markdown):
    text = strip_code(markdown)
    found = [m.group(1) for m in re.finditer(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", text)]
    found += re.findall(r'<(?:img|source)[^>]*\b(?:src|srcset)="([^"]+)"', text)
    return found


def image_links(markdown):
    text = strip_code(markdown)
    found = [(m.group(2), m.group(1)) for m in re.finditer(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)", text)]
    for tag in re.findall(r"<img[^>]*>", text):
        src = re.search(r'\bsrc="([^"]+)"', tag)
        alt = re.search(r'\balt="([^"]*)"', tag)
        found.append((src.group(1) if src else "", alt.group(1) if alt else ""))
    found += [(src, "picture source") for src in re.findall(r'<source[^>]*\bsrcset="([^"]+)"', text)]
    return found


def code_blocks(markdown):
    return [m.group(2) for m in re.finditer(r"^(```|~~~)[^\n]*\n(.*?)^\1[^\n]*$", markdown, flags=re.S | re.M)]


def section(markdown, title, level=2):
    """The text under the heading of that level and title, up to the next heading of the same or a higher level."""
    lines, out, inside = markdown.splitlines(), [], False
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m and len(m.group(1)) <= level:
            if inside:
                break
            inside = len(m.group(1)) == level and m.group(2).strip() == title
            continue
        if inside:
            out.append(line)
    return "\n".join(out) if inside or out else None


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.ids, self.css, self.text, self.json_ld = [], [], "", [], []
        self.headings, self.summaries, self.pre, self.eyebrow = [], [], [], ""
        self.labels, self.status, self.panels, self.descs = {}, [], {}, []
        self.stack, self.open = [], []

    def section_id(self):
        for name, attrs in reversed(self.stack):
            if name == "section" and attrs.get("id"):
                return attrs["id"]
        return ""

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        self.tags.append((tag, attrs))
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag in VOID:
            return
        self.stack.append((tag, attrs))
        classes = attrs.get("class", "").split()
        kinds = []
        if tag in ("h1", "h2", "h3"):
            kinds.append(("heading", tag, self.section_id()))
        if tag == "summary":
            kinds.append(("summary", self.section_id()))
        if "eyebrow" in classes:
            kinds.append(("eyebrow",))
        if tag == "label":
            kinds.append(("label", attrs.get("for", "")))
        if "status" in classes:
            kinds.append(("status",))
        if tag == "div" and "panel" in classes:
            kinds.append(("panel", attrs.get("id", "")))
        if tag == "desc":
            kinds.append(("desc",))
        for kind in kinds:
            self.open.append([kind, len(self.stack), tag, []])

    def handle_startendtag(self, tag, attrs):
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        self.tags.append((tag, attrs))
        if attrs.get("id"):
            self.ids.append(attrs["id"])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for item in [o for o in self.open if o[1] == len(self.stack) and o[2] == tag]:
            self.open.remove(item)
            self.finish(item[0], " ".join("".join(item[3]).split()))
        while self.stack:
            name, _ = self.stack.pop()
            if name == tag:
                break

    def finish(self, kind, text):
        if kind[0] == "heading":
            self.headings.append((kind[1], text, kind[2]))
        elif kind[0] == "summary":
            self.summaries.append((kind[1], text))
        elif kind[0] == "eyebrow":
            self.eyebrow = text
        elif kind[0] == "label":
            self.labels[kind[1]] = text
        elif kind[0] == "status":
            self.status.append(text)
        elif kind[0] == "panel":
            self.panels[kind[1]] = text
        elif kind[0] == "desc":
            self.descs.append(text)

    def handle_data(self, data):
        names = [name for name, _ in self.stack]
        if names and names[-1] == "style":
            self.css += data
            return
        if names and names[-1] == "script":
            attrs = self.stack[-1][1]
            if attrs.get("type") == "application/ld+json":
                self.json_ld.append(data)
            return
        for item in self.open:
            item[3].append(data)
        if "svg" in names and names[-1] in ("title", "desc"):
            return
        if names and names[-1] == "title" and "head" in names:
            return
        self.text.append(data)


@functools.lru_cache(maxsize=None)
def page():
    parsed = Page()
    raw = PAGE.read_text(encoding="utf-8")
    parsed.feed(raw)
    parsed.close()
    parsed.pre = [m for m in (html.unescape(re.sub(r"<[^>]+>", "", block)) for block in re.findall(r"<pre[^>]*>(.*?)</pre>", raw, re.S))]
    parsed.text = " ".join(" ".join(parsed.text).split())
    return parsed


def tokens(css):
    pair = r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;"
    light = re.search(r":root\s*\{(.*?)\}", css, re.S)
    dark = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", css, re.S)
    return (
        dict(re.findall(pair, light.group(1))) if light else {},
        dict(re.findall(pair, dark.group(1))) if dark else {},
    )


def contrast(a, b):
    def luminance(colour):
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def png_size(path):
    data = Path(path).read_bytes()[:24]
    return struct.unpack(">II", data[16:24])


def sources():
    patterns = (
        "*.py", "*.sh", "*.ps1", "*.cmd", "*.command", "desktop/*.cjs", "desktop/*.js", "desktop/ui/*.js",
        "desktop/actions.json", "desktop/index.html", "sandbox/Dockerfile", "firecrawl.compose.yml",
        "kits/*/kit.json", "model_catalog.json", "release/build.py", "evals/harbor/*.py",
    )
    found = []
    for pattern in patterns:
        found += [p for p in sorted(ROOT.glob(pattern)) if p.is_file() and p not in found]
    return found


def hosts(text):
    return {h.lower() for h in re.findall(r"https?://([A-Za-z0-9.-]+)", text)}


def passed(platform):
    systems = {s["id"] for s in record()["systems"] if s["platform"] == platform}
    return {r["check"] for r in record()["results"] if r["system"] in systems and r["status"] == "passed"}


def platform_has_rows(platform):
    systems = {s["id"] for s in record()["systems"] if s["platform"] == platform}
    return any(r["system"] in systems for r in record()["results"])


def mac_gate_met():
    return set(MAC_GATE) <= passed("macos")


def status_sentence(platform):
    version = site()["version"]
    name = PLATFORM_NAMES[platform]
    if platform == "macos":
        if mac_gate_met():
            return f"Status for {version}: the macOS checks listed in the README passed on Apple Silicon. Intel Macs are not supported."
        return f"Status for {version}: preview. The macOS checks listed in the README have not all passed. Intel Macs are not supported."
    if platform_has_rows(platform):
        return f"Status for {version}: some checks were run on {name}. The tested-configurations table says which."
    return f"Status for {version}: not run on {name}. It is expected to work; please report what you find."


def opencode_ships():
    source = "providers.py" if exists("providers.py") else "connections.py"
    return '"opencode"' in read(source)


def kit_labels():
    return {json.loads(p.read_text(encoding="utf-8"))["label"] for p in sorted((ROOT / "kits").glob("*/kit.json"))}


def interface_text():
    parts = []
    for pattern in ("desktop/index.html", "desktop/*.js", "desktop/ui/*.js", "doctor.py", "tooling.py"):
        parts += [html.unescape(p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob(pattern)) if p.is_file()]
    return "\n".join(parts)


def claim_problems(name, text, evidence_allowed=False):
    """One sentence per broken wording rule in this text, which is already stripped of code and link targets."""
    problems = []
    for pattern in CLAIM_BANS:
        for m in re.finditer(pattern, text, re.I):
            problems.append(f'{name} says "{m.group(0)}"; describe what it does instead of using a superlative or "simply".')
    for pattern in NUMBER_BANS:
        for m in re.finditer(pattern, text, re.I):
            problems.append(f'{name} quotes "{m.group(0)}"; speed and score figures belong in MEASUREMENTS.md only.')
    if not evidence_allowed:
        for m in re.finditer(EVIDENCE_WORDS, text, re.I):
            problems.append(f'{name} says "{m.group(0)}"; evidence words belong in the README tested-configurations table and CHANGELOG.md.')
    for m in re.finditer(PLACEHOLDERS, text):
        problems.append(f'{name} contains the placeholder "{m.group(0)}"; write the real text.')
    for m in re.finditer(ATTRIBUTION, text, re.I):
        problems.append(f'{name} contains "{m.group(0)}"; the only author is James Arslan, so remove tool attribution.')
    for m in re.finditer(PERSONAL, text):
        problems.append(f'{name} contains the personal path or address "{m.group(0)}"; remove it.')
    for line in text.splitlines():
        if "!" in line:
            problems.append(f'{name} has an exclamation mark in "{line.strip()[:60]}"; write it as a plain sentence.')
    for word in MISSPELT:
        if word in text:
            problems.append(f'{name} spells a name "{word}"; use the exact names Agent Workbench, OMP, Claude Code and opencode.')
    if not opencode_ships() and "opencode" in text:
        problems.append(f"{name} names opencode, which does not ship; remove it (docs-site.md 10.8).")
    return problems


def link_problems(rel, check_readme=False):
    """Broken relative links and anchors in one Markdown file."""
    problems = []
    text = read(rel)
    own = anchors(text)
    for target in links(text):
        if re.match(r"[a-z]+:", target) or target.startswith("//"):
            continue
        path, _, fragment = target.partition("#")
        if not path:
            if fragment and fragment not in own:
                problems.append(f"{rel} links to #{fragment}, which has no such heading; fix the link or add the heading.")
            continue
        dest = (ROOT / rel).parent / path
        dest_rel = dest.resolve().relative_to(ROOT).as_posix() if dest.resolve().is_relative_to(ROOT) else str(dest)
        if not dest.exists():
            if dest_rel in PENDING:
                continue
            problems.append(f"{rel} links to {target}, which does not exist; fix the link or add the file.")
            continue
        if fragment and dest.suffix == ".md" and (dest_rel != "README.md" or check_readme):
            if fragment not in anchors(dest.read_text(encoding="utf-8")):
                problems.append(f"{rel} links to {target}, which has no such heading; fix the link or add the heading.")
    return problems


def repository_problems(name, text):
    problems = []
    owner, repo = site()["repo"].split("/")
    for m in re.finditer(r"github\.com/" + re.escape(owner) + r"/([A-Za-z0-9._-]+)", text):
        if m.group(1) != repo:
            problems.append(f'{name} names the repository "{owner}/{m.group(1)}"; docs/site.json says "{site()["repo"]}". Use that name.')
    for m in re.finditer(r"https?://[A-Za-z0-9.-]+\.github\.io/[^\s\"')<>]*", text):
        if not m.group(0).startswith(site()["site"]):
            problems.append(f"{name} links {m.group(0)}; the site address in docs/site.json is {site()['site']}.")
    for m in re.finditer(re.escape(repo_url()) + r"/releases/(?:download|tag)/([^/\s\"')<>]+)", text):
        if m.group(1) != site()["tag"]:
            problems.append(f"{name} names the release {m.group(1)}; docs/site.json says {site()['tag']}.")
    for m in re.finditer(re.escape(repo_url()) + r"/(blob|tree)/([^/\s\"')<>]+)", text):
        if m.group(2) != site()["branch"]:
            problems.append(f"{name} links the branch {m.group(2)}; docs/site.json says {site()['branch']}.")
    return problems


def referenced_images(rel):
    """(path relative to the repository, alt text) for every image a Markdown file references."""
    out = []
    for target, alt in image_links(read(rel)):
        if re.match(r"[a-z]+:", target):
            out.append((target, alt))
            continue
        dest = ((ROOT / rel).parent / target.split("#")[0]).resolve()
        out.append((dest.relative_to(ROOT).as_posix() if dest.is_relative_to(ROOT) else str(dest), alt))
    return out


def page_images():
    out = []
    for tag, attrs in page().tags:
        if tag == "img":
            out.append(("docs/" + attrs.get("src", ""), attrs.get("alt", "")))
        elif tag == "source":
            out.append(("docs/" + attrs.get("srcset", ""), "picture source"))
        elif tag == "a" and attrs.get("href", "").endswith(".png"):
            out.append(("docs/" + attrs["href"], "link"))
    return out


def image_problems(name, images):
    problems = []
    for path, alt in images:
        if Path(path).name in OLD_IMAGES:
            problems.append(f"{name} shows {path}, a screenshot of the 0.3 interface; use an image from docs/images/.")
        elif path not in IMAGE_FILES:
            problems.append(f"{name} references {path}, which is not one of the screenshots in IMAGE_FILES; use one of those names.")
        if not alt.strip():
            problems.append(f"{name} shows {path} without alt text; describe what the image shows.")
    return problems


def tested_table_rows(readme):
    body = section(readme, "Tested configurations") or ""
    rows = [line for line in body.splitlines() if line.strip().startswith("|")]
    return [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]


class RecordTests(unittest.TestCase):
    def test_site_file(self):
        data = site()
        self.assertEqual(set(data), {"version", "tag", "repo", "branch", "site"}, msg="docs/site.json must hold exactly version, tag, repo, branch and site.")
        self.assertEqual(data["version"], "0.5.0", msg="docs/site.json version must be 0.5.0.")
        self.assertEqual(data["tag"], "v" + data["version"], msg="docs/site.json tag must be v plus the version.")
        self.assertRegex(data["repo"], r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", msg="docs/site.json repo must be owner/name.")
        self.assertTrue(data["site"].startswith("https://") and data["site"].endswith("/"), msg="docs/site.json site must be an https address ending in /.")

    @unittest.skipUnless((ROOT / ".git").exists(), "release kits leave out docs/.nojekyll; only the repository's docs/ is served by GitHub Pages")
    def test_pages_marker(self):
        self.assertTrue((DOCS / ".nojekyll").is_file(), msg="docs/.nojekyll is missing; GitHub Pages would run Jekyll over the guides.")

    def test_verification_record(self):
        data = record()
        self.assertEqual(data.get("schema"), 1, msg="docs/verification.json schema must be 1.")
        self.assertEqual(data.get("version"), site()["version"], msg="docs/verification.json version must equal docs/site.json version.")
        self.assertRegex(data.get("updated", ""), r"^\d{4}-\d{2}-\d{2}$", msg="docs/verification.json updated must be YYYY-MM-DD.")
        ids = [s["id"] for s in data["systems"]]
        self.assertEqual(len(ids), len(set(ids)), msg="docs/verification.json has a system id twice; make each id unique.")
        for system in data["systems"]:
            self.assertRegex(system["id"], r"^[a-z0-9-]{1,40}$", msg=f"docs/verification.json system id {system['id']} must match [a-z0-9-]{{1,40}}.")
            self.assertIn(system["platform"], PLATFORM_NAMES, msg=f"docs/verification.json system {system['id']} has an unknown platform.")
            self.assertTrue(system["label"].strip(), msg=f"docs/verification.json system {system['id']} needs a label.")
        pairs = set()
        for row in data["results"]:
            where = f"docs/verification.json row {row.get('system')}/{row.get('check')}"
            self.assertIn(row["system"], ids, msg=f"{where} names a system that is not listed; add the system first.")
            self.assertIn(row["check"], CHECKS, msg=f"{where} uses a check id that is not in CHECKS; use one of the catalogue ids.")
            self.assertIn(row["status"], ("passed", "failed", "partial"), msg=f"{where} status must be passed, failed or partial.")
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$", msg=f"{where} date must be YYYY-MM-DD.")
            self.assertTrue(row["evidence"].strip() and len(row["evidence"]) <= 200 and "\n" not in row["evidence"], msg=f"{where} evidence must be one line of at most 200 characters.")
            self.assertLessEqual(len(row.get("note", "")), 200, msg=f"{where} note must be at most 200 characters.")
            self.assertNotIn((row["system"], row["check"]), pairs, msg=f"{where} appears twice; keep one row per system and check.")
            pairs.add((row["system"], row["check"]))
        problems = claim_problems("docs/verification.json", read("docs/verification.json"), evidence_allowed=True)
        problems += claim_problems("docs/site.json", read("docs/site.json"))
        self.assertEqual(problems, [], msg="\n".join(problems))


class LinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        problems = []
        for rel in [*POLICY, NOTES]:
            if exists(rel):
                problems += link_problems(rel)
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_repository_name_is_spelled_one_way(self):
        files = [rel for rel in [*POLICY, "CHANGELOG.md", *GUIDE_FILES, NOTES, "docs/index.html", "install.sh", "install.ps1", "release/build.py", "release/README.md", ".github/ISSUE_TEMPLATE/config.yml"] if exists(rel)]
        problems = []
        for rel in files:
            problems += repository_problems(rel, read(rel))
        self.assertEqual(problems, [], msg="\n".join(problems))
        for rel, pattern in (("install.sh", r'^REPOSITORY="([^"]+)"'), ("install.ps1", r"^\$Repository = '([^']+)'"), ("release/build.py", r'^REPOSITORY = "([^"]+)"')):
            if exists(rel):
                m = re.search(pattern, read(rel), re.M)
                self.assertTrue(m and m.group(1) == site()["repo"], msg=f"{rel} must name the repository {site()['repo']} exactly as docs/site.json does.")

    def test_page_links_to_repository_files_exist(self):
        problems = []
        prefix = repo_url() + "/blob/" + site()["branch"] + "/"
        for tag, attrs in page().tags:
            href = attrs.get("href", "") if tag == "a" else ""
            if href.startswith(prefix):
                path, _, fragment = href[len(prefix) :].partition("#")
                if not exists(path):
                    if path not in PENDING:
                        problems.append(f"docs/index.html links to {path}, which does not exist; fix the link or add the file.")
                elif fragment and path.endswith(".md") and path != "README.md" and fragment not in anchors(read(path)):
                    problems.append(f"docs/index.html links to {path}#{fragment}, which has no such heading; fix the link or add the heading.")
            elif href.startswith(repo_url() + "/tree/" + site()["branch"] + "/"):
                path = href.split("/tree/" + site()["branch"] + "/", 1)[1]
                if not (ROOT / path).is_dir():
                    problems.append(f"docs/index.html links to the folder {path}, which does not exist.")
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_version_is_the_same_everywhere(self):
        version = site()["version"]
        self.assertIn(f"Version {version}", page().eyebrow, msg="docs/index.html: the hero eyebrow must start with the version from docs/site.json.")
        self.assertEqual(record()["version"], version, msg="docs/verification.json version must equal docs/site.json.")
        self.assertEqual(json.loads(read("desktop/package.json"))["version"], version, msg="desktop/package.json version must equal docs/site.json.")
        for rel, pattern in (("install.sh", r'^VERSION="([^"]+)"'), ("install.ps1", r"^\$Version = '([^']+)'"), ("release/build.py", r'^VERSION = "([^"]+)"')):
            if exists(rel):
                m = re.search(pattern, read(rel), re.M)
                self.assertTrue(m and m.group(1) == version, msg=f"{rel} must state version {version}.")


class ClaimTests(unittest.TestCase):
    def test_policy_files_and_notes_follow_the_wording_rules(self):
        problems = []
        for rel in POLICY:
            problems += claim_problems(rel, prose(read(rel)))
        if exists(NOTES):
            problems += claim_problems(NOTES, prose(read(NOTES)), evidence_allowed=True)
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_page_follows_the_wording_rules(self):
        problems = claim_problems("docs/index.html", page().text)
        self.assertEqual(problems, [], msg="\n".join(problems))
        self.assertTrue(any(attrs.get("href", "").endswith("MEASUREMENTS.md") for tag, attrs in page().tags if tag == "a"), msg="docs/index.html must link MEASUREMENTS.md for the numbers it does not show.")

    def test_no_secrets_in_public_documents(self):
        patterns = (
            r"https?://[^/\s)\"'<>]*@",
            r"https?://[^\s)\"'<>]*\?[^\s)\"'<>]*(key|token|secret|password|sig)=",
            r"\bsk-[A-Za-z0-9_-]{16,}",
            r"\b(ghp|gho|github_pat|hf|xox[bp])_[A-Za-z0-9_]{16,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}",
        )
        problems = []
        for rel in [*PUBLIC, "docs/index.html"]:
            if exists(rel):
                for pattern in patterns:
                    for m in re.finditer(pattern, read(rel)):
                        problems.append(f'{rel} contains "{m.group(0)[:40]}", which looks like a key or a credential in an address; documents show variable names, never values.')
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_no_speed_figures_outside_measurements(self):
        problems = []
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.suffix not in (".md", ".html") or not path.is_file() or set(rel.parts) & SKIP_DIRS or rel.as_posix() == "MEASUREMENTS.md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in NUMBER_BANS:
                for m in re.finditer(pattern, text, re.I):
                    problems.append(f'{rel.as_posix()} quotes "{m.group(0)}"; speed figures belong in MEASUREMENTS.md only (D21).')
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_no_telemetry_code(self):
        problems = []
        for path in sources():
            for m in re.finditer(TRACKERS, path.read_text(encoding="utf-8", errors="replace"), re.I):
                problems.append(
                    f"{path.relative_to(ROOT).as_posix()} contains {m.group(0)}; Agent Workbench ships no telemetry, analytics, crash "
                    "reporting or self-update (rule 2). Remove it or change the claim in README.md, SECURITY.md and docs/index.html."
                )
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_harness_names(self):
        names = re.findall(r'dict\(id="([^"]+)", cli=', read("providers.py")) if exists("providers.py") else []
        for name in names:
            self.assertIn(name, page().text, msg=f"docs/index.html does not name the agent {name}; list every agent in providers.HARNESSES.")
        if opencode_ships():
            self.assertIn("opencode", page().text, msg="docs/index.html must name opencode while it ships.")

    def test_macos_is_preview_until_its_checks_pass(self):
        if mac_gate_met():
            self.skipTest("Every macOS check in MAC_GATE has passed.")
        p = page()
        self.assertIn("preview", p.eyebrow, msg="docs/index.html: the eyebrow must call macOS a preview until MAC_GATE passes.")
        self.assertIn("preview", p.labels.get("os-macos", ""), msg="docs/index.html: the macOS tab label must say preview until MAC_GATE passes.")
        heading = [text for tag, text, _ in p.headings if tag == "h3" and text.startswith("macOS")]
        self.assertTrue(heading and "preview" in heading[0], msg="docs/index.html: the macOS panel heading must say preview until MAC_GATE passes.")

    def test_status_sentences_match_the_record(self):
        expected = [status_sentence("linux"), status_sentence("macos"), status_sentence("windows")]
        self.assertEqual(page().status, expected, msg="docs/index.html: the status sentences of the install tabs must be recomputed from docs/verification.json (docs-site.md 2.3).")

    def test_kits_match(self):
        if not (ROOT / "kits").is_dir():
            self.skipTest("No kits folder.")
        cards = {text for tag, text, where in page().headings if tag == "h3" and where == "kits"}
        self.assertEqual(cards, kit_labels(), msg="docs/index.html: the cards under Starter kits must be the label of every kits/*/kit.json.")


class LandingPageTests(unittest.TestCase):
    def test_page_loads_nothing_from_elsewhere(self):
        p = page()
        for tag, attrs in p.tags:
            if tag == "script":
                self.assertEqual(attrs.get("type"), "application/ld+json", msg="docs/index.html: only non-executable application/ld+json script blocks are allowed.")
                self.assertNotIn("src", attrs, msg="docs/index.html: JSON-LD script must not load an external src.")
            else:
                self.assertNotIn(tag, ("iframe", "object", "embed", "video", "audio", "form"), msg=f"docs/index.html contains a <{tag}>; the page must work without it and load nothing.")
            for name in attrs:
                self.assertFalse(name.startswith("on"), msg=f"docs/index.html has an {name} attribute; the page has no script.")
                self.assertNotEqual(name, "style", msg="docs/index.html has a style attribute; put the rule in the style element.")
            if tag == "link":
                self.assertIn(attrs.get("rel"), ("canonical", "icon", "alternate"), msg=f"docs/index.html has a link rel={attrs.get('rel')}; only canonical, icon and alternate are allowed.")
                if attrs.get("rel") == "icon":
                    self.assertTrue(attrs.get("href", "").startswith("data:"), msg="docs/index.html: the icon must be a data: address.")
                if attrs.get("rel") == "alternate":
                    self.assertEqual(attrs.get("href"), "llms.txt", msg="docs/index.html: alternate link must point to local llms.txt.")
            for name in ("src", "srcset", "poster", "data"):
                value = attrs.get(name, "")
                if value:
                    self.assertFalse(re.match(r"[a-z]+:|//|/", value), msg=f"docs/index.html loads {value}; load only files beside the page.")
            if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy":
                self.assertEqual(attrs.get("content"), CSP, msg="docs/index.html: the Content-Security-Policy must be exactly the one in docs-site.md 10.1.")
        for raw_json in p.json_ld:
            parsed = json.loads(raw_json)
            self.assertIn("@context", parsed, msg="docs/index.html JSON-LD block needs @context.")
        self.assertTrue(any(tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy" for tag, attrs in p.tags), msg="docs/index.html needs its Content-Security-Policy meta element.")
        for pattern in ("@import", "@font-face", "url("):
            self.assertNotIn(pattern, p.css, msg=f"docs/index.html CSS contains {pattern}; the page loads no fonts or files from CSS.")
        self.assertLessEqual(PAGE.stat().st_size, PAGE_BUDGET, msg=f"docs/index.html is larger than {PAGE_BUDGET // 1024} KB; trim it.")

    def test_page_structure(self):
        p, raw = page(), PAGE.read_text(encoding="utf-8")
        self.assertRegex(raw, r'<html lang="en">', msg='docs/index.html: the html element needs lang="en".')
        self.assertEqual([(tag, text) for tag, text, _ in p.headings], HEADINGS, msg="docs/index.html headings differ from HEADINGS in tests/test_docs.py; change one to match the other.")
        levels = [int(tag[1]) for tag, _, _ in p.headings]
        self.assertTrue(all(b <= a + 1 for a, b in zip(levels, levels[1:])), msg="docs/index.html skips a heading level.")
        sections = [attrs["id"] for tag, attrs in p.tags if tag == "section" and attrs.get("id")]
        self.assertEqual(sections, SECTIONS, msg="docs/index.html section ids must be, in order: " + ", ".join(SECTIONS) + ".")
        for tag, attrs in p.tags:
            if tag == "section":
                self.assertTrue(attrs.get("aria-labelledby"), msg="docs/index.html: every section must be labelled by its heading.")
            for name in ("aria-labelledby", "for"):
                for ref in attrs.get(name, "").split():
                    self.assertIn(ref, p.ids, msg=f"docs/index.html: {name} names {ref}, which no element has.")
            href = attrs.get("href", "") if tag == "a" else ""
            if href.startswith("#") and href != "#":
                self.assertIn(href[1:], p.ids, msg=f"docs/index.html links to {href}, which no element has.")
            if tag == "svg" and attrs.get("role") != "img":
                self.assertEqual(attrs.get("aria-hidden"), "true", msg="docs/index.html: a decorative svg needs aria-hidden=\"true\".")
        self.assertEqual(len(p.ids), len(set(p.ids)), msg="docs/index.html uses an id twice.")
        first = next(attrs for tag, attrs in p.tags if tag == "a")
        self.assertEqual((first.get("class"), first.get("href")), ("skip", "#main"), msg="docs/index.html: the first link must be the skip link to #main.")
        nav = [attrs for tag, attrs in p.tags if tag == "nav"]
        self.assertTrue(nav and nav[0].get("aria-label") == "Sections", msg='docs/index.html: the navigation needs aria-label="Sections".')

    def test_install_tabs_work_without_script(self):
        p = page()
        inputs = [attrs for tag, attrs in p.tags if tag == "input"]
        self.assertEqual([a.get("id") for a in inputs], ["os-linux", "os-macos", "os-windows"], msg="docs/index.html needs exactly the three system radios os-linux, os-macos and os-windows.")
        self.assertTrue(all(a.get("type") == "radio" and a.get("name") == "os" for a in inputs), msg="docs/index.html: the system tabs must be radios named os.")
        self.assertEqual(sum("checked" in a for a in inputs), 1, msg="docs/index.html: exactly one system tab must be checked.")
        assets = {"linux": "agent-workbench-linux-x64.tar.gz", "macos": "agent-workbench-macos-arm64.tar.gz", "windows": "agent-workbench-windows-x64.zip"}
        for system, asset in assets.items():
            self.assertIn(f"os-{system}", p.labels, msg=f"docs/index.html: the radio os-{system} needs its label.")
            self.assertIn(f"#os-{system}:checked ~ #panel-{system}", p.css, msg=f"docs/index.html CSS must show #panel-{system} when its radio is checked.")
            panel = p.panels.get(f"panel-{system}", "")
            for needle in (asset, "SHA256SUMS"):
                self.assertIn(needle, panel, msg=f"docs/index.html: the {system} panel must name {needle}.")
        self.assertRegex(p.css, r"\.panel\s*\{[^}]*display:\s*none", msg="docs/index.html CSS must hide .panel by default.")

    def test_install_commands_match_the_installers(self):
        base = repo_url() + "/releases/latest/download/"
        posix = f"curl -fsSL {base}install.sh | sh"
        windows = f"irm {base}install.ps1 | iex"
        pre = [block.strip() for block in page().pre]
        for command in (posix, windows):
            self.assertIn(command, pre, msg=f"docs/index.html must show the one-line install command: {command}")
        self.assertIn(f"curl -fsSLO {base}install.sh\nless install.sh\nsh install.sh", pre, msg="docs/index.html must show how to download and read install.sh before running it.")
        self.assertTrue(any(block.startswith(f"irm {base}install.ps1 -OutFile install.ps1") for block in pre), msg="docs/index.html must show how to download and read install.ps1 before running it.")
        for command in ("sha256sum -c SHA256SUMS --ignore-missing", "shasum -a 256 -c SHA256SUMS --ignore-missing", "Get-FileHash -Algorithm SHA256 agent-workbench-windows-x64.zip"):
            self.assertTrue(any(command in block for block in pre), msg=f"docs/index.html must show the checksum command {command}.")
        if exists("release/build.py"):
            self.assertRegex(read("release/build.py"), r'INSTALLERS = \("install\.sh", "install\.ps1"\)', msg="release/build.py must publish install.sh and install.ps1 as release assets, which the page downloads.")
        if exists("install.sh"):
            self.assertIn('base="https://github.com/$REPOSITORY/releases/download/v$VERSION"', read("install.sh"), msg="install.sh must download the kit of its own pinned version.")

    def test_faq(self):
        self.assertEqual([text for where, text in page().summaries if where == "faq"], QUESTIONS, msg="docs/index.html FAQ questions differ from QUESTIONS in tests/test_docs.py; change one to match the other.")

    def test_ui_labels_exist_in_the_interface(self):
        interface = interface_text()
        labels = [html.unescape(re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r'<b class="ui">(.*?)</b>', PAGE.read_text(encoding="utf-8"), re.S)]
        missing = [label for label in labels if label not in interface]
        self.assertTrue(labels, msg='docs/index.html marks interface labels with <b class="ui">.')
        self.assertEqual(missing, [], msg='docs/index.html: these <b class="ui"> labels are not in the interface; use the label the app shows: ' + ", ".join(missing))

    def test_images(self):
        p = page()
        problems = image_problems("docs/index.html", page_images())
        self.assertEqual(problems, [], msg="\n".join(problems))
        for tag, attrs in p.tags:
            if tag == "img":
                self.assertTrue(attrs.get("alt", "").strip(), msg=f"docs/index.html: {attrs.get('src')} needs alt text.")
                self.assertTrue(attrs.get("width", "").isdigit() and attrs.get("height", "").isdigit(), msg=f"docs/index.html: {attrs.get('src')} needs integer width and height.")
        if not (DOCS / "images").is_dir():
            self.skipTest("docs/images/ does not exist yet; the screenshots are added later.")
        raw = PAGE.read_text(encoding="utf-8")
        for picture in re.findall(r"<picture>(.*?)</picture>", raw, re.S):
            files = re.findall(r'(?:srcset|src)="([^"]+)"', picture)
            sizes = {png_size(DOCS / f) for f in files}
            self.assertEqual(len(sizes), 1, msg=f"docs/index.html: the light and dark images {files} must have the same pixel size.")
            img = re.search(r'<img[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', picture, re.S)
            width, height = next(iter(sizes))
            self.assertAlmostEqual(int(img.group(1)) / int(img.group(2)), width / height, delta=0.02, msg=f"docs/index.html: width and height of {files[-1]} must have the image's proportions ({width}x{height}).")

    def test_theme_contrast(self):
        light, dark = tokens(page().css)
        self.assertEqual(set(light), set(dark), msg="docs/index.html: the dark theme must redefine every colour token of the light theme.")
        pairs = [(fg, bg, 4.5) for fg in ("text", "muted", "accent") for bg in ("bg", "surface", "raised", "code", "accent-soft")]
        pairs += [("accent-ink", "accent", 4.5), ("note-ink", "note-bg", 4.5), ("term-text", "term-bg", 4.5), ("term-muted", "term-bg", 4.5)]
        pairs += [("edge-strong", "bg", 3.0), ("edge-strong", "surface", 3.0)]
        for name, theme in (("light", light), ("dark", dark)):
            for fg, bg, least in pairs:
                self.assertGreaterEqual(contrast(theme[fg], theme[bg]), least, msg=f"docs/index.html {name} theme: --{fg} on --{bg} is below {least}:1.")
        for needle in ("prefers-color-scheme: dark", "prefers-reduced-motion", "forced-colors: active", ":focus-visible", "@media print"):
            self.assertIn(needle, page().css, msg=f"docs/index.html CSS must contain {needle}.")

    def test_diagrams_have_text_alternatives(self):
        p, raw = page(), PAGE.read_text(encoding="utf-8")
        for attrs in [attrs for tag, attrs in p.tags if tag == "svg" and attrs.get("role") == "img"]:
            refs = attrs.get("aria-labelledby", "").split()
            self.assertEqual(len(refs), 2, msg="docs/index.html: each svg diagram is labelled by its title and its desc.")
            for ref in refs:
                self.assertIn(ref, p.ids, msg=f"docs/index.html: the diagram label {ref} does not exist.")
        folders = re.search(r'<ol class="folders" aria-label="([^"]+)">(.*?)</ol>', raw, re.S)
        self.assertTrue(folders and folders.group(1).strip(), msg='docs/index.html: the folder examples are an <ol class="folders"> with an aria-label.')
        rows = re.findall(r"<li>(.*?)</li>", folders.group(2), re.S)
        self.assertEqual(len(rows), 3, msg="docs/index.html: the folder examples have three rows.")
        names = re.findall(r'dict\(id="([^"]+)", cli=', read("providers.py")) if exists("providers.py") else []
        for row in rows:
            pill = re.search(r'<span class="pill">(.*?)</span>\s*<span class="f-arrow"', row, re.S)
            agent = re.sub(r"<[^>]+>|^starts ", "", re.sub(r'<span class="vh">starts </span>', "", pill.group(1) if pill else "")).strip()
            self.assertIn(agent, names, msg=f"docs/index.html: the folder example agent {agent!r} is not one of providers.HARNESSES.")

    def test_status_table_matches_the_record(self):
        raw = PAGE.read_text(encoding="utf-8")
        table = re.search(r'<table class="record[^"]*">(.*?)</table>', raw, re.S)
        self.assertTrue(table, msg='docs/index.html needs the status table <table class="record"> built from docs/verification.json.')
        found = {}
        for row in re.findall(r"<tr>(.*?)</tr>", table.group(1).split("<tbody>", 1)[-1], re.S):
            label = html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))).strip()
            cells = [[html.unescape(re.sub(r"<[^>]+>", "", li)).strip() for li in re.findall(r"<li>(.*?)</li>", cell, re.S)] for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            found[label] = cells
        expected = {}
        for system in record()["systems"]:
            label = system["label"] + (" (preview)" if system["platform"] == "macos" and not mac_gate_met() else "")
            mine = [r for r in record()["results"] if r["system"] == system["id"]]
            ok = [CHECKS[c] for c in CHECKS if any(r["check"] == c and r["status"] == "passed" for r in mine)]
            rest = [CHECKS[c] for c in CHECKS if any(r["check"] == c and r["status"] != "passed" for r in mine)]
            expected[label] = [ok, rest]
        self.assertEqual(found, expected, msg="docs/index.html: the status table must list, per system of docs/verification.json, exactly its passed checks and its other checks, in catalogue order.")


class ImageTests(unittest.TestCase):
    def test_referenced_images_are_from_the_list(self):
        problems = []
        for rel in [*POLICY, "CHANGELOG.md", *GUIDE_FILES, NOTES]:
            if exists(rel):
                problems += image_problems(rel, referenced_images(rel))
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_screenshots_exist_once_delivered(self):
        folder = DOCS / "images"
        if not folder.is_dir():
            self.skipTest("docs/images/ does not exist yet; the screenshots are added later.")
        for rel in sorted(IMAGE_FILES):
            self.assertTrue(exists(rel), msg=f"{rel} is missing; every screenshot in IMAGE_FILES must exist in light and dark.")
            self.assertLessEqual((ROOT / rel).stat().st_size, IMAGE_BUDGET, msg=f"{rel} is larger than {IMAGE_BUDGET // 1024} KB; compress it.")
        for name in IMAGES:
            self.assertEqual(png_size(folder / f"{name}.png"), png_size(folder / f"{name}-dark.png"), msg=f"docs/images/{name}.png and {name}-dark.png must have the same pixel size.")
        extra = sorted(p.name for p in folder.iterdir() if f"docs/images/{p.name}" not in IMAGE_FILES)
        self.assertEqual(extra, [], msg="docs/images/ holds files that are not in IMAGE_FILES; remove them or add them to the list.")


class SecurityTests(unittest.TestCase):
    def test_security_policy_parts(self):
        text = read("SECURITY.md")
        self.assertEqual(text.splitlines()[0], "# Security policy", msg="SECURITY.md must start with # Security policy.")
        self.assertEqual([t for level, t in headings(text) if level == 2], SECURITY_HEADINGS, msg="SECURITY.md ## headings must be, in order: " + ", ".join(SECURITY_HEADINGS) + ".")
        self.assertIn(repo_url() + "/security/advisories/new", text, msg="SECURITY.md must give the private advisory address built from docs/site.json.")
        for word in ("vulnerability", "disclosure"):
            self.assertIn(word, text.lower(), msg=f"SECURITY.md must contain the word {word}.")
        self.assertIn("0.5", section(text, "Supported versions") or "", msg="SECURITY.md: the Supported versions table must name 0.5.")
        self.assertNotIn("if the repository has it enabled", text, msg="SECURITY.md must state the reporting route flatly.")
        self.assertIsNone(re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", text), msg="SECURITY.md must not publish an email address; the advisory form is the route.")

    def test_every_host_in_the_code_is_listed(self):
        text = read("SECURITY.md")
        problems = []
        for path in sources():
            content = path.read_text(encoding="utf-8", errors="replace")
            found = hosts(content)
            if path.name == "firecrawl.compose.yml":
                found |= {self.registry(image) for image in re.findall(r"^\s*image:\s*(\S+)", content, re.M)}
            if path.name == "Dockerfile":
                found |= {self.registry(image) for image in re.findall(r"^FROM\s+(\S+)", content, re.M)}
            for host in sorted(found - IGNORED_HOSTS):
                if host not in text:
                    problems.append(f"{path.relative_to(ROOT).as_posix()} contacts or opens {host}, which SECURITY.md does not list; add it to \"Outbound hosts\" with when and why, or to IGNORED_HOSTS with the reason.")
        self.assertEqual(problems, [], msg="\n".join(problems))

    @staticmethod
    def registry(image):
        first = image.split("/")[0]
        return first if "." in first and "/" in image else "docker.io"

    def test_contributing_and_conduct_shape(self):
        text = read("CONTRIBUTING.md")
        self.assertEqual(text.splitlines()[0], "# Contributing", msg="CONTRIBUTING.md must start with # Contributing.")
        self.assertEqual([t for level, t in headings(text) if level == 2], CONTRIBUTING_HEADINGS, msg="CONTRIBUTING.md ## headings must be, in order: " + ", ".join(CONTRIBUTING_HEADINGS) + ".")
        self.assertIn("python -m unittest discover -s tests", text, msg="CONTRIBUTING.md must give the Python test command.")
        self.assertIn("node --test tests/bridge-client.test.cjs", text, msg="CONTRIBUTING.md must give the Node test command.")
        conduct = read("CODE_OF_CONDUCT.md")
        self.assertEqual(conduct.splitlines()[0], "# Code of conduct", msg="CODE_OF_CONDUCT.md must start with # Code of conduct.")
        self.assertEqual([t for level, t in headings(conduct) if level == 2], CONDUCT_HEADINGS, msg="CODE_OF_CONDUCT.md ## headings must be, in order: " + ", ".join(CONDUCT_HEADINGS) + ".")
        for rel, body in (("CONTRIBUTING.md", text), ("CODE_OF_CONDUCT.md", conduct)):
            self.assertIsNone(re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", body), msg=f"{rel} must not publish an email address.")


class GuideTests(unittest.TestCase):
    """Vacuous until the guides exist; once one of them exists, every rule applies to all of them."""

    def present(self):
        found = [rel for rel in GUIDE_FILES if exists(rel)]
        if not found:
            self.skipTest("The guides are not written yet.")
        return found

    def test_guides_exist_and_start_with_a_title(self):
        self.present()
        for rel in GUIDE_FILES:
            self.assertTrue(exists(rel), msg=f"{rel} is missing; write every guide of docs-site.md section 5.")
            self.assertTrue(read(rel).startswith("# "), msg=f"{rel} must start with a # title.")
        index = read("docs/README.md")
        for name in GUIDES:
            self.assertIn(f"{name}.md", index, msg=f"docs/README.md must link {name}.md.")
        self.assertFalse(exists("docs/workspace-guide.md"), msg="docs/workspace-guide.md must be deleted once the guides exist (docs-site.md 5.9).")

    def test_guides_follow_the_wording_rules(self):
        problems = []
        for rel in self.present():
            problems += claim_problems(rel, prose(read(rel)))
            problems += repository_problems(rel, read(rel))
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_guide_links_resolve(self):
        problems = []
        for rel in self.present():
            problems += link_problems(rel)
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_ui_labels_exist_in_the_interface(self):
        interface = interface_text()
        problems = []
        for rel in self.present():
            problems += label_problems(rel, interface)
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_environment_variables(self):
        self.present()
        code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources())
        named = set()
        for rel in [*PUBLIC, "docs/index.html"]:
            if exists(rel):
                named |= set(re.findall(r"WORKBENCH_[A-Z0-9_]*[A-Z0-9]", read(rel)))
        for name in sorted(named):
            if not name.startswith("WORKBENCH_KEY_"):
                self.assertIn(name, code, msg=f"The documents name {name}, which no shipped source uses; fix the name or remove it.")
        if not exists("docs/reference.md"):
            self.fail("docs/reference.md is missing; it lists every WORKBENCH_ variable.")
        reference = read("docs/reference.md")
        for name in sorted(set(re.findall(r"WORKBENCH_[A-Z0-9_]*[A-Z0-9]", code)) - INTERNAL_VARIABLES):
            if not name.startswith("WORKBENCH_KEY_"):
                self.assertIn(name, reference, msg=f"docs/reference.md does not list {name}; add it to the environment-variable table.")

    def test_doctor_sections(self):
        self.present()
        if not exists("doctor.py"):
            self.skipTest("doctor.py does not exist.")
        tree = ast.parse(read("doctor.py"))
        keys = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CHECKS" for t in node.targets):
                keys = [element.elts[0].value for element in node.value.elts]
        self.assertTrue(exists("docs/troubleshooting.md"), msg="docs/troubleshooting.md is missing.")
        text = read("docs/troubleshooting.md")
        self.assertEqual(re.findall(r"^Doctor check: `([a-z-]+)`$", text, re.M), keys, msg="docs/troubleshooting.md needs one 'Doctor check: `<key>`' line per doctor.CHECKS entry, in order.")
        self.assertIn("uninstall", anchors(text), msg="docs/troubleshooting.md needs an Uninstall section.")

    def test_kit_table(self):
        self.present()
        self.assertTrue(exists("docs/project-setup.md"), msg="docs/project-setup.md is missing.")
        body = section(read("docs/project-setup.md"), "Starter kits") or ""
        rows = [line for line in body.splitlines() if line.startswith("|")]
        first = {row.strip("|").split("|")[0].strip().strip("*") for row in rows[2:]}
        self.assertTrue(kit_labels() <= first and rows, msg="docs/project-setup.md: the first table under ## Starter kits must list every kit label from kits/*/kit.json.")


def label_problems(rel, interface):
    problems = []
    for m in re.finditer(r"\*\*([^*\n]+)\*\*", strip_code(read(rel))):
        label = m.group(1).strip()
        if label.endswith((".", ":")):
            continue
        for part in (p.strip() for p in label.split("→")):
            if part and part not in interface:
                problems.append(f'{rel} shows the label "{part}", which is not in the interface; use the label the app shows or remove the bold.')
    return problems


class ReadmeTests(unittest.TestCase):
    """README.md always exists, so these rules apply to whatever README.md is in the tree."""

    def setUp(self):
        self.text = read("README.md")

    def test_readme_shape(self):
        lines = self.text.splitlines()
        self.assertLessEqual(len(lines), 200, msg="README.md is longer than 200 lines; move detail into the guides.")
        self.assertLessEqual(len(self.text.split()), 1600, msg="README.md is longer than 1,600 words; move detail into the guides.")
        self.assertEqual(lines[0], "# Agent Workbench", msg="README.md must start with # Agent Workbench.")
        first = next((line for line in lines[1:] if line.strip()), "")
        self.assertEqual(len(re.findall(r"\.(\s|$)", first)), 1, msg="README.md: the first paragraph must be one sentence.")
        self.assertNotIn("**", first, msg="README.md: the first sentence has no bold.")
        self.assertEqual([t for level, t in headings(self.text) if level == 2], README_HEADINGS, msg="README.md ## headings must be, in order: " + ", ".join(README_HEADINGS) + ".")
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", self.text), msg="README.md must not contain a SHA-256 value; the digests are in SHA256SUMS.")
        for phrase in ("reference PC", "reference RTX", "desktop shortcut"):
            self.assertNotIn(phrase, self.text, msg=f'README.md says "{phrase}", which describes the author\'s machine.')
        status = next((line for line in lines[1:] if line.startswith("Version ")), "")
        self.assertIn(f"Version {site()['version']}", status, msg="README.md needs the status line with the version from docs/site.json.")

    def test_readme_follows_the_wording_rules(self):
        problems = claim_problems("README.md", prose(self.text), evidence_allowed=True)
        problems += repository_problems("README.md", self.text)
        problems += link_problems("README.md", check_readme=True)
        problems += label_problems("README.md", interface_text())
        problems += image_problems("README.md", referenced_images("README.md"))
        self.assertEqual(problems, [], msg="\n".join(problems))
        body = prose(self.text).replace(section(self.text, "Tested configurations") or "\0", "")
        self.assertIsNone(re.search(EVIDENCE_WORDS, body), msg="README.md uses evidence words outside Tested configurations.")

    def test_install_section(self):
        body = section(self.text, "Install")
        self.assertIsNotNone(body, msg="README.md needs ## Install.")
        before = body.split("```")[0]
        self.assertIn("not code-signed or notarised", before, msg="README.md: say the downloads are not code-signed or notarised before the first command.")
        for needle in ("agent-workbench-windows-x64.zip", "agent-workbench-linux-x64.tar.gz", "agent-workbench-macos-arm64.tar.gz", "agent-workbench-source.tar.gz", "SHA256SUMS", "Get-FileHash", "sha256sum -c", "shasum -a 256 -c"):
            self.assertIn(needle, body, msg=f"README.md ## Install must name {needle}.")
        mac = "### macOS" if mac_gate_met() else "### macOS (preview)"
        for heading in ("### Windows", "### Linux", mac):
            self.assertRegex(body, r"(?m)^" + re.escape(heading) + r"\s*$", msg=f"README.md ## Install needs the subsection {heading}.")
        pre = [block.strip() for block in page().pre]
        for block in code_blocks(body):
            self.assertIn(block.strip(), pre, msg="README.md ## Install has a command block that the landing page does not show; keep the two the same.")
        for platform, title in (("windows", "Windows"), ("linux", "Linux"), ("macos", "macOS")):
            part = section(body, mac[4:] if platform == "macos" else title, level=3) or ""
            status = next((line for line in part.splitlines() if line.startswith("Status for")), "")
            self.assertEqual(status, status_sentence(platform), msg=f"README.md ### {title}: the status line must be the sentence computed from docs/verification.json.")

    def test_what_leaves_your_computer(self):
        body = section(self.text, "What leaves your computer") or ""
        self.assertIn("SECURITY.md#outbound-hosts", body, msg="README.md ## What leaves your computer must link SECURITY.md#outbound-hosts.")
        self.assertIn("outbound-hosts", anchors(read("SECURITY.md")), msg="SECURITY.md needs the Outbound hosts heading.")

    def test_tested_configurations_match_the_record(self):
        rows = tested_table_rows(self.text)
        self.assertTrue(rows, msg="README.md needs the table under ## Tested configurations.")
        self.assertEqual(rows[0], ["System", "Passed", "Failed or partly working", "Not run"], msg="README.md: the tested-configurations table needs the four columns of docs-site.md 4.7.")
        body = {row[0]: row for row in rows[2:]}
        for system in record()["systems"]:
            label = system["label"] + (" (preview)" if system["platform"] == "macos" and not mac_gate_met() else "")
            self.assertIn(label, body, msg=f"README.md: the tested-configurations table needs a row labelled {label}.")
            mine = [r for r in record()["results"] if r["system"] == system["id"]]
            ok = "; ".join(CHECKS[c] for c in CHECKS if any(r["check"] == c and r["status"] == "passed" for r in mine)) or "—"
            self.assertEqual(body[label][1], ok, msg=f"README.md: the Passed cell for {label} must list exactly its passed checks in catalogue order.")
            for r in mine:
                if r["status"] != "passed":
                    self.assertIn(CHECKS[r["check"]], body[label][2], msg=f"README.md: {label} must list {CHECKS[r['check']]} as failed or partly working.")
        for platform, label in (("windows", "Windows 11 x64"), ("linux", "Linux x64 desktop"), ("macos", "macOS on Apple Silicon (preview)")):
            if not any(s["platform"] == platform for s in record()["systems"]):
                self.assertIn(label, body, msg=f"README.md: the tested-configurations table needs the row {label}.")
                self.assertEqual(body[label][3], "Everything", msg=f"README.md: {label} has no recorded runs, so its Not run cell is Everything.")
        if not mac_gate_met():
            for label in body:
                if label.startswith("macOS"):
                    self.assertIn("preview", label, msg="README.md: macOS rows say preview until MAC_GATE passes.")

    def test_anchors_other_documents_use(self):
        own = anchors(self.text)
        prefix = repo_url() + "#"
        wanted = [a.get("href", "")[len(prefix) :] for tag, a in page().tags if tag == "a" and a.get("href", "").startswith(prefix)]
        for rel in [*POLICY, "CHANGELOG.md", *GUIDE_FILES]:
            if exists(rel):
                wanted += [t.partition("#")[2] for t in links(read(rel)) if t.partition("#")[0].endswith("README.md") and not t.partition("#")[0].endswith("docs/README.md") and "#" in t]
        for fragment in wanted:
            self.assertIn(fragment, own, msg=f"README.md has no heading for the anchor #{fragment} that another document links to.")


class ChangelogTests(unittest.TestCase):
    """Vacuous until CHANGELOG.md exists."""

    def setUp(self):
        if not exists("CHANGELOG.md"):
            self.skipTest("CHANGELOG.md is not written yet.")
        self.text = read("CHANGELOG.md")

    def test_changelog_shape(self):
        self.assertEqual(self.text.splitlines()[0], "# Changelog", msg="CHANGELOG.md must start with # Changelog.")
        entries = self.text.split("\n## [")[1:]
        if entries and entries[0].startswith("Unreleased]\n"):
            # Changes on the main branch since the last release come first, in the same heading order.
            found = [t for level, t in headings(entries.pop(0)) if level == 3]
            self.assertEqual(found, [h for h in CHANGELOG_HEADINGS if h in found], msg="CHANGELOG.md: the Unreleased headings must keep the order Added, Changed, Fixed, Security, Removed, Known issues.")
        first = next((t for level, t in headings(self.text) if level == 2 and t != "[Unreleased]"), "")
        self.assertRegex(first, r"^\[" + re.escape(site()["version"]) + r"\] - (unreleased|\d{4}-\d{2}-\d{2})$", msg="CHANGELOG.md: the first version entry must be ## [<version>] - unreleased or a date, after an optional ## [Unreleased].")
        entry = entries[0] if entries else ""
        found = [t for level, t in headings(entry) if level == 3]
        self.assertEqual(found, [h for h in CHANGELOG_HEADINGS if h in found], msg="CHANGELOG.md: the release headings must keep the order Added, Changed, Fixed, Security, Removed, Known issues.")
        for needed in ("Added", "Known issues"):
            self.assertIn(needed, found, msg=f"CHANGELOG.md: the release entry needs ### {needed}.")
        self.assertIn("README.md#tested-configurations", entry, msg="CHANGELOG.md: the release entry must link README.md#tested-configurations.")
        if not mac_gate_met():
            known = section(entry, "Known issues", level=3) or ""
            self.assertTrue(any("macOS" in line and "preview" in line for line in known.splitlines()), msg="CHANGELOG.md: Known issues must say macOS is a preview.")

    def test_changelog_follows_the_wording_rules(self):
        problems = claim_problems("CHANGELOG.md", prose(self.text), evidence_allowed=True)
        problems += repository_problems("CHANGELOG.md", self.text)
        problems += link_problems("CHANGELOG.md")
        self.assertEqual(problems, [], msg="\n".join(problems))


class ManifestTests(unittest.TestCase):
    def test_manifest_lists_public_docs(self):
        if not exists("release/public-files.txt"):
            self.skipTest("release/public-files.txt does not exist.")
        lines = set(read("release/public-files.txt").splitlines())
        wanted = [rel for rel in PUBLIC if exists(rel)]
        wanted += ["docs/index.html", "docs/site.json", "docs/verification.json", "tests/test_docs.py"]
        wanted += sorted(rel for rel in IMAGE_FILES if exists(rel))
        missing = [rel for rel in wanted if rel not in lines]
        # Pages serves docs/ from the repository; its marker file stays out of the release kits.
        self.assertNotIn("docs/.nojekyll", lines)
        self.assertEqual(missing, [], msg="release/public-files.txt must list " + ", ".join(missing) + "; regenerate it with python release/build.py --write-manifest.")
        for rel in (NOTES, "docs/workspace-guide.md"):
            self.assertNotIn(rel, lines, msg=f"release/public-files.txt lists {rel}, which stays out of releases.")
        stale = sorted(rel for rel in lines if re.match(r"docs/[^/]+\.png$", rel))
        self.assertEqual(stale, [], msg="release/public-files.txt lists 0.3 screenshots that no document references: " + ", ".join(stale) + ".")


if __name__ == "__main__":
    unittest.main()
