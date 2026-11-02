"""Turn a publisher landing page into direct PDF URLs.

Two complementary strategies:
  1. Generic: <meta name="citation_pdf_url"> (Google Scholar convention, used by
     most publishers), <link type="application/pdf">, meta-refresh, JS redirects,
     iframes/embeds and obvious PDF anchors.
  2. Platform rules: URL rewrites for big platforms whose pages hide the PDF
     link (ScienceDirect, Wiley, Springer, Nature, Atypon sites, IEEE, ...).

Rules rewrite the *path* but keep the scheme+host of the page we actually
landed on, so they keep working behind an EZproxy hostname such as
www-sciencedirect-com.ezproxy.myuni.edu.
"""

from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

PDF_HREF_RE = re.compile(
    r"(\.pdf(?:$|[?#]))|/pdf(?:direct|ft)?/|/content/pdf/|stampPDF|showPdf|articlepdf|/pdfft\b|blobtype=pdf",
    re.I,
)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta_pdf: list[str] = []
        self.refresh: list[str] = []
        self.alt_pdf: list[str] = []
        self.frames: list[str] = []
        self.anchors: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("citation_pdf_url", "bepress_citation_pdf_url", "eprints.document_url") and a.get("content"):
                self.meta_pdf.append(a["content"])
            if a.get("http-equiv", "").lower() == "refresh":
                m = re.search(r"url\s*=\s*['\"]?([^'\";]+)", a.get("content", ""), re.I)
                if m:
                    self.refresh.append(m.group(1).strip())
        elif tag == "link":
            if a.get("type", "").lower() == "application/pdf" and a.get("href"):
                self.alt_pdf.append(a["href"])
        elif tag in ("iframe", "embed", "object"):
            src = a.get("src") or a.get("data")
            if src and PDF_HREF_RE.search(src):
                self.frames.append(src)
        elif tag == "a":
            href = a.get("href", "")
            if href and PDF_HREF_RE.search(href) and not href.startswith(("javascript:", "mailto:")):
                self.anchors.append(href)


JS_REDIRECT_RE = re.compile(
    r"""(?:window\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]|location\.replace\(\s*['"]([^'"]+)['"]""",
    re.I,
)
SD_ASSET_RE = re.compile(r"https:(?:\\?/){2}pdf\.sciencedirectassets\.com[^\"'\s<>]+")
ELSEVIER_REDIRECT_RE = re.compile(r"""name=["']redirectURL["'][^>]*value=["']([^"']+)""", re.I)


def extract_pdf_links(page_url: str, html: str, max_anchors: int = 6) -> list[str]:
    p = _LinkParser()
    try:
        p.feed(html)
    except Exception:  # malformed HTML: use what we got
        pass
    found: list[str] = []
    found += p.meta_pdf + p.alt_pdf + p.refresh + p.frames
    for m in SD_ASSET_RE.finditer(html):
        found.append(m.group(0).replace("\\/", "/"))
    for m in JS_REDIRECT_RE.finditer(html):
        target = m.group(1) or m.group(2)
        if target and not target.startswith("#"):
            found.append(target)
    for m in ELSEVIER_REDIRECT_RE.finditer(html):
        found.append(htmllib.unescape(_unquote(m.group(1))))
    found += rewrite_rules(page_url)
    found += p.anchors[:max_anchors]
    return _dedupe(urljoin(page_url, htmllib.unescape(u.strip())) for u in found if u.strip())


