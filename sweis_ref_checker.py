#!/usr/bin/env python3
"""
SWEIS Internal Reference Checker
=================================
Extracts text from all three SWEIS PDF volumes, identifies internal references
and their targets (section headings, table/figure labels, appendix headers),
cross-references them, and produces a report of potentially orphaned references.

Usage:
    python sweis_ref_checker.py
"""

import re
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF is required. Install with: pip install PyMuPDF")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Reference:
    """An internal reference found in the document text."""
    ref_type: str        # "section", "chapter", "table", "figure", "appendix"
    ref_id: str          # normalized identifier, e.g. "S.1.3", "5.2", "S.2-1"
    raw_text: str        # the matched text as it appeared
    volume: str          # source PDF filename
    page: int            # 1-based page number
    context: str = ""    # surrounding text snippet


@dataclass
class Target:
    """A reference target (heading, label, caption) found in the document."""
    target_type: str     # "section", "chapter", "table", "figure", "appendix"
    target_id: str       # normalized identifier
    raw_text: str        # the matched text as it appeared
    volume: str
    page: int


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: str) -> list[tuple[int, str]]:
    """Extract text from each page of a PDF. Returns list of (page_num, text)."""
    pages = []
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        text = page.get_text("text")
        pages.append((i + 1, text))
    doc.close()
    return pages


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_id(raw: str) -> str:
    """Normalize an identifier for matching: strip whitespace, unify dashes."""
    s = raw.strip()
    s = s.replace("\u2013", "-").replace("\u2014", "-")  # en/em dash -> hyphen
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_section_id(raw: str) -> str:
    """Normalize a section/chapter number: strip trailing dots."""
    s = normalize_id(raw)
    s = s.rstrip(".")
    return s


# ---------------------------------------------------------------------------
# Reference extraction  (what the text *points to*)
# ---------------------------------------------------------------------------

# Patterns for inline references
# Each tuple: (ref_type, compiled regex, group index for the id)

REF_PATTERNS = [
    # "Section S.1.3", "Section 5.2", "Section 3.2.4", "Sections S.2.3 and S.2.4"
    ("section", re.compile(
        r'Sections?\s+(S\.[\d.]+|[\d]+\.[\d.]+)', re.IGNORECASE
    ), 1),

    # "Chapter 2", "Chapters 3 and 4"
    ("chapter", re.compile(
        r'Chapters?\s+(\d+)', re.IGNORECASE
    ), 1),

    # "Table S.2-1", "Table A.3.5-1", "Table 4.3-1", "Tables A.3.5-1 and A.3.5-2"
    ("table", re.compile(
        r'Tables?\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)', re.IGNORECASE
    ), 1),

    # "Figure S.1-1", "Figure 1.3-1"
    ("figure", re.compile(
        r'Figures?\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)', re.IGNORECASE
    ), 1),

    # "Appendix A", "Appendix H", "Appendices A and B"
    ("appendix", re.compile(
        r'Appendi(?:x|ces)\s+([A-Z])\b', re.IGNORECASE
    ), 1),
]

# Additional pattern for compound references like "Tables A.3.5-1 and A.3.5-2"
COMPOUND_TABLE_RE = re.compile(
    r'Tables?\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)\s+and\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)',
    re.IGNORECASE
)
COMPOUND_FIGURE_RE = re.compile(
    r'Figures?\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)\s+and\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)',
    re.IGNORECASE
)
COMPOUND_SECTION_RE = re.compile(
    r'Sections?\s+(S\.[\d.]+|[\d]+\.[\d.]+)\s+and\s+(S\.[\d.]+|[\d]+\.[\d.]+)',
    re.IGNORECASE
)
COMPOUND_APPENDIX_RE = re.compile(
    r'Appendi(?:x|ces)\s+([A-Z])\s+and\s+([A-Z])\b', re.IGNORECASE
)

# Patterns that indicate an EXTERNAL reference — skip these
EXTERNAL_CONTEXT_RE = re.compile(
    r'(?:'
    r'2008\s+LANL\s+SWEIS'
    r'|Final\s+Site-Wide\s+Environmental\s+Impact\s+Statement\s+for\s+Continued'
    r'|CT\s+EIS'
    r'|Conveyance\s+and\s+Transfer'
    r'|DOE/EIS-0380'
    r'|DOE/EIS-0293'
    r'|DOE/EA-\d+'
    r'|Chromium\s+(?:Interim|Final)\s+Remedy\s+(?:EA|Environmental)'
    r'|previous\s+SWEIS'
    r'|prior\s+SWEIS'
    r')',
    re.IGNORECASE
)

