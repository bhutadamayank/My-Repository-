"""Configuration: defaults <- config file <- environment variables <- CLI flags."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("PAPERFETCH_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "paperfetch" / "config.toml"
)

TEMPLATE = """\
# paperfetch configuration
# Your e-mail is sent to Unpaywall / OpenAlex / Crossref as a courtesy contact
# (their "polite pool"). Unpaywall will not answer without one.
email = "you@university.edu"

# Where PDFs are saved.
download_dir = "~/Papers"

# Order of preference when several copies exist:
#   "best"      published OA > publisher via institution > accepted manuscript > preprint
#   "oa"        any open-access copy before trying the publisher
#   "publisher" publisher's version of record first
prefer = "best"

# Off campus? Put your library's EZproxy prefix here, e.g.
# proxy_prefix = "https://login.ezproxy.myuniversity.edu/login?url="
# and export your logged-in browser cookies (Netscape cookies.txt format):
# cookies_file = "~/cookies.txt"

# Optional API keys
# elsevier_api_key = ""          # https://dev.elsevier.com  (entitlement is checked by your IP)
# semantic_scholar_api_key = ""
# openalex_api_key = ""
"""


@dataclass
class Config:
    email: str | None = None
    download_dir: Path = field(default_factory=lambda: Path.cwd() / "papers")
    prefer: str = "best"
    proxy_prefix: str | None = None
    cookies_file: Path | None = None
    elsevier_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    openalex_api_key: str | None = None
    timeout: float = 30.0
    use_institution: bool = True
    use_oa: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls()
        path = path or CONFIG_PATH
        data: dict = {}
        if path.is_file():
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        env = {
            "email": "PAPERFETCH_EMAIL",
            "download_dir": "PAPERFETCH_DIR",
            "prefer": "PAPERFETCH_PREFER",
            "proxy_prefix": "PAPERFETCH_PROXY_PREFIX",
            "cookies_file": "PAPERFETCH_COOKIES",
            "elsevier_api_key": "ELSEVIER_API_KEY",
            "semantic_scholar_api_key": "S2_API_KEY",
            "openalex_api_key": "OPENALEX_API_KEY",
        }
        for key, var in env.items():
            if os.environ.get(var):
                data[key] = os.environ[var]
        for key, value in data.items():
            if not hasattr(cfg, key) or value in ("", None):
                continue
            if key in ("download_dir", "cookies_file"):
                value = Path(str(value)).expanduser()
            if key == "timeout":
                value = float(value)
            setattr(cfg, key, value)
        if cfg.prefer not in ("best", "oa", "publisher"):
            cfg.prefer = "best"
        return cfg


def write_template(path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(TEMPLATE)
    return path