def _unquote(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


def _dedupe(urls) -> list[str]:
    seen, out = set(), []
    for u in urls:
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def rewrite_rules(url: str) -> list[str]:
    """Platform-specific landing-page -> PDF rewrites."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path
    origin = f"{parts.scheme}://{parts.netloc}"
    out: list[str] = []

    m = re.search(r"/pii/([A-Z0-9()\-]+)", path, re.I) if ("sciencedirect" in host or "linkinghub" in host) else None
    if m:
        pii = re.sub(r"[()\-]", "", m.group(1))
        base = origin if "sciencedirect" in host else "https://www.sciencedirect.com"
        out.append(f"{base}/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true")

    if "cell.com" in host or "thelancet.com" in host:
        m = re.search(r"/(?:fulltext|abstract|article)/(S[\d\-()X]+)", path, re.I) or re.search(r"pii=(S[\d\-()X]+)", parts.query, re.I)
        if m:
            out.append(f"{origin}/action/showPdf?pii={m.group(1)}")

    m = re.match(r"^/doi/(?:abs/|full/|epdf/|epub/|reader/|book/)?(10\..+)$", path)
    if m:
        doi = m.group(1)
        if "wiley" in host:
            out += [f"{origin}/doi/pdfdirect/{doi}?download=true", f"{origin}/doi/pdf/{doi}"]
        else:  # Atypon platform: ACS, T&F, SAGE, Science, NEJM, PNAS, AHA, ASCO, ...
            out += [f"{origin}/doi/pdf/{doi}?download=true", f"{origin}/doi/pdf/{doi}"]

    if "springer" in host:
        m = re.match(r"^/(?:article|chapter|book|referenceworkentry|protocol)/(10\..+)$", path)
        if m:
            out.append(f"{origin}/content/pdf/{m.group(1)}.pdf")

    if "nature.com" in host:
        m = re.match(r"^/articles/([^/.?]+)$", path)
        if m:
            out.append(f"{origin}/articles/{m.group(1)}.pdf")

    if "ieeexplore" in host:
        m = re.match(r"^/(?:abstract/)?document/(\d+)", path)
        if m:
            out.append(f"{origin}/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}")

    if "iopscience" in host:
        m = re.match(r"^/article/(10\.[^/]+/.+?)(?:/meta|/pdf)?$", path)
        if m:
            out.append(f"{origin}/article/{m.group(1)}/pdf")

    if "pubs.rsc.org" in host and "articlelanding" in path:
        out.append(origin + path.replace("articlelanding", "articlepdf"))

    if "journals.aps.org" in host:
        m = re.match(r"^/(\w+)/abstract/(10\..+)$", path)
        if m:
            out.append(f"{origin}/{m.group(1)}/pdf/{m.group(2)}")

    if "jstor.org" in host:
        m = re.match(r"^/stable/(\d+)", path)
        if m:
            out.append(f"{origin}/stable/pdf/{m.group(1)}.pdf")

    if "arxiv.org" in host:
        m = re.match(r"^/abs/(.+)$", path)
        if m:
            out.append(f"{origin}/pdf/{m.group(1)}")

    return out


# DOI prefix -> direct PDF URL, used as a fallback when the landing page itself
# is unreachable (e.g. blocked by a JavaScript bot check).
DOI_PREFIX_RULES: list[tuple[tuple[str, ...], str]] = [
    (("10.1002/", "10.1111/", "10.1113/"), "https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true"),
    (("10.1007/", "10.1186/", "10.1140/"), "https://link.springer.com/content/pdf/{doi}.pdf"),
    (("10.1021/",), "https://pubs.acs.org/doi/pdf/{doi}"),
    (("10.1080/",), "https://www.tandfonline.com/doi/pdf/{doi}?download=true"),
    (("10.1177/",), "https://journals.sagepub.com/doi/pdf/{doi}"),
    (("10.1126/",), "https://www.science.org/doi/pdf/{doi}"),
    (("10.1056/",), "https://www.nejm.org/doi/pdf/{doi}"),
    (("10.1073/",), "https://www.pnas.org/doi/pdf/{doi}"),
    (("10.1161/",), "https://www.ahajournals.org/doi/pdf/{doi}"),
    (("10.1200/",), "https://ascopubs.org/doi/pdf/{doi}"),
    (("10.1088/",), "https://iopscience.iop.org/article/{doi}/pdf"),
    (("10.1103/",), "https://link.aps.org/pdf/{doi}"),
]


def doi_prefix_urls(doi: str) -> list[str]:
    out = []
    for prefixes, template in DOI_PREFIX_RULES:
        if doi.startswith(prefixes):
            out.append(template.format(doi=doi))
    if doi.startswith("10.1038/"):
        out.append(f"https://www.nature.com/articles/{doi.split('/', 1)[1]}.pdf")
    return out


def looks_like_bot_wall(html: str) -> bool:
    h = html[:20000].lower()
    return any(s in h for s in ("just a moment...", "cf-challenge", "challenge-platform", "are you a robot", "captcha"))
