#!/usr/bin/env python3
"""Pure helpers for one deterministic standing-task evidence hunk."""

from __future__ import annotations

import difflib
import re


EVIDENCE_HEADING = "### Vault Publication Evidence"
SECTION_BOUNDARY = re.compile(r"^ {0,3}#{1,3}(?:[ \t]+|$)")
FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class EvidenceHunkError(ValueError):
    """Represent Markdown that cannot receive an isolated evidence block."""


def markdown_evidence_boundary(lines: list[str]) -> int:
    """Find the canonical section boundary while ignoring fenced content."""
    headings: list[int] = []
    fence_character: str | None = None
    fence_length = 0
    fence_start_index: int | None = None
    for index, line in enumerate(lines):
        text = line.rstrip("\r\n")
        if fence_character is not None:
            if re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
                text,
            ):
                fence_character = None
                fence_length = 0
                fence_start_index = None
            continue
        opening = FENCE_START.match(text)
        if opening:
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
            fence_start_index = index
            continue
        if text == EVIDENCE_HEADING:
            headings.append(index)
    if fence_character is not None:
        location = (
            "in evidence section"
            if headings and fence_start_index is not None and fence_start_index > headings[0]
            else "before evidence heading"
        )
        raise EvidenceHunkError(f"unterminated fenced block {location}")
    if len(headings) != 1:
        raise EvidenceHunkError("canonical evidence heading is missing or duplicated")

    fence_character = None
    fence_length = 0
    for index in range(headings[0] + 1, len(lines)):
        text = lines[index].rstrip("\r\n")
        if fence_character is not None:
            if re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
                text,
            ):
                fence_character = None
                fence_length = 0
            continue
        opening = FENCE_START.match(text)
        if opening:
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
            continue
        if SECTION_BOUNDARY.match(text):
            return index
    if fence_character is not None:
        raise EvidenceHunkError("unterminated fenced block in evidence section")
    return len(lines)


def insert_evidence_block(existing: bytes, block: bytes, marker: bytes) -> bytes:
    """Insert exactly one reviewed block into one UTF-8 standing-task variant."""
    if marker in existing:
        raise EvidenceHunkError("run evidence marker already exists")
    try:
        text = existing.decode("utf-8")
        block.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceHunkError("evidence input is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    boundary_index = markdown_evidence_boundary(lines)
    offset = len("".join(lines[:boundary_index]).encode("utf-8"))
    prefix = existing[:offset]
    suffix = existing[offset:]
    separator = b"" if prefix.endswith(b"\n\n") else b"\n"
    return prefix + separator + block.lstrip(b"\n") + suffix


def canonical_patch(target: str, before: bytes, after: bytes) -> bytes:
    """Return a path-stable UTF-8 unified diff for read-only hunk review."""
    try:
        before_lines = before.decode("utf-8").splitlines(keepends=True)
        after_lines = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise EvidenceHunkError("evidence patch input is not UTF-8") from exc
    patch = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{target}",
        tofile=f"b/{target}",
        lineterm="\n",
    )
    rendered: list[str] = []
    for line in patch:
        if line.startswith(("+", "-", " ")) and not line.endswith("\n"):
            rendered.extend((line, "\n", "\\ No newline at end of file\n"))
        else:
            rendered.append(line)
    return "".join(rendered).encode("utf-8")