# Regulatory / legal citations to skip entirely
REGULATORY_RE = re.compile(
    r'(?:'
    r'\d+\s+CFR'
    r'|\d+\s+U\.S\.C\.'
    r'|\d+\s+FR\s+\d+'
    r'|Executive\s+Order'
    r'|DOE\s+Order'
    r')',
    re.IGNORECASE
)


def get_context(text: str, match_start: int, match_end: int, window: int = 150) -> str:
    """Return a snippet of surrounding text for context."""
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def is_external_reference(text: str, match_start: int, match_end: int) -> bool:
    """Check if the surrounding context indicates an external document reference."""
    window = 200
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    context = text[start:end]

    if EXTERNAL_CONTEXT_RE.search(context):
        return True

    # Check for "of the Final SWEIS" or "of the 2008 SWEIS" nearby after the match
    after = text[match_end:min(len(text), match_end + 80)]
    if re.search(r'of\s+the\s+(?:Final|2008|previous)', after, re.IGNORECASE):
        return True

    # "Source: Author (Year), Table X-Y" — citation to a table/figure in another doc
    before = text[max(0, match_start - 80):match_start]
    if re.search(r'Source:\s*\w+\s*\(\d{4}', before, re.IGNORECASE):
        return True
    # Also handle "Source: DOE (2008b), Table 8-14" where source is further back
    before_wide = text[max(0, match_start - 150):match_start]
    if re.search(r'Source:\s*\w+\s*\(\d{4}\w?\)', before_wide, re.IGNORECASE):
        return True

    # Legal codes: "Code of Ordinance, Chapter 18" or "Title 18 USC, Chapter 40"
    if re.search(r'(?:Code\s+of\s+Ordinance|U\.?S\.?C\.?|United\s+States\s+Code)', before_wide, re.IGNORECASE):
        return True

    return False


def is_regulatory_context(text: str, match_start: int, match_end: int) -> bool:
    """Check if this is part of a regulatory citation."""
    window = 100
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    context = text[start:end]
    return bool(REGULATORY_RE.search(context))


def extract_references(pages: list[tuple[int, str]], volume: str) -> list[Reference]:
    """Extract all internal references from the document pages."""
    refs = []

    for page_num, text in pages:
        # Skip header/footer lines, TOC pages with dotted leaders
        # (we still scan them for references though)

        for ref_type, pattern, group_idx in REF_PATTERNS:
            for m in pattern.finditer(text):
                raw = m.group(0)
                ref_id_raw = m.group(group_idx)

                # Skip if this is part of a regulatory citation
                if is_regulatory_context(text, m.start(), m.end()):
                    continue

                # Skip if context suggests external document
                if is_external_reference(text, m.start(), m.end()):
                    continue

                # Normalize
                if ref_type in ("section", "chapter"):
                    norm_id = normalize_section_id(ref_id_raw)
                else:
                    norm_id = normalize_id(ref_id_raw)

                context = get_context(text, m.start(), m.end())

                refs.append(Reference(
                    ref_type=ref_type,
                    ref_id=norm_id,
                    raw_text=raw,
                    volume=volume,
                    page=page_num,
                    context=context,
                ))

        # Handle compound references (e.g., "Tables A.3.5-1 and A.3.5-2")
        for m in COMPOUND_TABLE_RE.finditer(text):
            if not is_external_reference(text, m.start(), m.end()):
                # Second ID (first is already captured by the main pattern)
                norm_id = normalize_id(m.group(2))
                refs.append(Reference(
                    ref_type="table",
                    ref_id=norm_id,
                    raw_text=m.group(0),
                    volume=volume,
                    page=page_num,
                    context=get_context(text, m.start(), m.end()),
                ))

        for m in COMPOUND_FIGURE_RE.finditer(text):
            if not is_external_reference(text, m.start(), m.end()):
                norm_id = normalize_id(m.group(2))
                refs.append(Reference(
                    ref_type="figure",
                    ref_id=norm_id,
                    raw_text=m.group(0),
                    volume=volume,
                    page=page_num,
                    context=get_context(text, m.start(), m.end()),
                ))

        for m in COMPOUND_SECTION_RE.finditer(text):
            if not is_external_reference(text, m.start(), m.end()):
                norm_id = normalize_section_id(m.group(2))
                refs.append(Reference(
                    ref_type="section",
                    ref_id=norm_id,
                    raw_text=m.group(0),
                    volume=volume,
                    page=page_num,
                    context=get_context(text, m.start(), m.end()),
                ))

        for m in COMPOUND_APPENDIX_RE.finditer(text):
            if not is_external_reference(text, m.start(), m.end()):
                norm_id = normalize_id(m.group(2)).upper()
                refs.append(Reference(
                    ref_type="appendix",
                    ref_id=norm_id,
                    raw_text=m.group(0),
                    volume=volume,
                    page=page_num,
                    context=get_context(text, m.start(), m.end()),
                ))

    return refs


