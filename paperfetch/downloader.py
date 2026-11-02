"""Try ranked candidates until one yields a genuine PDF."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .config import Config
from .metadata import Article
from .http import api_headers
from .publishers import extract_pdf_links, figshare_article_id, figshare_pdf_urls, looks_like_bot_wall
from .sources import Candidate

MAX_PDF_BYTES = 250 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_HOPS_PER_FETCH = 25


@dataclass
class Result:
    path: Path | None = None
    candidate: Candidate | None = None
    attempts: list[tuple[str, str]] = field(default_factory=list)  # (url, outcome)
    bot_walls: list[str] = field(default_factory=list)


class Downloader:
    def __init__(self, session: requests.Session, cfg: Config, log=lambda *_: None):
        self.s = session
        self.cfg = cfg
        self.log = log

    def fetch(self, candidates: list[Candidate], dest: Path) -> Result:
        res = Result()
        visited: set[str] = set()
        dest.parent.mkdir(parents=True, exist_ok=True)
        for cand in candidates:
            self.log(f"  → trying {cand.label()}: {cand.url}")
            path = self._try(cand.url, cand, dest, 0, visited, None, res)
            if path:
                res.path, res.candidate = path, cand
                return res
        return res

    def _try(self, url, cand, dest, depth, visited, referer, res: Result) -> Path | None:
        if url in visited or len(visited) >= MAX_HOPS_PER_FETCH:
            return None
        visited.add(url)
        headers = dict(cand.headers or {}) if depth == 0 else {}
        if referer:
            headers["Referer"] = referer
        try:
            r = self.s.get(url, headers=headers, timeout=self.cfg.timeout, stream=True, allow_redirects=True)
        except requests.RequestException as e:
            res.attempts.append((url, f"network error: {type(e).__name__}"))
            return None

        with r:
            chunks = r.iter_content(64 * 1024)
            try:
                first = next(chunks, b"")
            except requests.RequestException as e:
                res.attempts.append((url, f"network error: {type(e).__name__}"))
                return None
            if r.status_code == 200 and b"%PDF-" in first[:1024]:
                return self._save_pdf(r, first, chunks, cand, dest, res)

            if r.status_code == 429:
                res.attempts.append((url, "HTTP 429: the site is rate-limiting us; try later or use the browser"))
                res.bot_walls.append(r.url)
                return None
            ctype = r.headers.get("Content-Type", "").lower()
            if "html" not in ctype and not first.lstrip()[:1] == b"<":
                res.attempts.append((url, f"HTTP {r.status_code}, not a PDF ({ctype or 'unknown type'})"))
                return None
            body = bytearray(first)
            try:
                for chunk in chunks:
                    body += chunk
                    if len(body) > MAX_HTML_BYTES:
                        break
            except requests.RequestException:
                pass
            final_url = r.url
            status = r.status_code
            text = body.decode(r.encoding or "utf-8", errors="replace")

        fid = figshare_article_id(final_url)
        if fid and depth < 2:
            return self._try_figshare(fid, cand, dest, depth, visited, res)
        if looks_like_bot_wall(text) or (status == 202 and len(text.strip()) < 2000):
            res.attempts.append((final_url, f"HTTP {status}: blocked by a bot/JavaScript check"))
            res.bot_walls.append(final_url)
            return None
        if depth >= 2:
            res.attempts.append((final_url, f"HTTP {status}: HTML page (max depth)"))
            return None
        links = [u for u in extract_pdf_links(final_url, text) if u not in visited]
        if not links:
            hint = "likely not entitled" if status in (200, 401, 403) else "no PDF link"
            res.attempts.append((final_url, f"HTTP {status}: HTML page without a PDF link ({hint})"))
            return None
        res.attempts.append((final_url, f"HTTP {status}: HTML page, following {min(len(links), 8)} PDF link(s)"))
        for link in links[:8]:
            path = self._try(link, cand, dest, depth + 1, visited, final_url, res)
            if path:
                return path
        return None

    def _try_figshare(self, article_id, cand, dest, depth, visited, res: Result) -> Path | None:
        """Repositories on Figshare serve files through their public API."""
        api = f"https://api.figshare.com/v2/articles/{article_id}"
        try:
            r = self.s.get(api, headers=api_headers(self.cfg), timeout=self.cfg.timeout)
            data = r.json() if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            data = {}
        urls = figshare_pdf_urls(data)
        if not urls:
            res.attempts.append((api, "repository record has no PDF file attached (metadata only)"))
            return None
        for u in urls:
            path = self._try(u, cand, dest, depth + 1, visited, None, res)
            if path:
                return path
        return None

    def _save_pdf(self, r, first, chunks, cand, dest: Path, res: Result) -> Path | None:
        tmp = dest.with_suffix(".part")
        size = 0
        try:
            with open(tmp, "wb") as fh:
                fh.write(first)
                size = len(first)
                for chunk in chunks:
                    fh.write(chunk)
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ValueError("file too large")
        except (requests.RequestException, ValueError, OSError) as e:
            tmp.unlink(missing_ok=True)
            res.attempts.append((r.url, f"download failed: {e}"))
            return None
        expected = r.headers.get("Content-Length")
        if expected and expected.isdigit() and "gzip" not in r.headers.get("Content-Encoding", "") and int(expected) != size:
            tmp.unlink(missing_ok=True)
            res.attempts.append((r.url, f"truncated download ({size}/{expected} bytes)"))
            return None
        if cand.source == "elsevier-api" and pdf_page_count(tmp) == 1:
            # Elsevier's API returns only page 1 when your IP is not entitled.
            tmp.unlink(missing_ok=True)
            res.attempts.append((r.url, "Elsevier API returned a 1-page preview (not entitled)"))
            return None
        os.replace(tmp, dest)
        res.attempts.append((r.url, f"OK, {size / 1024:.0f} KB"))
        return dest


def pdf_page_count(path: Path) -> int | None:
    """Rough page count; None when pages live in compressed object streams."""
    data = path.read_bytes()
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
    return n or None


def safe_filename(art: Article, max_title_words: int = 8) -> str:
    def ascii_(s: str) -> str:
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

    author = ascii_(art.authors[0].split()[-1]) if art.authors and art.authors[0].split() else "Unknown"
    words = re.findall(r"[A-Za-z0-9]+", ascii_(art.title or ""))[:max_title_words]
    ident = (art.doi or art.arxiv_id or art.pmcid or art.pmid or "article")
    stem = "_".join(filter(None, [author, str(art.year or "nd"), "_".join(words) or re.sub(r"\W+", "_", ident)]))
    return re.sub(r"_+", "_", stem)[:150] + ".pdf"


class Library:
    """A JSON-lines log of what was downloaded, kept next to the PDFs."""

    def __init__(self, folder: Path):
        self.path = folder / "library.jsonl"

    def find(self, art: Article) -> Path | None:
        if not self.path.is_file():
            return None
        keys = {k for k in (art.doi, art.arxiv_id, art.pmcid) if k}
        if not keys:
            return None
        for line in self.path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if keys & {rec.get("doi"), rec.get("arxiv_id"), rec.get("pmcid")}:
                p = self.path.parent / rec.get("file", "")
                if p.is_file():
                    return p
        return None

    def add(self, art: Article, path: Path, cand: Candidate) -> None:
        rec = {
            "file": path.name,
            "title": art.title,
            "authors": art.authors,
            "year": art.year,
            "venue": art.venue,
            "doi": art.doi,
            "arxiv_id": art.arxiv_id,
            "pmid": art.pmid,
            "pmcid": art.pmcid,
            "source": cand.source,
            "access": cand.access,
            "version": cand.version,
            "url": cand.url,
            "downloaded": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
