"""Collect every candidate PDF URL for an article and rank them."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import requests

from .config import Config
from .http import api_headers
from .metadata import Article
from .publishers import doi_prefix_urls

VERSION_RANK = {"publishedVersion": 0, "acceptedVersion": 2, "submittedVersion": 3}


@dataclass
class Candidate:
    url: str
    source: str  # unpaywall | openalex | semantic-scholar | arxiv | europepmc | pmc | publisher | ...
    access: str  # "open" | "institutional"
    version: str | None = None
    headers: dict | None = None

    def rank(self, prefer: str) -> float:
        if self.access == "institutional":
            base = 1.0
        else:
            base = VERSION_RANK.get(self.version or "", 1.5)
        if prefer == "oa":
            return base + (10 if self.access == "institutional" else 0)
        if prefer == "publisher":
            return base - (10 if self.access == "institutional" else 0)
        return base

    def label(self) -> str:
        v = {"publishedVersion": "published", "acceptedVersion": "accepted ms", "submittedVersion": "preprint"}.get(
            self.version or "", "version ?"
        )
        kind = "via institution" if self.access == "institutional" else f"open access, {v}"
        return f"{self.source} ({kind})"


class SourceFinder:
    def __init__(self, session: requests.Session, cfg: Config, log=lambda *_: None):
        self.s = session
        self.cfg = cfg
        self.log = log

    def unpaywall(self, doi: str) -> list[Candidate]:
        if not self.cfg.email:
            self.log("  · Unpaywall skipped (set an e-mail in config to enable it)")
            return []
        try:
            r = self.s.get(
                f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
                params={"email": self.cfg.email},
                headers=api_headers(self.cfg),
                timeout=self.cfg.timeout,
            )
            if r.status_code != 200:
                return []
            data = r.json()
        except (requests.RequestException, ValueError):
            return []
        out = []
        for loc in data.get("oa_locations") or []:
            url = loc.get("url_for_pdf") or loc.get("url")
            if url:
                out.append(Candidate(url, "unpaywall", "open", loc.get("version")))
        return out

    def semantic_scholar(self, art: Article) -> list[Candidate]:
        key = f"DOI:{art.doi}" if art.doi else (f"ARXIV:{art.arxiv_id}" if art.arxiv_id else None)
        if not key:
            return []
        headers = api_headers(self.cfg)
        if self.cfg.semantic_scholar_api_key:
            headers["x-api-key"] = self.cfg.semantic_scholar_api_key
        try:
            r = self.s.get(
                f"https://api.semanticscholar.org/graph/v1/paper/{quote(key, safe=':/')}",
                params={"fields": "openAccessPdf"},
                headers=headers,
                timeout=self.cfg.timeout,
            )
            if r.status_code != 200:
                return []
            pdf = (r.json().get("openAccessPdf") or {}).get("url")
        except (requests.RequestException, ValueError):
            return []
        return [Candidate(pdf, "semantic-scholar", "open")] if pdf else []

    def find(self, art: Article) -> list[Candidate]:
        cands: list[Candidate] = []
        if self.cfg.use_oa:
            for loc in art.locations:
                if loc.is_oa and loc.pdf_url:
                    cands.append(Candidate(loc.pdf_url, loc.source, "open", loc.version))
            if art.pmcid:
                cands.append(Candidate(
                    f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={art.pmcid}&blobtype=pdf",
                    "europepmc", "open", "publishedVersion"))
                cands.append(Candidate(
                    f"https://pmc.ncbi.nlm.nih.gov/articles/{art.pmcid}/pdf/", "pmc", "open", "publishedVersion"))
            if art.arxiv_id:
                cands.append(Candidate(f"https://arxiv.org/pdf/{art.arxiv_id}", "arxiv", "open", "submittedVersion"))
            if art.doi:
                cands += self.unpaywall(art.doi)
            cands += self.semantic_scholar(art)
            for loc in art.locations:  # OA landing pages without a direct PDF link
                if loc.is_oa and not loc.pdf_url and loc.landing_url:
                    cands.append(Candidate(loc.landing_url, loc.source, "open", loc.version))

        if self.cfg.use_institution:
            if art.doi:
                cands.append(Candidate(self.wrap(f"https://doi.org/{art.doi}"), "publisher", "institutional", "publishedVersion"))
                if self.cfg.elsevier_api_key and art.doi.startswith("10.1016/"):
                    cands.append(Candidate(
                        f"https://api.elsevier.com/content/article/doi/{art.doi}?httpAccept=application/pdf",
                        "elsevier-api", "institutional", "publishedVersion",
                        headers={"X-ELS-APIKey": self.cfg.elsevier_api_key, "Accept": "application/pdf"}))
                for u in doi_prefix_urls(art.doi):
                    cands.append(Candidate(self.wrap(u), "publisher", "institutional", "publishedVersion"))
                for loc in art.locations:
                    if loc.source == "crossref" and loc.pdf_url:
                        cands.append(Candidate(self.wrap(loc.pdf_url), "publisher", "institutional", "publishedVersion"))
            if art.url:
                cands.append(Candidate(self.wrap(art.url), "given-url", "institutional", None))

        # Stable sort keeps discovery order within the same rank; drop duplicate URLs.
        seen, ranked = set(), []
        for c in sorted(cands, key=lambda c: c.rank(self.cfg.prefer)):
            if c.url not in seen:
                seen.add(c.url)
                ranked.append(c)
        return ranked

    def wrap(self, url: str) -> str:
        """Route a publisher URL through the library's EZproxy if configured."""
        p = self.cfg.proxy_prefix
        return f"{p}{url}" if p else url
