# paperfetch

Ask for an article — by title, DOI, arXiv id, PMID, PMCID or URL — and get its PDF.
It tries **open-access copies** and **your institution's subscriptions** (the publisher recognises your
campus Wi-Fi / VPN IP address, exactly like your browser does), validates that the file really is a
PDF, names it sensibly and logs it.

```text
$ paperfetch "attention is all you need"
Searching for: attention is all you need
• Vaswani et al. (2017). Attention Is All You Need. arXiv
      arXiv:1706.03762  [open access]
✓ Saved papers/Vaswani_2017_Attention_Is_All_You_Need.pdf
    from arxiv (open access, preprint)
```

## Install

Python ≥ 3.11.

```bash
git clone <this repo> && cd My-Repository-
pip install -e .          # installs the `paperfetch` command (only dependency: requests)
paperfetch init           # writes ~/.config/paperfetch/config.toml — put your e-mail in it
paperfetch check          # on campus Wi-Fi: confirms the publisher sees your institution
```

**Run it on your own computer, connected to the institution's network (or its VPN).** Subscription
access is granted to that network's IP address; a cloud server or home connection will only get
open-access copies (unless you configure EZproxy, below).

## Use

| You want | Command |
|---|---|
| Article by title (asks you to pick if ambiguous) | `paperfetch "deep learning lecun 2015"` |
| By identifier | `paperfetch 10.1038/nature14539` · `paperfetch arXiv:1706.03762` · `paperfetch PMC4795549` · `paperfetch "PMID 26017442"` |
| Several at once | `paperfetch 10.1038/nature14539 arXiv:1706.03762` |
| A reading list (one per line, `#` comments) | `paperfetch batch list.txt` |
| Ask again and again (interactive) | `paperfetch` → `article> …` |
| Only look, don't download | `paperfetch search "crispr base editing"` · `paperfetch <id> --list` |
| Choose which copy wins | `--prefer best` (default) · `--prefer oa` · `--prefer publisher` |
| Restrict | `--oa-only` · `--publisher-only` |
| Elsewhere | `-o ~/thesis/refs` · `-y` (never prompt) · `--open` (open the page in your browser on failure) · `-v` (show every hop) |

PDFs are saved as `FirstAuthor_Year_Title_Words.pdf`, with a `library.jsonl` log (metadata, source,
version) in the same folder; an article already in the library is not downloaded twice (`--force` to redo).

## How it decides (the pipeline)

1. **Identify** — parse the input; free text is searched in OpenAlex (falls back to Crossref) and
   arXiv; near-exact title matches are promoted, otherwise you choose from a list.
2. **Collect candidate copies** from
   OpenAlex locations · Unpaywall · Semantic Scholar · Europe PMC / PubMed Central · arXiv ·
   the publisher (DOI landing page, DOI-prefix rules, Crossref full-text links, optional Elsevier API).
3. **Rank** — default `best`:
   published open-access version → publisher via institution → accepted manuscript → preprint.
4. **Fetch** — each candidate is downloaded; if it returns an HTML page, the tool reads it like a
   browser would (`citation_pdf_url` meta tag, PDF links, redirects) plus platform rules for
   ScienceDirect, Wiley, Springer, Nature, IEEE, IOP, RSC, APS, JSTOR, Cell/Lancet and Atypon sites
   (ACS, T&F, SAGE, Science, NEJM, PNAS…). The result is accepted only if it starts with `%PDF-`
   and is complete.
5. **Explain failures** — every hop and its outcome; the article link to open manually.

## Off campus: EZproxy

Most university libraries run EZproxy. In `config.toml`:

```toml
proxy_prefix = "https://login.ezproxy.youruniversity.edu/login?url="
cookies_file = "~/cookies.txt"   # export from your logged-in browser (Netscape cookies.txt format)
```

Publisher requests are then routed through the proxy using your login session.

## Configuration reference (`~/.config/paperfetch/config.toml`)

| Key | Env var | Purpose |
|---|---|---|
| `email` | `PAPERFETCH_EMAIL` | Contact address required by Unpaywall; also the "polite pool" for OpenAlex/Crossref |
| `download_dir` | `PAPERFETCH_DIR` | Default folder (otherwise `./papers`) |
| `prefer` | `PAPERFETCH_PREFER` | `best` / `oa` / `publisher` |
| `proxy_prefix`, `cookies_file` | `PAPERFETCH_PROXY_PREFIX`, `PAPERFETCH_COOKIES` | EZproxy |
| `elsevier_api_key` | `ELSEVIER_API_KEY` | Free key from dev.elsevier.com; full text when your IP is entitled (1-page previews are rejected) |
| `openalex_api_key` | `OPENALEX_API_KEY` | OpenAlex now meters usage per day; a free key raises the limit |
| `semantic_scholar_api_key` | `S2_API_KEY` | Higher Semantic Scholar rate limit |

## Ground rules (by design)

* Only legitimate routes: open access, and subscriptions your institution pays for. No shadow libraries.
* The tool identifies itself honestly (`paperfetch` user agent) and **does not try to defeat bot
  checks, CAPTCHAs or rate limits** — when a publisher (often ScienceDirect) insists on a real
  browser, you get the link to open yourself. No 429 is ever retried.
* Respect your licence: institutional subscriptions usually forbid bulk/systematic downloading.
  `batch` is for a reading list, not for harvesting a journal.

## Using it from Claude Code

`.claude/skills/fetch-paper/` teaches Claude Code to use this tool: in a Claude Code session on your
own (campus-connected) machine, just say *"get me the PDF of the AlphaFold 2 paper"*.

## Development

```bash
pip install -e '.[dev]'
pytest -q          # offline tests: parsing, publisher rules, ranking, downloader with a fake HTTP layer
```
