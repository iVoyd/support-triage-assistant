"""Pull Markdown docs, chunk them by heading, and retrieve chunks with BM25."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

MAX_CHUNK_CHARS = 2000
HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*#*\s*$")
CUSTOM_ANCHOR = re.compile(r"\s*(?:\[#([\w-]+)\]|\{#([\w-]+)\})\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
TOKEN = re.compile(r"[a-z0-9_]+")
STOPWORDS = set(
    "a an and are as at be but by can do does for from has have how i if in into is it its "
    "me my not of on or so that the then there this to was we when where which with you your "
    "after before using use get".split()
)


@dataclass
class Chunk:
    path: str
    heading: str
    url: str
    text: str


# ---------- source ----------


def fetch_source(source: str, subdir: str, cache_dir: Path) -> Path:
    """Return the local docs folder, cloning a git URL (shallow, sparse) if needed."""
    if not re.match(r"^(https?://|git@)", source):
        root = Path(source).expanduser()
        return root / subdir if subdir else root
    name = re.sub(r"[^\w.-]+", "_", source.rstrip("/").removesuffix(".git"))
    dest = cache_dir / name
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "pull", "-q", "--depth", "1"], check=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone = ["git", "clone", "-q", "--depth", "1", "--filter=blob:none"]
        if subdir:
            clone.append("--sparse")
        subprocess.run([*clone, source, str(dest)], check=True)
    if subdir:
        subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", subdir], check=True)
    return dest / subdir if subdir else dest


# ---------- chunking ----------


def slugify(heading: str) -> str:
    """GitHub-style anchor: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"`|\*|_(?=\w)|\[|\]\([^)]*\)", "", heading.lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text.strip())


def page_url(rel_path: str, url_base: str) -> str:
    stem = re.sub(r"\.mdx?$", "", rel_path)
    stem = re.sub(r"(^|/)index$", "", stem)
    return f"{url_base}/{stem}".rstrip("/")


def _strip_frontmatter(text: str) -> tuple[str, str | None]:
    if not text.startswith("---"):
        return text, None
    end = text.find("\n---", 3)
    if end == -1:
        return text, None
    front, rest = text[3:end], text[end + 4 :]
    m = re.search(r"^title:\s*['\"]?(.*?)['\"]?\s*$", front, re.M)
    return rest, (m.group(1) if m else None)


def _clean(lines: list[str]) -> str:
    """Drop MDX import/export lines and collapse blank runs."""
    kept = [ln for ln in lines if not re.match(r"^(import|export)\s", ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) > MAX_CHUNK_CHARS:
            parts.append(current.strip())
            current = ""
        current += para + "\n\n"
    if current.strip():
        parts.append(current.strip())
    return parts


def chunk_markdown(text: str, rel_path: str, url_base: str) -> list[Chunk]:
    """Split one Markdown/MDX file into chunks at #, ## and ### headings."""
    body, title = _strip_frontmatter(text)
    base = page_url(rel_path, url_base)
    sections: list[tuple[str, str, list[str]]] = [(title or rel_path, "", [])]
    in_fence = False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            heading = m.group(2)
            custom = CUSTOM_ANCHOR.search(heading)
            if custom:
                anchor = custom.group(1) or custom.group(2)
                heading = heading[: custom.start()]
            else:
                anchor = slugify(heading)
            sections.append((heading.strip(), anchor, []))
        else:
            sections[-1][2].append(line)

    chunks = []
    for heading, anchor, lines in sections:
        content = _clean(lines)
        if len(content) < 20:
            continue
        url = f"{base}#{anchor}" if anchor else base
        for part in _split_long(content):
            chunks.append(Chunk(path=rel_path, heading=heading, url=url, text=part))
    return chunks


def chunk_folder(root: Path, url_base: str) -> list[Chunk]:
    chunks = []
    for path in sorted(root.rglob("*")):
        if path.suffix in (".md", ".mdx") and path.is_file():
            rel = path.relative_to(root).as_posix()
            chunks.extend(chunk_markdown(path.read_text(errors="replace"), rel, url_base))
    return chunks


# ---------- storage ----------


def store_chunks(conn: sqlite3.Connection, source: str, chunks: list[Chunk]) -> None:
    conn.execute("DELETE FROM doc_chunks WHERE source = ?", (source,))
    conn.executemany(
        "INSERT INTO doc_chunks (source, path, heading, url, text) VALUES (?, ?, ?, ?, ?)",
        [(source, c.path, c.heading, c.url, c.text) for c in chunks],
    )
    conn.commit()


def load_chunks(conn: sqlite3.Connection, source: str) -> list[Chunk]:
    rows = conn.execute(
        "SELECT path, heading, url, text FROM doc_chunks WHERE source = ? ORDER BY id", (source,)
    )
    return [Chunk(**dict(r)) for r in rows]


# ---------- retrieval ----------


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


class Retriever:
    """BM25 over chunk heading + text. Built in memory; a few thousand chunks is fast."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        corpus = [tokenize(f"{c.heading} {c.heading} {c.text}") for c in chunks]
        self.bm25 = BM25Okapi(corpus) if chunks else None

    def search(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        if not self.bm25 or not terms:
            return []
        scores = self.bm25.get_scores(terms)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results, seen_urls = [], set()
        for i in ranked:
            if scores[i] <= 0 or len(results) == k:
                break
            chunk = self.chunks[i]
            if chunk.url in seen_urls:  # long sections are split; one hit per URL
                continue
            seen_urls.add(chunk.url)
            results.append((chunk, float(scores[i])))
        return results
