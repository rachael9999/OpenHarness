"""Attachment ingestion helpers for building a project knowledge base."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from re import sub
from urllib import error as urllib_error
from urllib import request as urllib_request

from openharness.config.paths import get_project_config_dir

_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".csv",
    ".sql",
    ".sh",
    ".log",
}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
_MAX_SOURCE_CHARS = 60_000
_MAX_CONTEXT_CHARS_PER_FILE = 4_000
_MAX_CONTEXT_TOTAL_CHARS = 16_000


@dataclass(frozen=True)
class AttachmentKnowledgeResult:
    """Result of ingesting attached files into the project knowledge base."""

    added_files: list[Path]
    skipped: list[str]
    knowledge_dir: Path
    index_path: Path
    context_markdown: str


def ingest_attachments_to_knowledge_base(
    cwd: str | Path,
    attachment_paths: list[str],
    *,
    ollama_model: str | None = None,
) -> AttachmentKnowledgeResult:
    """Convert attached files to markdown, persist them, and build prompt context."""
    knowledge_dir = get_project_config_dir(cwd) / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    index_path = knowledge_dir / "INDEX.md"

    added_files: list[Path] = []
    skipped: list[str] = []
    context_sections: list[str] = []
    context_size = 0

    chosen_model = (ollama_model or os.environ.get("OPENHARNESS_OLLAMA_MODEL", "")).strip() or None
    ocr_model = os.environ.get("OPENHARNESS_OLLAMA_OCR_MODEL", "glm-ocr:q8_0").strip() or "glm-ocr:q8_0"

    for raw_path in attachment_paths:
        source_path = _resolve_path(cwd, raw_path)
        if not source_path.exists():
            skipped.append(f"missing: {raw_path}")
            continue
        if source_path.is_dir():
            skipped.append(f"is directory: {raw_path}")
            continue

        markdown = _read_as_markdown(source_path, ocr_model=ocr_model)
        if markdown is None:
            skipped.append(f"unsupported or binary: {raw_path}")
            continue

        if chosen_model:
            rewritten = _rewrite_markdown_with_ollama(chosen_model, source_path, markdown)
            if rewritten:
                markdown = rewritten

        target = _write_knowledge_file(knowledge_dir, source_path, markdown)
        _append_index_entry(index_path, source_path, target)
        added_files.append(target)

        excerpt = markdown[:_MAX_CONTEXT_CHARS_PER_FILE].strip()
        if excerpt and context_size < _MAX_CONTEXT_TOTAL_CHARS:
            remaining = _MAX_CONTEXT_TOTAL_CHARS - context_size
            excerpt = excerpt[:remaining]
            context_sections.extend(
                [
                    f"## {source_path.name}",
                    "```md",
                    excerpt,
                    "```",
                    "",
                ]
            )
            context_size += len(excerpt)

    context_markdown = ""
    if context_sections:
        context_markdown = "\n".join(
            [
                "# Attached File Knowledge",
                "Use the extracted notes from attached files as authoritative context when relevant.",
                "",
                *context_sections,
            ]
        ).strip()

    return AttachmentKnowledgeResult(
        added_files=added_files,
        skipped=skipped,
        knowledge_dir=knowledge_dir,
        index_path=index_path,
        context_markdown=context_markdown,
    )


def _resolve_path(cwd: str | Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = Path(cwd).resolve() / path
    return path.resolve()


def _read_as_markdown(path: Path, *, ocr_model: str | None = None) -> str | None:
    ext = path.suffix.lower()
    if ext in _PDF_EXTENSIONS:
        return _read_pdf_as_markdown(path, ocr_model=ocr_model)
    if ext in _IMAGE_EXTENSIONS:
        return _read_image_as_markdown(path, ocr_model=ocr_model)
    if ext and ext not in _TEXT_EXTENSIONS:
        return None

    raw = path.read_bytes()
    if b"\x00" in raw:
        return None

    text = raw.decode("utf-8", errors="replace")
    if len(text) > _MAX_SOURCE_CHARS:
        text = text[:_MAX_SOURCE_CHARS] + "\n...[truncated]..."

    if ext in _MARKDOWN_EXTENSIONS:
        return text.strip()

    language = _language_for_extension(ext)
    if language:
        return f"```{language}\n{text.strip()}\n```"
    return text.strip()


def _read_pdf_as_markdown(path: Path, *, ocr_model: str | None = None) -> str | None:
    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(str(path))
    except Exception:
        return None

    chunks: list[str] = []
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            chunks.append(text)

    if not chunks:
        ocr_chunks = _extract_pdf_text_with_ollama_ocr(reader, ocr_model=ocr_model)
        if not ocr_chunks:
            return None
        chunks = ocr_chunks

    merged = "\n\n".join(chunks)
    if len(merged) > _MAX_SOURCE_CHARS:
        merged = merged[:_MAX_SOURCE_CHARS] + "\n...[truncated]..."
    return "\n".join(
        [
            "## Extracted PDF Content",
            "",
            merged,
        ]
    ).strip()


def _read_image_as_markdown(path: Path, *, ocr_model: str | None = None) -> str | None:
    if not ocr_model:
        return None
    image_bytes = path.read_bytes()
    text = _ocr_image_bytes_with_ollama(image_bytes, ocr_model)
    if not text:
        return None
    return "\n".join(
        [
            "## OCR Extracted Image Content",
            "",
            text,
        ]
    ).strip()


def _extract_pdf_text_with_ollama_ocr(reader, *, ocr_model: str | None) -> list[str]:
    if not ocr_model:
        return []
    chunks: list[str] = []
    for page_index, page in enumerate(getattr(reader, "pages", []), start=1):
        images = getattr(page, "images", None)
        if not images:
            continue
        page_texts: list[str] = []
        for image in images[:2]:
            data = getattr(image, "data", None)
            if not data:
                continue
            text = _ocr_image_bytes_with_ollama(data, ocr_model)
            if text:
                page_texts.append(text)
        if page_texts:
            chunks.append(f"### Page {page_index}\n\n" + "\n\n".join(page_texts))
    return chunks


def _ocr_image_bytes_with_ollama(image_bytes: bytes, model: str) -> str | None:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "Perform OCR and return clean markdown text. Preserve original language and structure.",
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
    except (urllib_error.URLError, TimeoutError, OSError):
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    message = data.get("message", {}) if isinstance(data, dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    text = str(content).strip()
    return text or None


def _language_for_extension(ext: str) -> str | None:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".csv": "csv",
        ".sql": "sql",
        ".sh": "bash",
    }
    return mapping.get(ext)


def _rewrite_markdown_with_ollama(model: str, source_path: Path, markdown: str) -> str | None:
    prompt = (
        "Convert this source content into concise, accurate markdown for a coding knowledge base. "
        "Preserve facts and code snippets, add short headings, do not invent details.\n\n"
        f"Source: {source_path.name}\n\n"
        f"{markdown}\n"
    )
    try:
        proc = subprocess.run(
            ["ollama", "run", model, prompt],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or "").strip()
    return output or None


def _write_knowledge_file(knowledge_dir: Path, source_path: Path, markdown: str) -> Path:
    stem = sub(r"[^a-zA-Z0-9]+", "_", source_path.stem.lower()).strip("_") or "attachment"
    digest = sha1(str(source_path).encode("utf-8")).hexdigest()[:8]
    filename = f"{stem}_{digest}.md"
    target = knowledge_dir / filename
    timestamp = datetime.now(timezone.utc).isoformat()
    target.write_text(
        "\n".join(
            [
                f"# Knowledge: {source_path.name}",
                "",
                f"- Source: {source_path}",
                f"- Imported: {timestamp}",
                "",
                markdown.strip(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return target


def _append_index_entry(index_path: Path, source_path: Path, target_path: Path) -> None:
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
    else:
        content = "# Knowledge Base Index\n\n"
    entry = f"- [{source_path.name}]({target_path.name})"
    if entry not in content:
        content = content.rstrip() + "\n" + entry + "\n"
        index_path.write_text(content, encoding="utf-8")
