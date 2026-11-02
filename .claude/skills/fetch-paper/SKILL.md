---
name: fetch-paper
description: Find and download the PDF of a scholarly article (open access, or through the user's institutional subscription) using the paperfetch tool in this repository. Use whenever the user asks to get, fetch, download, or find the PDF of a paper/article, given a title, DOI, arXiv id, PMID, PMCID or URL.
---

# Fetch a paper with paperfetch

1. Make sure the tool is installed: `paperfetch --version` (otherwise `pip install -e .` from the repo root).
2. If the user gave a vague description rather than an exact title or identifier, first list matches:
   `paperfetch search "<description>"` and pick the one that fits (ask the user if several fit).
3. Download non-interactively, preferring an identifier when you have one:
   `paperfetch "<DOI | arXiv id | PMID | PMCID | exact title>" -y`
   - Several articles: pass each one as a separately quoted argument, or write them to a file and run `paperfetch batch <file> -y`.
   - `--list` shows which sources would be tried without downloading; `-v` explains every hop.
4. Report the saved path and which source it came from (open-access version vs. publisher via institution;
   note when the copy is a preprint or accepted manuscript rather than the published version).
5. If it fails, relay the tool's reason. Do not try to get around bot checks, rate limits, or paywalls,
   and never use shadow libraries: give the user the article link to open in a browser on their
   institution's network, and suggest `paperfetch check` to diagnose institutional access.
