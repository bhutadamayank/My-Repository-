"""Offline tests: no network access needed."""

from pathlib import Path

import pytest

from paperfetch.config import Config
from paperfetch.downloader import Downloader, Library, safe_filename
from paperfetch.identifiers import clean_doi, parse
from paperfetch.metadata import Article, parse_arxiv_feed, parse_crossref, parse_openalex, title_similarity
from paperfetch.publishers import figshare_article_id, figshare_pdf_urls, doi_prefix_urls, extract_pdf_links, looks_like_bot_wall, rewrite_rules
from paperfetch.sources import Candidate, SourceFinder


@pytest.mark.parametrize(
    "text,kind,value",
    [
        ("10.1038/nature14539", "doi", "10.1038/nature14539"),
        ("https://doi.org/10.1038/NATURE14539", "doi", "10.1038/nature14539"),
        ("doi: 10.1016/j.cell.2020.02.052.", "doi", "10.1016/j.cell.2020.02.052"),
        ("Smith J. Some title. Nature. 2015;521:436. doi:10.1038/nature14539", "doi", "10.1038/nature14539"),
        ("arXiv:1706.03762", "arxiv", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762v7", "arxiv", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762.pdf", "arxiv", "1706.03762"),
        ("1706.03762", "arxiv", "1706.03762"),
        ("10.48550/arXiv.1706.03762", "arxiv", "1706.03762"),
        ("arxiv hep-th/9901001", "arxiv", "hep-th/9901001"),
        ("PMC4795549", "pmcid", "PMC4795549"),
        ("PMID: 26017442", "pmid", "26017442"),
        ("26017442", "pmid", "26017442"),
        ("https://pubmed.ncbi.nlm.nih.gov/26017442/", "pmid", "26017442"),
        ("https://www.example.org/paper/42", "url", "https://www.example.org/paper/42"),
        ("deep learning lecun nature", "query", "deep learning lecun nature"),
    ],
)
def test_parse(text, kind, value):
    ident = parse(text)
    assert (ident.kind, ident.value) == (kind, value)


def test_clean_doi_keeps_balanced_parens():
    assert clean_doi("10.1016/S0140-6736(20)30183-5") == "10.1016/s0140-6736(20)30183-5"
    assert parse("(doi: 10.1016/S0140-6736(20)30183-5)").value == "10.1016/s0140-6736(20)30183-5"


def test_rewrite_rules():
    r = rewrite_rules
    assert r("https://www.sciencedirect.com/science/article/pii/S0092867420302294")[0] == (
        "https://www.sciencedirect.com/science/article/pii/S0092867420302294/pdfft?isDTMRedir=true&download=true")
    assert "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/anie.1?download=true" in r(
        "https://onlinelibrary.wiley.com/doi/full/10.1002/anie.1")
    assert r("https://link.springer.com/article/10.1007/s00125-1") == [
        "https://link.springer.com/content/pdf/10.1007/s00125-1.pdf"]
    assert r("https://www.nature.com/articles/nature14539") == ["https://www.nature.com/articles/nature14539.pdf"]
    assert r("https://pubs.acs.org/doi/10.1021/acs.x.1")[0] == "https://pubs.acs.org/doi/pdf/10.1021/acs.x.1?download=true"
    assert r("https://ieeexplore.ieee.org/document/12345") == [
        "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=12345"]
    assert r("https://www.cell.com/cell/fulltext/S0092-8674(20)30229-4") == [
        "https://www.cell.com/action/showPdf?pii=S0092-8674(20)30229-4"]


def test_rewrite_rules_keep_ezproxy_host():
    out = rewrite_rules("https://onlinelibrary-wiley-com.ezproxy.uni.edu/doi/10.1111/j.1")
    assert out[0].startswith("https://onlinelibrary-wiley-com.ezproxy.uni.edu/doi/pdfdirect/10.1111/j.1")


def test_doi_prefix_urls():
    assert doi_prefix_urls("10.1038/s41586-020-2649-2") == ["https://www.nature.com/articles/s41586-020-2649-2.pdf"]
    assert doi_prefix_urls("10.9999/unknown") == []


