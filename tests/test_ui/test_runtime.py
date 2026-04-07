"""Tests for runtime attachment helpers."""

from __future__ import annotations

from pathlib import Path

from openharness.ui.runtime import _extract_attachment_candidates, _merge_attachment_lists


def test_extract_attachment_candidates_from_quoted_paths(tmp_path: Path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    spanish_pdf = project_dir / "Anna's A.pdf"
    spanish_pdf.write_text("hola", encoding="utf-8")

    line = f'请根据这个文件学习西语 "{spanish_pdf}"'
    found = _extract_attachment_candidates(str(project_dir), line)

    assert found == [str(spanish_pdf.resolve())]


def test_merge_attachment_lists_deduplicates():
    merged = _merge_attachment_lists(["a.md", "b.md"], ["b.md", "c.md", ""])
    assert merged == ["a.md", "b.md", "c.md"]