# ---------------------------------------------------------------------------
# Target extraction  (what the document *defines*)
# ---------------------------------------------------------------------------

# Section heading: line starts with a section number like "S.1.1" or "5.2.3"
# Followed by at least one space and then a title (uppercase or mixed case word)
SECTION_HEADING_RE = re.compile(
    r'^[ \t]*(S\.[\d.]+|[\d]+\.[\d.]+)\s+[A-Z]',
    re.MULTILINE
)

# Chapter heading: "CHAPTER 1" or "Chapter 1" at start of line
# or "1 INTRODUCTION" style (single number at start with all-caps title)
CHAPTER_HEADING_RE = re.compile(
    r'^[ \t]*(?:CHAPTER|Chapter)\s+(\d+)',
    re.MULTILINE
)
# Alternative: just a number followed by all-caps title
CHAPTER_NUM_HEADING_RE = re.compile(
    r'^[ \t]*(\d+)\s+[A-Z][A-Z]+',
    re.MULTILINE
)

# Table label at start of a line
TABLE_LABEL_RE = re.compile(
    r'^[ \t]*Table\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)',
    re.MULTILINE | re.IGNORECASE
)

# Figure label at start of a line
FIGURE_LABEL_RE = re.compile(
    r'^[ \t]*Figure\s+([A-Z]?\.?[\d]+[\d.]*[-–]\d+)',
    re.MULTILINE | re.IGNORECASE
)

# Appendix header: "APPENDIX A" or "Appendix A" as a standalone heading
APPENDIX_HEADER_RE = re.compile(
    r'^[ \t]*(?:APPENDIX|Appendix)\s+([A-Z])\b',
    re.MULTILINE
)


def extract_targets(pages: list[tuple[int, str]], volume: str) -> list[Target]:
    """Extract all reference targets (headings, labels) from document pages."""
    targets = []

    for page_num, text in pages:
        # Section headings
        for m in SECTION_HEADING_RE.finditer(text):
            raw_id = m.group(1)
            norm_id = normalize_section_id(raw_id)
            targets.append(Target(
                target_type="section",
                target_id=norm_id,
                raw_text=text[m.start():min(m.end() + 80, len(text))].split("\n")[0].strip(),
                volume=volume,
                page=page_num,
            ))

        # Chapter headings
        for m in CHAPTER_HEADING_RE.finditer(text):
            norm_id = m.group(1).strip()
            targets.append(Target(
                target_type="chapter",
                target_id=norm_id,
                raw_text=text[m.start():min(m.end() + 60, len(text))].split("\n")[0].strip(),
                volume=volume,
                page=page_num,
            ))

        # Table labels
        for m in TABLE_LABEL_RE.finditer(text):
            raw_id = m.group(1)
            norm_id = normalize_id(raw_id)
            targets.append(Target(
                target_type="table",
                target_id=norm_id,
                raw_text=text[m.start():min(m.end() + 80, len(text))].split("\n")[0].strip(),
                volume=volume,
                page=page_num,
            ))

        # Figure labels
        for m in FIGURE_LABEL_RE.finditer(text):
            raw_id = m.group(1)
            norm_id = normalize_id(raw_id)
            targets.append(Target(
                target_type="figure",
                target_id=norm_id,
                raw_text=text[m.start():min(m.end() + 80, len(text))].split("\n")[0].strip(),
                volume=volume,
                page=page_num,
            ))

        # Appendix headers
        for m in APPENDIX_HEADER_RE.finditer(text):
            norm_id = m.group(1).upper()
            targets.append(Target(
                target_type="appendix",
                target_id=norm_id,
                raw_text=text[m.start():min(m.end() + 60, len(text))].split("\n")[0].strip(),
                volume=volume,
                page=page_num,
            ))

    return targets


# ---------------------------------------------------------------------------
# Cross-reference matching
# ---------------------------------------------------------------------------

