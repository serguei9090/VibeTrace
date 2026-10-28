"""Heading-aware document chunker for Markdown, plain text, and PDF files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HeadingChunk:
    """A single coherent chunk of text with structural heading context."""

    title: str
    heading_path: str
    start_line: int
    end_line: int
    content: str


def chunk_markdown(content: str, max_chars: int = 1500) -> list[HeadingChunk]:
    """Chunk Markdown by headings (#, ##, ###, etc.) preserving hierarchy and lines."""
    lines = content.splitlines()
    if not lines:
        return []

    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
    sections: list[tuple[int, int, str, list[str]]] = []  # (start_line, level, title, lines)
    current_level = 0
    current_title = "Introduction"
    current_start = 1
    current_lines: list[str] = []
    stack: list[tuple[int, str]] = []  # [(level, title)]
    in_code_block = False

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block

        match = heading_re.match(stripped) if not in_code_block else None
        if match:
            # Save previous section if it has content
            if current_lines:
                sections.append((current_start, current_level, current_title, current_lines))
                current_lines = []

            level = len(match.group(1))
            title = match.group(2).strip()

            # Maintain hierarchical heading stack
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))

            current_level = level
            current_title = " > ".join(t for _, t in stack)
            current_start = idx
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_start, current_level, current_title, current_lines))

    chunks: list[HeadingChunk] = []
    for s_start, _, h_path, s_lines in sections:
        sec_text = "\n".join(s_lines).strip()
        if not sec_text:
            continue

        s_end = s_start + len(s_lines) - 1
        # If section is small enough, keep as single chunk
        if len(sec_text) <= max_chars:
            chunks.append(
                HeadingChunk(
                    title=h_path.split(" > ")[-1],
                    heading_path=h_path,
                    start_line=s_start,
                    end_line=s_end,
                    content=sec_text,
                )
            )
        else:
            # Sub-split long section by paragraphs or lines
            sub_chunks = _split_long_section(s_lines, s_start, h_path, max_chars)
            chunks.extend(sub_chunks)

    return chunks


def _split_long_section(
    lines: list[str],
    base_start_line: int,
    heading_path: str,
    max_chars: int,
) -> list[HeadingChunk]:
    """Sub-split a long markdown section into bounded chunks with slight overlap."""
    chunks: list[HeadingChunk] = []
    buf: list[str] = []
    chunk_start = base_start_line
    cur_len = 0

    for idx, line in enumerate(lines):
        line_len = len(line) + 1
        if cur_len + line_len > max_chars and buf:
            chunk_end = base_start_line + idx - 1
            chunks.append(
                HeadingChunk(
                    title=heading_path.split(" > ")[-1],
                    heading_path=heading_path,
                    start_line=chunk_start,
                    end_line=chunk_end,
                    content="\n".join(buf).strip(),
                )
            )
            # Overlap: keep last 3 lines if available
            overlap_lines = buf[-3:] if len(buf) > 3 else []
            buf = list(overlap_lines)
            cur_len = sum(len(line_item) + 1 for line_item in buf)
            chunk_start = base_start_line + idx - len(overlap_lines)

        buf.append(line)
        cur_len += line_len

    if buf:
        chunk_end = base_start_line + len(lines) - 1
        chunks.append(
            HeadingChunk(
                title=heading_path.split(" > ")[-1],
                heading_path=heading_path,
                start_line=chunk_start,
                end_line=chunk_end,
                content="\n".join(buf).strip(),
            )
        )
    return chunks


def chunk_text(content: str, max_chars: int = 1200, overlap_lines: int = 4) -> list[HeadingChunk]:
    """Chunk plain text files using sliding window with line preservation."""
    lines = content.splitlines()
    if not lines:
        return []

    # Flatten oversized individual lines into bounded slices
    norm_lines: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        if len(line) <= max_chars:
            norm_lines.append((idx, line))
        else:
            for part in [line[i : i + max_chars] for i in range(0, len(line), max_chars)]:
                norm_lines.append((idx, part))

    chunks: list[HeadingChunk] = []
    buf: list[str] = []
    chunk_start = norm_lines[0][0]
    cur_len = 0

    for l_idx, (orig_line, line_text) in enumerate(norm_lines):
        line_len = len(line_text) + 1
        if cur_len + line_len > max_chars and buf:
            prev_orig_line = norm_lines[l_idx - 1][0]
            chunks.append(
                HeadingChunk(
                    title=f"Lines {chunk_start}-{prev_orig_line}",
                    heading_path=f"Text Section {chunk_start}-{prev_orig_line}",
                    start_line=chunk_start,
                    end_line=prev_orig_line,
                    content="\n".join(buf).strip(),
                )
            )
            overlap = buf[-overlap_lines:] if len(buf) > overlap_lines else []
            buf = list(overlap)
            cur_len = sum(len(line_item) + 1 for line_item in buf)
            chunk_start = norm_lines[max(0, l_idx - len(overlap))][0] if overlap else orig_line

        buf.append(line_text)
        cur_len += line_len

    if buf:
        last_orig_line = norm_lines[-1][0]
        chunks.append(
            HeadingChunk(
                title=f"Lines {chunk_start}-{last_orig_line}",
                heading_path=f"Text Section {chunk_start}-{last_orig_line}",
                start_line=chunk_start,
                end_line=last_orig_line,
                content="\n".join(buf).strip(),
            )
        )
    return chunks


def chunk_pdf(pdf_path: Path, max_chars: int = 1500) -> list[HeadingChunk]:
    """Extract and chunk text from a PDF file by pages."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    try:
        reader = PdfReader(str(pdf_path))
        if getattr(reader, "is_encrypted", False):
            try:
                dec_res = reader.decrypt("")
                # If decrypt returns 0, password was required and empty pass failed
                if dec_res == 0:
                    return []
            except Exception:
                return []
        pages = list(reader.pages)
    except Exception:
        return []

    chunks: list[HeadingChunk] = []
    for page_num, page in enumerate(pages, start=1):
        try:
            text = (page.extract_text() or "").replace("\x00", "").strip()
        except Exception:
            text = ""
        if not text:
            continue

        page_title = f"Page {page_num}"
        if len(text) <= max_chars:
            chunks.append(
                HeadingChunk(
                    title=page_title,
                    heading_path=page_title,
                    start_line=page_num,
                    end_line=page_num,
                    content=text,
                )
            )
        else:
            # Split page text into smaller paragraph chunks
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            buf: list[str] = []
            cur_len = 0
            sub_idx = 1
            for p in paragraphs:
                if cur_len + len(p) > max_chars and buf:
                    chunks.append(
                        HeadingChunk(
                            title=f"Page {page_num} Part {sub_idx}",
                            heading_path=f"Page {page_num} > Section {sub_idx}",
                            start_line=page_num,
                            end_line=page_num,
                            content="\n\n".join(buf),
                        )
                    )
                    buf = []
                    cur_len = 0
                    sub_idx += 1
                buf.append(p)
                cur_len += len(p)
            if buf:
                chunks.append(
                    HeadingChunk(
                        title=f"Page {page_num} Part {sub_idx}",
                        heading_path=f"Page {page_num} > Section {sub_idx}",
                        start_line=page_num,
                        end_line=page_num,
                        content="\n\n".join(buf),
                    )
                )

    return chunks
