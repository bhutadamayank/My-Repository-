"""Resolve identifiers / free text to article metadata (OpenAlex, Crossref, arXiv, Europe PMC)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

from .config import Config
from .http import api_headers
from .identifiers import Identifier, clean_doi


@dataclass
class Location:
    """A place a copy of the article is hosted (from an OA index)."""

    pdf_url: str | None
    landing_url: str | None
    version: str | None  # publishedVersion | acceptedVersion | submittedVersion | None
    is_oa: bool
    source: str  # which index reported it
    host: str | None = None  # repository / journal name


@dataclass
class Article:
    title: str | None = None
    doi: str | None = None
    year: int | None = None
    authors: list[str] = field(default_factory=list)
    venue: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    is_oa: bool | None = None
    locations: list[Location] = field(default_factory=list)
    url: str | None = None  # a non-DOI landing page the user gave us

    def citation(self) -> str:
        who = self.authors[0].split()[-1] if self.authors else "Unknown"
        if len(self.authors) > 1:
            who += " et al."
        bits = [f"{who} ({self.year or 'n.d.'})", self.title or "(untitled)"]
        if self.venue:
            bits.append(self.venue)
        return ". ".join(bits)

    def merge(self, other: "Article") -> None:
        for name in ("title", "doi", "year", "venue", "arxiv_id", "pmid", "pmcid", "is_oa", "url"):
            if getattr(self, name) in (None, "") and getattr(other, name) not in (None, ""):
                setattr(self, name, getattr(other, name))
        if not self.authors:
            self.authors = other.authors
        self.locations.extend(other.locations)


class Resolver:
    def __init__(self, session: requests.Session, cfg: Config):
        self.s = session
        self.cfg = cfg
        self.warnings: list[str] = []

    def _get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        try:
            r = self.s.get(url, params=params, headers=headers or api_headers(self.cfg), timeout=self.cfg.timeout)
        except requests.RequestException:
            return None
        if r.status_code == 429:
            host = url.split("/")[2]
            hint = " (add a free openalex_api_key to the config)" if "openalex" in host else ""
            msg = f"{host} rate limit reached{hint}"
            if msg not in self.warnings:
                self.warnings.append(msg)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # ---------- OpenAlex ----------
    def _openalex_params(self) -> dict:
        p = {}
        if self.cfg.email:
            p["mailto"] = self.cfg.email
        if self.cfg.openalex_api_key:
            p["api_key"] = self.cfg.openalex_api_key
        return p

    def openalex(self, key: str) -> Article | None:
        """key: 'https://doi.org/..', 'pmid:123', 'pmcid:PMC123'."""
        data = self._get_json(f"https://api.openalex.org/works/{quote(key, safe=':/')}", self._openalex_params())
        return parse_openalex(data) if data else None

    def openalex_search(self, query: str, n: int = 5) -> list[Article]:
        params = {"search": query, "per-page": n, **self._openalex_params()}
        data = self._get_json("https://api.openalex.org/works", params)
        return [parse_openalex(w) for w in (data or {}).get("results", [])]

    # ---------- Crossref ----------
    def crossref(self, doi: str) -> Article | None:
        data = self._get_json(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
        return parse_crossref(data["message"]) if data and "message" in data else None

    def crossref_search(self, query: str, n: int = 5) -> list[Article]:
        params = {"query.bibliographic": query, "rows": n}
        if self.cfg.email:
            params["mailto"] = self.cfg.email
        data = self._get_json("https://api.crossref.org/works", params)
        return [parse_crossref(m) for m in (data or {}).get("message", {}).get("items", [])]

    # ---------- arXiv ----------
    def arxiv(self, arxiv_id: str) -> Article | None:
        try:
            r = self.s.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": arxiv_id},
                headers=api_headers(self.cfg),
                timeout=self.cfg.timeout,
            )
            r.raise_for_status()
        except requests.RequestException:
            return Article(arxiv_id=arxiv_id)
        return parse_arxiv_atom(r.text, arxiv_id)

    # ---------- Europe PMC (for PMIDs / PMCIDs OpenAlex might miss) ----------
    def europepmc(self, kind: str, value: str) -> Article | None:
        q = f"EXT_ID:{value} AND SRC:MED" if kind == "pmid" else f"PMCID:{value}"
        data = self._get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": q, "format": "json", "resultType": "core"},
        )
        hits = (data or {}).get("resultList", {}).get("result", [])
        if not hits:
            return None
        h = hits[0]
        authors = [a.get("fullName", "") for a in h.get("authorList", {}).get("author", [])]
        art = Article(
            title=(h.get("title") or "").rstrip(".") or None,
            doi=clean_doi(h["doi"]) if h.get("doi") else None,
            year=int(h["pubYear"]) if str(h.get("pubYear", "")).isdigit() else None,
            authors=[_flip_name(a) for a in authors if a],
            venue=h.get("journalInfo", {}).get("journal", {}).get("title"),
            pmid=h.get("pmid"),
            pmcid=h.get("pmcid"),
            is_oa=h.get("isOpenAccess") == "Y",
        )
        return art

    # ---------- Entry points ----------
    def resolve(self, ident: Identifier) -> Article | None:
        if ident.kind == "doi":
            art = self.openalex(f"https://doi.org/{ident.value}") or Article(doi=ident.value)
            if not art.title:
                cr = self.crossref(ident.value)
                if cr:
                    art.merge(cr)
            art.doi = art.doi or ident.value
            return art
        if ident.kind == "arxiv":
            art = self.arxiv(ident.value) or Article(arxiv_id=ident.value)
            if art.doi:  # published version exists -> pull OA/journal info too
                oa = self.openalex(f"https://doi.org/{art.doi}")
                if oa:
                    art.merge(oa)
            return art
        if ident.kind in ("pmid", "pmcid"):
            art = self.openalex(f"{ident.kind}:{ident.value}")
            epmc = self.europepmc(ident.kind, ident.value)
            if art and epmc:
                art.merge(epmc)
            art = art or epmc
            if art is None:
                return None
            if ident.kind == "pmcid":
                art.pmcid = art.pmcid or ident.value
            else:
                art.pmid = art.pmid or ident.value
            return art
        if ident.kind == "url":
            return Article(url=ident.value)
        return None

    def arxiv_search(self, query: str, n: int = 5) -> list[Article]:
        stop = {"a", "an", "the", "is", "are", "of", "in", "on", "for", "to", "and", "or", "with", "by", "from", "at", "as", "be"}
        words = [w for w in re.findall(r"\w+", query) if w.lower() not in stop]
        if not words:
            return []
        try:
            r = self.s.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": " AND ".join(f"ti:{w}" for w in words) if len(words) < 12 else f'all:"{query}"',
                        "max_results": n},
                headers=api_headers(self.cfg),
                timeout=self.cfg.timeout,
            )
            r.raise_for_status()
        except requests.RequestException:
            return []
        return parse_arxiv_feed(r.text)

    def search(self, query: str, n: int = 5) -> list[Article]:
        results = self.openalex_search(query, n)
        from_openalex = bool(results)
        if not results:
            results = self.crossref_search(query, n)
        # Crossref has no preprints; OpenAlex covers arXiv already.
        if not from_openalex or not any(title_similarity(query, a.title) >= 0.95 for a in results):
            seen = {a.doi for a in results if a.doi}
            results += [a for a in self.arxiv_search(query, 3) if not a.doi or a.doi not in seen]
        # Near-exact title matches first (best first); the rest keep the engines' relevance order.
        def key(a: Article):
            sim = title_similarity(query, a.title)
            return (0, -sim) if sim >= 0.85 else (1, 0)
        return sorted(results, key=key)[: n + 2]


# ---------------- parsers (pure functions, unit-tested) ----------------

def _flip_name(name: str) -> str:
    """'Smith J' / 'Smith, John' -> 'John Smith' (best effort)."""
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        return f"{first} {last}"
    return name


def parse_openalex(w: dict) -> Article:
    ids = w.get("ids") or {}
    doi = clean_doi(w["doi"]) if w.get("doi") else None
    pmid = ids.get("pmid", "").rstrip("/").rsplit("/", 1)[-1] or None
    pmcid_raw = ids.get("pmcid", "")
    m = re.search(r"(?:PMC)?(\d+)/?$", pmcid_raw or "", re.I)
    art = Article(
        title=w.get("title") or w.get("display_name"),
        doi=doi,
        year=w.get("publication_year"),
        authors=[a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])],
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        pmid=pmid,
        pmcid=f"PMC{m.group(1)}" if m else None,
        is_oa=(w.get("open_access") or {}).get("is_oa"),
    )
    for loc in w.get("locations") or []:
        landing = loc.get("landing_page_url")
        if landing and not art.arxiv_id:
            am = re.search(r"arxiv\.org/abs/([^\s?#]+?)(?:v\d+)?$", landing)
            if am:
                art.arxiv_id = am.group(1)
        if loc.get("pdf_url") or landing:
            art.locations.append(
                Location(
                    pdf_url=loc.get("pdf_url"),
                    landing_url=landing,
                    version=loc.get("version"),
                    is_oa=bool(loc.get("is_oa")),
                    source="openalex",
                    host=(loc.get("source") or {}).get("display_name"),
                )
            )
    return art


def parse_crossref(m: dict) -> Article:
    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (m.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    authors = []
    for a in m.get("author", []):
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    art = Article(
        title=(m.get("title") or [None])[0],
        doi=clean_doi(m["DOI"]) if m.get("DOI") else None,
        year=year,
        authors=authors,
        venue=(m.get("container-title") or [None])[0],
    )
    for link in m.get("link", []):
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            # Crossref full-text links are publisher-hosted; usually need a subscription.
            art.locations.append(
                Location(pdf_url=link["URL"], landing_url=None, version="publishedVersion",
                         is_oa=False, source="crossref", host=m.get("publisher"))
            )
    return art


def title_similarity(query: str, title: str | None) -> float:
    from difflib import SequenceMatcher

    norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))
    return SequenceMatcher(None, norm(query), norm(title)).ratio()


ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def parse_arxiv_feed(xml_text: str) -> list[Article]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for entry in root.findall("a:entry", ATOM_NS):
        m = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", entry.findtext("a:id", "", ATOM_NS))
        if m and entry.find("a:title", ATOM_NS) is not None:
            art = Article(arxiv_id=m.group(1), is_oa=True, venue="arXiv")
            _fill_arxiv(art, entry)
            out.append(art)
    return out


def parse_arxiv_atom(xml_text: str, arxiv_id: str) -> Article:
    ns = ATOM_NS
    art = Article(arxiv_id=arxiv_id, is_oa=True, venue="arXiv")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return art
    entry = root.find("a:entry", ns)
    if entry is None or entry.find("a:title", ns) is None:
        return art
    _fill_arxiv(art, entry)
    return art


def _fill_arxiv(art: Article, entry) -> None:
    ns = ATOM_NS
    art.title = " ".join((entry.findtext("a:title", "", ns)).split())
    published = entry.findtext("a:published", "", ns)
    art.year = int(published[:4]) if published[:4].isdigit() else None
    art.authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]
    doi = entry.findtext("x:doi", None, ns)
    if doi:
        art.doi = clean_doi(doi)