def test_extract_pdf_links():
    html = """<html><head>
      <meta name="citation_pdf_url" content="/content/paper.pdf">
      <meta http-equiv="refresh" content="0; url=https://cdn.example.org/x.pdf">
      </head><body>
      <a href="/about">About</a> <a href="/doi/pdf/10.1/xyz">PDF</a>
      <script>window.location.href = 'https://pdf.example.org/final.pdf';</script>
      </body></html>"""
    links = extract_pdf_links("https://journal.example.org/article/1", html)
    assert links[0] == "https://journal.example.org/content/paper.pdf"
    assert "https://cdn.example.org/x.pdf" in links
    assert "https://journal.example.org/doi/pdf/10.1/xyz" in links
    assert "https://pdf.example.org/final.pdf" in links
    assert all("about" not in u for u in links)


def test_bot_wall():
    assert looks_like_bot_wall("<title>Just a moment...</title>")
    assert not looks_like_bot_wall("<title>An article</title>")


OPENALEX = {
    "title": "Deep learning", "doi": "https://doi.org/10.1038/nature14539", "publication_year": 2015,
    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/26017442", "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/123456"},
    "authorships": [{"author": {"display_name": "Yann LeCun"}}, {"author": {"display_name": "Yoshua Bengio"}}],
    "primary_location": {"source": {"display_name": "Nature"}},
    "open_access": {"is_oa": True},
    "locations": [
        {"pdf_url": None, "landing_page_url": "https://www.nature.com/articles/nature14539", "is_oa": False},
        {"pdf_url": "https://arxiv.org/pdf/1234.5678", "landing_page_url": "https://arxiv.org/abs/1234.5678v2",
         "is_oa": True, "version": "submittedVersion", "source": {"display_name": "arXiv"}},
    ],
}


def test_parse_openalex():
    a = parse_openalex(OPENALEX)
    assert (a.doi, a.year, a.pmid, a.pmcid, a.arxiv_id) == ("10.1038/nature14539", 2015, "26017442", "PMC123456", "1234.5678")
    assert a.citation().startswith("LeCun et al. (2015). Deep learning")


def test_parse_crossref():
    a = parse_crossref({"DOI": "10.1/ABC", "title": ["T"], "issued": {"date-parts": [[2020, 1]]},
                        "author": [{"given": "Ada", "family": "Lovelace"}],
                        "link": [{"URL": "https://pub.example/x.pdf", "content-type": "application/pdf"}]})
    assert (a.doi, a.year, a.authors) == ("10.1/abc", 2020, ["Ada Lovelace"])
    assert a.locations[0].pdf_url == "https://pub.example/x.pdf" and not a.locations[0].is_oa