def build_target_index(all_targets: list[Target]) -> dict[str, set[str]]:
    """
    Build a lookup: (type, normalized_id) -> set of target_ids found.
    Also indexes parent sections for prefix matching.
    """
    index: dict[str, set[str]] = defaultdict(set)

    for t in all_targets:
        key = f"{t.target_type}:{t.target_id}"
        index[key].add(t.target_id)

    return index


def find_orphans(
    refs: list[Reference],
    targets: list[Target],
) -> tuple[list[Reference], list[Reference]]:
    """
    Match references against targets. Returns (matched, orphaned).
    Uses exact and prefix matching for sections.
    """
    # Build sets of known target IDs by type
    target_sets: dict[str, set[str]] = defaultdict(set)
    for t in targets:
        target_sets[t.target_type].add(t.target_id)
        # For sections, also add without trailing sub-numbers for prefix matching
        # e.g., if we have "3.2.4", also register "3.2" and "3"
        if t.target_type == "section":
            parts = t.target_id.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                target_sets["section"].add(parent)

    matched = []
    orphaned = []

    # Deduplicate references for reporting (same type+id may appear many times)
    seen_refs: dict[str, list[Reference]] = defaultdict(list)
    for r in refs:
        key = f"{r.ref_type}:{r.ref_id}"
        seen_refs[key].append(r)

    for key, ref_list in seen_refs.items():
        ref_type, ref_id = key.split(":", 1)
        known = target_sets.get(ref_type, set())

        is_matched = ref_id in known

        # For sections, also try prefix match: "Section 5" matches if "5.1" exists
        if not is_matched and ref_type == "section":
            for tid in known:
                if tid.startswith(ref_id + ".") or tid == ref_id:
                    is_matched = True
                    break

        if is_matched:
            matched.extend(ref_list)
        else:
            orphaned.extend(ref_list)

    return matched, orphaned


# ---------------------------------------------------------------------------
# Deduplication for reporting
# ---------------------------------------------------------------------------

