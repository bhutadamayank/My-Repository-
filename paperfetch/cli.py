"""Command-line interface.

    paperfetch "attention is all you need"      # title / free text
    paperfetch 10.1038/nature14539              # DOI (or doi.org URL)
    paperfetch arXiv:1706.03762  PMC4795549  PMID 26017442
    paperfetch                                  # interactive: ask for one article after another
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import requests

from . import __version__
from .config import CONFIG_PATH, Config, write_template
from .downloader import Downloader, Library, safe_filename
from .http import api_headers, make_session
from .identifiers import Identifier, parse
from .metadata import Article, Resolver, title_similarity
from .sources import SourceFinder

COMMANDS = {"get", "search", "batch", "init", "check", "shell"}


class Ctx:
    def __init__(self, cfg: Config, verbose: bool, quiet: bool):
        self.cfg = cfg
        self.verbose = verbose
        self.quiet = quiet
        self.session = make_session(cfg)
        self.resolver = Resolver(self.session, cfg)
        self.finder = SourceFinder(self.session, cfg, self.debug)
        self.downloader = Downloader(self.session, cfg, self.debug)

    def say(self, *a):
        if not self.quiet:
            print(*a, flush=True)

    def debug(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr, flush=True)


def describe(a: Article) -> str:
    oa = {True: "open access", False: "paywalled", None: "access ?"}[a.is_oa]
    ident = f"doi:{a.doi}" if a.doi else (f"arXiv:{a.arxiv_id}" if a.arxiv_id else (a.pmcid or a.pmid or ""))
    return f"{a.citation()}\n      {ident}  [{oa}]"


def choose(ctx: Ctx, query: str, results: list[Article], auto: bool) -> Article | None:
    if not results:
        return None
    sim = title_similarity(query, results[0].title)
    if auto or sim >= 0.9 or len(results) == 1 or not sys.stdin.isatty():
        if sim < 0.9 and not ctx.quiet:
            ctx.say("  (best guess — pass a DOI for certainty)")
        return results[0]
    ctx.say("Several matches:")
    for i, a in enumerate(results, 1):
        ctx.say(f"  [{i}] {describe(a)}")
    while True:
        ans = input(f"Pick 1-{len(results)} [Enter = 1, s = skip]: ").strip().lower()
        if ans == "":
            return results[0]
        if ans in ("s", "q", "skip"):
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(results):
            return results[int(ans) - 1]


def resolve_text(ctx: Ctx, text: str, auto: bool) -> Article | None:
    ident = parse(text)
    ctx.debug(f"  · interpreted as {ident.kind}: {ident.value}")
    if ident.kind != "query":
        return ctx.resolver.resolve(ident)
    ctx.say(f"Searching for: {text}")
    art = choose(ctx, text, ctx.resolver.search(text), auto)
    if art and art.doi and not any(l.source == "openalex" for l in art.locations):
        full = ctx.resolver.resolve(Identifier("doi", art.doi))  # enrich Crossref hits with OA data
        if full:
            full.merge(art)
            art = full
    return art


def fetch_one(ctx: Ctx, text: str, args) -> bool:
    try:
        art = resolve_text(ctx, text, args.yes)
    except requests.RequestException as e:
        ctx.say(f"✗ Network error while looking up the article: {e}")
        return False
    for w in ctx.resolver.warnings:
        ctx.say(f"  ! {w}")
    ctx.resolver.warnings.clear()
    if not art or not (art.doi or art.arxiv_id or art.pmcid or art.url):
        ctx.say(f"✗ Could not identify an article for: {text}")
        return False
    ctx.say(f"• {describe(art)}")

    out_dir = Path(args.out).expanduser() if args.out else ctx.cfg.download_dir
    lib = Library(out_dir)
    existing = lib.find(art)
    if existing and not args.force:
        ctx.say(f"✓ Already downloaded: {existing}")
        return True

    cands = ctx.finder.find(art)
    if args.list:
        ctx.say("Candidate sources (in the order they would be tried):")
        for c in cands:
            ctx.say(f"  - {c.label()}\n      {c.url}")
        return bool(cands)
    if not cands:
        ctx.say("✗ No sources to try (no DOI and no open-access copy known).")
        return False

    dest = out_dir / safe_filename(art)
    res = ctx.downloader.fetch(cands, dest)
    if res.path:
        lib.add(art, res.path, res.candidate)
        ctx.say(f"✓ Saved {res.path}\n    from {res.candidate.label()}")
        return True

    ctx.say("✗ No PDF could be retrieved.")
    shown = res.attempts if ctx.verbose else res.attempts[-6:]
    for url, outcome in shown:
        ctx.say(f"    {outcome}\n      {url}")
    landing = f"https://doi.org/{art.doi}" if art.doi else (art.url or "")
    if res.bot_walls:
        ctx.say("  The site refused automated access (bot check or rate limit). paperfetch does not try to"
                " get around that: open the article in your browser and download it there.")
    elif ctx.cfg.use_institution and art.doi and art.is_oa is not True:
        ctx.say("  No open-access copy, and the publisher did not grant a PDF. Are you on the institution's"
                " network/VPN, and does it subscribe to this journal? Run `paperfetch check`.")
    if landing:
        ctx.say(f"  Article page: {landing}")
        if args.open:
            webbrowser.open(landing)
    return False


def cmd_search(ctx: Ctx, args) -> int:
    results = ctx.resolver.search(" ".join(args.query), args.n)
    if not results:
        ctx.say("No results.")
        return 1
    for i, a in enumerate(results, 1):
        print(f"[{i}] {describe(a)}")
    return 0


def cmd_batch(ctx: Ctx, args) -> int:
    lines = [l.strip() for l in Path(args.file).read_text().splitlines()]
    items = [l for l in lines if l and not l.startswith("#")]
    ok = 0
    for i, item in enumerate(items, 1):
        ctx.say(f"\n[{i}/{len(items)}] {item}")
        ok += fetch_one(ctx, item, args)
    ctx.say(f"\nDone: {ok}/{len(items)} downloaded.")
    return 0 if ok == len(items) else 1


def cmd_shell(ctx: Ctx, args) -> int:
    ctx.say(f"paperfetch {__version__} — type a title, DOI, arXiv id, PMID or URL. Empty line or 'q' to quit.")
    while True:
        try:
            text = input("\narticle> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in ("", "q", "quit", "exit"):
            return 0
        fetch_one(ctx, text, args)


def cmd_check(ctx: Ctx, args) -> int:
    cfg = ctx.cfg
    print(f"Config file : {CONFIG_PATH} ({'found' if CONFIG_PATH.is_file() else 'missing — run `paperfetch init`'})")
    print(f"E-mail      : {cfg.email or 'NOT SET (Unpaywall disabled)'}")
    print(f"Save to     : {args.out or cfg.download_dir}")
    print(f"EZproxy     : {cfg.proxy_prefix or '—'}")
    try:
        info = ctx.session.get("https://ipinfo.io/json", timeout=10).json()
        print(f"Public IP   : {info.get('ip')}  ({info.get('org', '?')}, {info.get('city', '?')})")
        print("              ↑ publishers grant access by this address; it should belong to your institution.")
    except Exception:
        print("Public IP   : unknown")
    for name, url in [
        ("OpenAlex", "https://api.openalex.org/works?search=test&per-page=1"),
        ("Crossref", "https://api.crossref.org/works?rows=1"),
        ("Unpaywall", f"https://api.unpaywall.org/v2/10.1038/nature14539?email={cfg.email or 'x'}"),
        ("arXiv", "https://export.arxiv.org/api/query?id_list=1706.03762"),
    ]:
        try:
            code = ctx.session.get(url, headers=api_headers(cfg), timeout=15).status_code
        except requests.RequestException as e:
            code = type(e).__name__
        print(f"{name:<12}: {code}")

    doi = args.doi
    print(f"\nInstitutional test with a subscription article ({doi}) …")
    saved_oa = cfg.use_oa
    cfg.use_oa = False
    try:
        art = ctx.resolver.resolve(Identifier("doi", doi)) or Article(doi=doi)
        tmp = Path(args.out or cfg.download_dir) / ".paperfetch-check.pdf"
        res = ctx.downloader.fetch(ctx.finder.find(art), tmp)
    finally:
        cfg.use_oa = saved_oa
    if res.path:
        res.path.unlink(missing_ok=True)
        print(f"✓ Institutional access works (got the PDF from {res.candidate.label()}).")
        return 0
    print("✗ Could not get the subscription PDF. Either this network is not recognised by the publisher,"
          " your library does not subscribe to that journal, or a bot check blocked us (see -v).")
    for url, outcome in res.attempts[-4:]:
        print(f"    {outcome}  {url}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out", help="download folder (default: from config, else ./papers)")
    common.add_argument("-y", "--yes", action="store_true", help="never prompt; take the best search match")
    common.add_argument("--prefer", choices=["best", "oa", "publisher"], help="which copy to try first")
    g = common.add_mutually_exclusive_group()
    g.add_argument("--oa-only", action="store_true", help="only open-access copies")
    g.add_argument("--publisher-only", action="store_true", help="only the publisher (institutional access)")
    common.add_argument("--list", action="store_true", help="show candidate sources without downloading")
    common.add_argument("--open", action="store_true", help="open the article page in a browser if download fails")
    common.add_argument("--force", action="store_true", help="download again even if already in the library")
    common.add_argument("--proxy-prefix", help="EZproxy prefix, e.g. https://ezproxy.uni.edu/login?url=")
    common.add_argument("--email", help="contact e-mail for Unpaywall/OpenAlex/Crossref")
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("-q", "--quiet", action="store_true")

    p = argparse.ArgumentParser(
        prog="paperfetch",
        description="Find and download article PDFs: open access first, then your institution's subscriptions.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("get", parents=[common], help="download one or more articles (default command)")
    s.add_argument("query", nargs="*", help="title, DOI, arXiv id, PMID, PMCID or URL; use quotes per article")
    s = sub.add_parser("search", parents=[common], help="list matching articles without downloading")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", type=int, default=5)
    s = sub.add_parser("batch", parents=[common], help="download every identifier/title listed in a text file")
    s.add_argument("file")
    sub.add_parser("shell", parents=[common], help="interactive mode (also the default with no arguments)")
    sub.add_parser("init", parents=[common], help=f"create a config file at {CONFIG_PATH}")
    s = sub.add_parser("check", parents=[common], help="diagnose network / institutional access")
    s.add_argument("--doi", default="10.1038/nature14539", help="a subscription-only DOI your library should cover")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "get")  # bare query: `paperfetch "some title" -o dir`; no query -> shell
    args = build_parser().parse_args(argv)

    if args.cmd == "init":
        path = write_template()
        print(f"Config file: {path}\nEdit it to set your e-mail, download folder and (optionally) EZproxy.")
        return 0

    cfg = Config.load()
    if args.email:
        cfg.email = args.email
    if args.prefer:
        cfg.prefer = args.prefer
    if args.proxy_prefix:
        cfg.proxy_prefix = args.proxy_prefix
    if args.oa_only:
        cfg.use_institution = False
    if args.publisher_only:
        cfg.use_oa = False
    ctx = Ctx(cfg, args.verbose, args.quiet)

    try:
        if args.cmd == "get" and args.query:
            # Several quoted arguments = several articles; unquoted words = one title.
            items = args.query if all(parse(q).kind != "query" for q in args.query) else [" ".join(args.query)]
            ok = sum(fetch_one(ctx, q, args) for q in items)
            return 0 if ok == len(items) else 1
        if args.cmd == "search":
            return cmd_search(ctx, args)
        if args.cmd == "batch":
            return cmd_batch(ctx, args)
        if args.cmd == "check":
            return cmd_check(ctx, args)
        return cmd_shell(ctx, args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
