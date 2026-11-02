"""Classify whatever the user typed: DOI, arXiv id, PMID, PMCID, URL, or free text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
ARXIV_NEW_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_OLD_RE = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
PMCID_RE = re.compile(r"\b(PMC\d{4,9})\b", re.I)
PMID_RE = re.compile(r"^(?:pmid\s*:?\s*)?(\d{1,9})$", re.I)


@dataclass(frozen=True)
class Identifier:
    kind: str  # doi | arxiv | pmid | pmcid | url | query
    value: str


def clean_doi(doi: str) -> str:
    doi = unquote(doi.strip())
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    # Trailing punctuation from copy-pasting a citation; keep balanced parentheses.
    while doi and doi[-1] in ".,;:]>'\"":
        doi = doi[:-1]
    if doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    for suffix in ("/full", "/abstract", "/pdf", "/epdf", ".pdf"):
        if doi.lower().endswith(suffix):
            doi = doi[: -len(suffix)]
    return doi.lower()


def parse(text: str) -> Identifier:
    s = text.strip()
    low = s.lower()

    arxiv_url = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?(?:[?#]|$)", s, re.I)
    if arxiv_url:
        return Identifier("arxiv", re.sub(r"v\d+$", "", arxiv_url.group(1)))
    if low.startswith("arxiv"):
        rest = re.sub(r"^arxiv\s*:?\s*", "", s, flags=re.I)
        for rx in (ARXIV_NEW_RE, ARXIV_OLD_RE):
            m = rx.fullmatch(rest)
            if m:
                return Identifier("arxiv", m.group(1))
    if re.search(r"10\.48550/arxiv\.", low):
        return Identifier("arxiv", re.split(r"arxiv\.", s, flags=re.I)[-1])

    m = DOI_RE.search(s)
    if m and (" " not in s.strip() or low.startswith("doi") or "doi.org" in low):
        return Identifier("doi", clean_doi(m.group(1)))

    m = PMCID_RE.search(s)
    if m and len(s.split()) <= 2:
        return Identifier("pmcid", m.group(1).upper())

    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", s)
    if m:
        return Identifier("pmid", m.group(1))
    m = PMID_RE.match(s)
    if m:
        return Identifier("pmid", m.group(1))

    m = ARXIV_NEW_RE.fullmatch(s)
    if m:
        return Identifier("arxiv", m.group(1))

    if re.match(r"^https?://", s):
        return Identifier("url", s)
    # A DOI buried inside a longer citation string is still the best handle.
    m = DOI_RE.search(s)
    if m:
        return Identifier("doi", clean_doi(m.group(1)))
    return Identifier("query", s)