def test_parse_arxiv_feed():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is
      All You Need</title><published>2017-06-12T00:00:00Z</published>
      <author><name>Ashish Vaswani</name></author><arxiv:doi>10.5555/X</arxiv:doi></entry></feed>"""
    (a,) = parse_arxiv_feed(xml)
    assert (a.arxiv_id, a.title, a.year, a.doi) == ("1706.03762", "Attention Is All You Need", 2017, "10.5555/x")


def test_title_similarity():
    assert title_similarity("attention is all you need", "Attention Is All You Need") == 1.0
    assert title_similarity("attention is all you need", "All You Need Is LSD") < 0.85


def test_ranking_and_ezproxy():
    cfg = Config(email=None, proxy_prefix="https://ez.uni.edu/login?url=")
    finder = SourceFinder(session=None, cfg=cfg)
    art = parse_openalex(OPENALEX)
    cands = finder.find(art)  # no network: unpaywall skipped (no email), S2 would need the session
    labels = [(c.source, c.access, c.version) for c in cands]
    # Version of record via institution comes before the preprint.
    assert labels.index(("publisher", "institutional", "publishedVersion")) < labels.index(("openalex", "open", "submittedVersion"))
    pub = [c for c in cands if c.access == "institutional"]
    assert all(c.url.startswith("https://ez.uni.edu/login?url=") for c in pub)
    assert cands[0].source == "europepmc"  # PMC copy = published version, open

    cfg.prefer = "oa"
    cands = finder.find(art)
    assert all(c.access == "open" for c in cands[: len(cands) - len(pub)])


SourceFinder.semantic_scholar = lambda self, art: []  # keep tests offline
SourceFinder.hal = lambda self, doi: []


# ---------------- downloader with a fake HTTP layer ----------------

class FakeResp:
    def __init__(self, url, status=200, body=b"", ctype="text/html"):
        self.url, self.status_code, self._body = url, status, body
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        self.encoding = "utf-8"

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.routes.get(url) or FakeResp(url, 404, b"<html>nope</html>")


PDF = b"%PDF-1.7\n" + b"x" * 5000 + b"\n%%EOF"


def test_downloader_follows_landing_page(tmp_path: Path):
    landing = "https://pub.example.org/article/1"
    s = FakeSession({
        "https://doi.org/10.1/x": FakeResp(landing, 200, b'<meta name="citation_pdf_url" content="/a.pdf">'),
        "https://pub.example.org/a.pdf": FakeResp("https://pub.example.org/a.pdf", 200, PDF, "application/pdf"),
    })
    d = Downloader(s, Config())
    res = d.fetch([Candidate("https://doi.org/10.1/x", "publisher", "institutional")], tmp_path / "x.pdf")
    assert res.path and res.path.read_bytes() == PDF


def test_downloader_rejects_html_paywall_and_reports(tmp_path: Path):
    s = FakeSession({"https://doi.org/10.1/x": FakeResp("https://pub.example.org/a", 200, b"<html>Buy now</html>")})
    res = Downloader(s, Config()).fetch([Candidate("https://doi.org/10.1/x", "publisher", "institutional")], tmp_path / "x.pdf")
    assert res.path is None and "not entitled" in res.attempts[-1][1]
    assert not list(tmp_path.iterdir())


def test_downloader_respects_rate_limit(tmp_path: Path):
    s = FakeSession({"https://a.example/x": FakeResp("https://a.example/x", 429, b"slow down")})
    res = Downloader(s, Config()).fetch([Candidate("https://a.example/x", "x", "open")], tmp_path / "x.pdf")
    assert res.path is None and res.bot_walls and s.calls == ["https://a.example/x"]


def test_filename_and_library(tmp_path: Path):
    art = Article(title="Ünïcode: a study/of things?", year=2021, authors=["José Núñez"], doi="10.1/x")
    name = safe_filename(art)
    assert name == "Nunez_2021_Unicode_a_study_of_things.pdf"
    lib = Library(tmp_path)
    (tmp_path / name).write_bytes(PDF)
    lib.add(art, tmp_path / name, Candidate("u", "s", "open"))
    assert lib.find(Article(doi="10.1/x")) == tmp_path / name
    assert lib.find(Article(doi="10.1/other")) is None


def test_figshare():
    url = "https://outputs.ncl.ac.uk/articles/journal_contribution/Some_title/34272276/1"
    assert figshare_article_id(url) == "34272276"
    assert figshare_article_id("https://figshare.com/articles/dataset/x/152356") == "152356"
    assert figshare_article_id("https://www.nature.com/articles/nature14539") is None
    data = {"files": [{"name": "paper.PDF", "download_url": "https://ndownloader.figshare.com/files/1"},
                      {"name": "data.csv", "download_url": "https://ndownloader.figshare.com/files/2"}]}
    assert figshare_pdf_urls(data) == ["https://ndownloader.figshare.com/files/1"]
    assert figshare_pdf_urls({"files": []}) == []


def test_empty_202_is_reported_as_challenge(tmp_path: Path):
    s = FakeSession({"https://repo.example.org/item/9": FakeResp("https://repo.example.org/item/9", 202, b"")})
    res = Downloader(s, Config()).fetch([Candidate("https://repo.example.org/item/9", "x", "open")], tmp_path / "x.pdf")
    assert res.path is None and res.bot_walls