def deduplicate_for_report(refs: list[Reference]) -> list[dict]:
    """Group references by type+id and return summary entries."""
    groups: dict[str, dict] = {}
    for r in refs:
        key = f"{r.ref_type}:{r.ref_id}"
        if key not in groups:
            groups[key] = {
                "ref_type": r.ref_type,
                "ref_id": r.ref_id,
                "raw_text": r.raw_text,
                "occurrences": [],
            }
        groups[key]["occurrences"].append({
            "volume": r.volume,
            "page": r.page,
            "context": r.context,
        })

    return sorted(groups.values(), key=lambda x: (x["ref_type"], x["ref_id"]))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    matched: list[Reference],
    orphaned: list[Reference],
    all_refs: list[Reference],
    all_targets: list[Target],
    output_path: str,
):
    """Write a plain-text report."""
    lines = []
    w = lines.append

    w("=" * 80)
    w("SWEIS INTERNAL REFERENCE CHECK REPORT")
    w("=" * 80)
    w("")

    # --- Summary statistics ---
    ref_by_type: dict[str, set[str]] = defaultdict(set)
    for r in all_refs:
        ref_by_type[r.ref_type].add(r.ref_id)
    target_by_type: dict[str, set[str]] = defaultdict(set)
    for t in all_targets:
        target_by_type[t.target_type].add(t.target_id)

    orphan_by_type: dict[str, set[str]] = defaultdict(set)
    for r in orphaned:
        orphan_by_type[r.ref_type].add(r.ref_id)
    matched_by_type: dict[str, set[str]] = defaultdict(set)
    for r in matched:
        matched_by_type[r.ref_type].add(r.ref_id)

    total_unique_refs = sum(len(v) for v in ref_by_type.values())
    total_unique_targets = sum(len(v) for v in target_by_type.values())
    total_unique_orphans = sum(len(v) for v in orphan_by_type.values())
    total_unique_matched = sum(len(v) for v in matched_by_type.values())

    w("SUMMARY STATISTICS")
    w("-" * 40)
    w(f"Total unique references found:  {total_unique_refs}")
    w(f"Total unique targets found:     {total_unique_targets}")
    w(f"References matched to targets:  {total_unique_matched}")
    w(f"POTENTIALLY ORPHANED:           {total_unique_orphans}")
    w("")
    w(f"  {'Type':<12} {'Refs':>6} {'Targets':>8} {'Matched':>8} {'Orphaned':>8}")
    w(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")
    for t in ["section", "chapter", "table", "figure", "appendix"]:
        w(f"  {t:<12} {len(ref_by_type.get(t, set())):>6} "
          f"{len(target_by_type.get(t, set())):>8} "
          f"{len(matched_by_type.get(t, set())):>8} "
          f"{len(orphan_by_type.get(t, set())):>8}")
    w("")

    # --- Orphaned references (the main output) ---
    w("=" * 80)
    w("POTENTIALLY ORPHANED REFERENCES")
    w("(References that could not be matched to any target in the three volumes)")
    w("=" * 80)
    w("")

    if not orphaned:
        w("  No orphaned references found!")
        w("")
    else:
        deduped = deduplicate_for_report(orphaned)
        for i, entry in enumerate(deduped, 1):
            w(f"  [{i}] {entry['ref_type'].upper()}: {entry['ref_id']}")
            w(f"      Raw text: \"{entry['raw_text']}\"")
            w(f"      Occurrences ({len(entry['occurrences'])}):")
            for occ in entry["occurrences"][:5]:  # limit to 5 per entry
                short_vol = os.path.basename(occ["volume"])
                w(f"        - {short_vol}, page {occ['page']}")
                w(f"          Context: {occ['context'][:200]}")
            if len(entry["occurrences"]) > 5:
                w(f"        ... and {len(entry['occurrences']) - 5} more occurrences")
            w("")

    # --- All targets found (inventory) ---
    w("=" * 80)
    w("ALL TARGETS FOUND (Sections, Tables, Figures, Appendices, Chapters)")
    w("=" * 80)
    w("")

    for target_type in ["chapter", "section", "table", "figure", "appendix"]:
        type_targets = sorted(
            set((t.target_id, t.volume, t.page) for t in all_targets if t.target_type == target_type),
            key=lambda x: x[0]
        )
        # Deduplicate by id
        seen_ids = {}
        for tid, vol, pg in type_targets:
            if tid not in seen_ids:
                seen_ids[tid] = (vol, pg)

        w(f"  {target_type.upper()} targets ({len(seen_ids)} unique):")
        w(f"  {'-' * 60}")
        for tid in sorted(seen_ids.keys()):
            vol, pg = seen_ids[tid]
            short_vol = os.path.basename(vol)
            w(f"    {tid:<30} ({short_vol}, p.{pg})")
        w("")

    # --- All references found (full inventory) ---
    w("=" * 80)
    w("ALL REFERENCES FOUND (grouped by type)")
    w("=" * 80)
    w("")

    for ref_type in ["section", "chapter", "table", "figure", "appendix"]:
        type_refs = ref_by_type.get(ref_type, set())
        w(f"  {ref_type.upper()} references ({len(type_refs)} unique):")
        w(f"  {'-' * 60}")
        for rid in sorted(type_refs):
            status = "ORPHANED" if rid in orphan_by_type.get(ref_type, set()) else "OK"
            w(f"    {rid:<30} [{status}]")
        w("")

    w("=" * 80)
    w("END OF REPORT")
    w("=" * 80)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Report written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Find PDF files
    pdf_files = sorted([
        os.path.join(script_dir, f)
        for f in os.listdir(script_dir)
        if f.lower().endswith(".pdf")
    ])

    if not pdf_files:
        print("ERROR: No PDF files found in the script directory.")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDF file(s):")
    for f in pdf_files:
        print(f"  - {os.path.basename(f)}")
    print()

    # Extract text from all PDFs
    all_refs: list[Reference] = []
    all_targets: list[Target] = []

    for pdf_path in pdf_files:
        basename = os.path.basename(pdf_path)
        print(f"Processing: {basename} ...")

        pages = extract_pages(pdf_path)
        print(f"  Extracted {len(pages)} pages")

        refs = extract_references(pages, pdf_path)
        print(f"  Found {len(refs)} references")

        targets = extract_targets(pages, pdf_path)
        print(f"  Found {len(targets)} targets")

        all_refs.extend(refs)
        all_targets.extend(targets)

    print()
    print(f"Total references: {len(all_refs)}")
    print(f"Total targets:    {len(all_targets)}")
    print()

    # Cross-reference
    print("Cross-referencing ...")
    matched, orphaned = find_orphans(all_refs, all_targets)

    unique_orphan_ids = set(f"{r.ref_type}:{r.ref_id}" for r in orphaned)
    print(f"  Matched:  {len(set(f'{r.ref_type}:{r.ref_id}' for r in matched))} unique ref IDs")
    print(f"  Orphaned: {len(unique_orphan_ids)} unique ref IDs")
    print()

    # Generate report
    report_path = os.path.join(script_dir, "sweis_ref_report.txt")
    generate_report(matched, orphaned, all_refs, all_targets, report_path)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
