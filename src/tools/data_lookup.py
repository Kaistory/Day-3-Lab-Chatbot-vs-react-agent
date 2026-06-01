from pathlib import Path
import csv
import json
import re

from pypdf import PdfReader


DATA_DIR = Path("data")
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf"}
MAX_SNIPPETS = 5
MAX_SNIPPET_CHARS = 1200


def _read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[{path.name} page {index}]\n{text}")

    return "\n\n".join(pages)


def _read_file(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".csv":
        rows = []
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
        return "\n".join(rows)

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        return json.dumps(data, ensure_ascii=False, indent=2)

    if suffix == ".pdf":
        return _read_pdf(path)

    return ""


def _expand_query(query: str) -> str:
    q = query.lower()

    aliases = []
    if "lab 1" in q or "lab1" in q:
        aliases.extend(["thực hành 1", "thuc hanh 1", "bài 1", "bai 1", "thuc hanh"])
    if "lab 2" in q or "lab2" in q:
        aliases.extend(["thực hành 2", "thuc hanh 2", "bài 2", "bai 2", "thuc hanh"])
    if "lab 3" in q or "lab3" in q:
        aliases.extend(["thực hành 3", "thuc hanh 3", "bài 3", "bai 3"])

    return query + " " + " ".join(aliases)


def _requested_lab_number(query: str):
    q = query.lower()

    patterns = [
        r"lab\s*(\d+)",
        r"bài\s*(\d+)",
        r"bai\s*(\d+)",
        r"thực\s*hành\s*(\d+)",
        r"thuc\s*hanh\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            return match.group(1)

    return None


def _terms(query: str):
    expanded = _expand_query(query)
    return [
        term
        for term in re.findall(r"[\wÀ-ỹ]+", expanded.lower())
        if len(term) >= 2
    ]


def _score(text: str, query: str) -> int:
    text_lower = text.lower()
    return sum(text_lower.count(term) for term in _terms(query))


def _chunks(content: str):
    raw_chunks = re.split(r"\n\s*\n|(?<=\.)\s+", content)
    return [chunk.strip() for chunk in raw_chunks if chunk.strip()]


def search_data(query: str) -> str:
    if not DATA_DIR.exists():
        return "DATA_FOLDER_NOT_FOUND: create a data/ folder and put files there."

    matches = []
    lab_number = _requested_lab_number(query)

    for path in sorted(DATA_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        content = _read_file(path)
        if not content.strip():
            continue

        filename_lower = path.name.lower()
        file_score = _score(path.name, query)

        # If the user asks lab 1/2/3, strongly prefer files whose names contain that number.
        if lab_number and lab_number in filename_lower:
            file_score += 100

        chunks = _chunks(content)

        for index, chunk in enumerate(chunks):
            score = _score(chunk, query) + file_score * 3

            # Return early parts of the matched lab file even when wording differs.
            if lab_number and lab_number in filename_lower and index < 10:
                score += 50

            if score > 0:
                matches.append((score, path.name, chunk[:MAX_SNIPPET_CHARS]))

    if not matches:
        available_files = ", ".join(
            path.name for path in sorted(DATA_DIR.glob("*")) if path.is_file()
        )
        return (
            f"NO_RELEVANT_DATA_FOUND for query: {query}\n"
            f"Available files: {available_files}"
        )

    matches.sort(reverse=True, key=lambda item: item[0])
    top = matches[:MAX_SNIPPETS]

    return "\n\n".join(
        f"[{filename}]\n{snippet}"
        for _, filename, snippet in top
    )