"""Tests for attachment knowledge-base ingestion."""

from __future__ import annotations

from pathlib import Path

import openharness.services.knowledge_base as kb
from openharness.services.knowledge_base import ingest_attachments_to_knowledge_base


def test_ingest_attachments_writes_knowledge_and_index(tmp_path: Path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    source = project_dir / "notes.txt"
    source.write_text("alpha\nbeta\n", encoding="utf-8")

    result = ingest_attachments_to_knowledge_base(project_dir, ["notes.txt"])

    assert result.knowledge_dir.exists()
    assert result.index_path.exists()
    assert len(result.added_files) == 1
    assert result.added_files[0].exists()
    assert "notes.txt" in result.index_path.read_text(encoding="utf-8")
    stored = result.added_files[0].read_text(encoding="utf-8")
    assert "Knowledge: notes.txt" in stored
    assert "alpha" in stored
    assert "Attached File Knowledge" in result.context_markdown


def test_ingest_attachments_skips_missing_or_binary(tmp_path: Path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    binary = project_dir / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02")

    result = ingest_attachments_to_knowledge_base(project_dir, ["missing.md", "blob.bin"])

    assert result.added_files == []
    assert len(result.skipped) == 2
    assert any("missing" in item for item in result.skipped)
    assert any("unsupported or binary" in item for item in result.skipped)
    assert result.context_markdown == ""


def test_ingest_pdf_uses_pdf_extractor(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    pdf = project_dir / "lesson.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        kb,
        "_read_pdf_as_markdown",
        lambda path, ocr_model=None: "## Extracted PDF Content\n\nHola mundo" if path.name == "lesson.pdf" else None,
    )

    result = ingest_attachments_to_knowledge_base(project_dir, ["lesson.pdf"])

    assert len(result.added_files) == 1
    stored = result.added_files[0].read_text(encoding="utf-8")
    assert "Extracted PDF Content" in stored
    assert "Hola mundo" in stored


def test_ingest_image_uses_ollama_ocr(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    image = project_dir / "spanish.png"
    image.write_bytes(b"PNG")

    monkeypatch.setattr(kb, "_ocr_image_bytes_with_ollama", lambda image_bytes, model: "hola clase")

    result = ingest_attachments_to_knowledge_base(project_dir, ["spanish.png"])

    assert len(result.added_files) == 1
    stored = result.added_files[0].read_text(encoding="utf-8")
    assert "OCR Extracted Image Content" in stored
    assert "hola clase" in stored
