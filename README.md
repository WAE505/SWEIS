# SWEIS Internal Reference Checker

A Python tool that checks the Draft Site-Wide Environmental Impact Statement (SWEIS) for Los Alamos National Laboratory (DOE/EIS-0552) for orphaned internal references -- references to sections, tables, figures, appendices, or chapters that may no longer exist or have been renumbered during document development.

## Problem Statement

Large multi-volume EIS documents undergo extensive revision during development. Internal references (e.g., "see Section 5.2", "Table 3.5-1") may become orphaned when the referenced material is eliminated, moved, or renumbered. The three SWEIS volumes total ~53.5 MB and ~1,223 pages, making manual cross-reference checking impractical.

## Design Approach

The tool uses a two-pass approach:

### Pass 1: Extraction

**References** (what the text *points to*) are extracted using regex patterns:

| Type | Pattern | Examples |
|------|---------|----------|
| Section | `Sections?\s+(S\.[\d.]+\|[\d]+\.[\d.]+)` | Section S.1.3, Section 5.2 |
| Chapter | `Chapters?\s+(\d+)` | Chapter 2, Chapter 5 |
| Table | `Tables?\s+([A-Z]?\.?[\d]+[\d.]*[-\u2013]\d+)` | Table S.2-1, Table A.3.5-1 |
| Figure | `Figures?\s+([A-Z]?\.?[\d]+[\d.]*[-\u2013]\d+)` | Figure S.1-1, Figure 1.3-1 |
| Appendix | `Appendi(?:x\|ces)\s+([A-Z])` | Appendix A, Appendix H |

Compound references (e.g., "Tables A.3.5-1 and A.3.5-2") are also handled with dedicated patterns.

**Targets** (what the document *defines*) are extracted by looking for headings and labels at the start of lines:

- Section headings: `S.1.3.1 Other LANL Program Considerations...` or `5.2 Land and Visual Resources`
- Table labels: `Table S.2-1 Summary of Construction...`
- Figure labels: `Figure S.1-1 Location of the Los Alamos...`
- Appendix headers: `Appendix A` as standalone headings
- Chapter headers: `Chapter 1` or similar

Each reference and target is stored with its source volume, page number, and surrounding context text.

### Pass 2: Cross-Reference Matching

- All identifiers are normalized (whitespace stripped, en/em dashes converted to hyphens, trailing dots removed)
- For each reference, the tool checks if a matching target exists anywhere across all three volumes
- Section matching includes parent-prefix logic: if Section 5.2 is referenced and a heading "5.2.1" exists, it counts as matched (because 5.2 is the parent of 5.2.1)

### False Positive Filtering

During development, the initial run flagged 33 orphans. Analysis revealed several categories of false positives, which led to a multi-layer filtering system:

**1. External document references** -- References like "Appendix I of the Final Site-Wide Environmental Impact Statement" or "2008 LANL SWEIS" point to other documents, not this one. Filtered by checking a 200-character window around each match for phrases like:
- "2008 LANL SWEIS", "Final Site-Wide Environmental Impact Statement for Continued"
- "CT EIS", "DOE/EIS-0380", "DOE/EIS-0293", "DOE/EA-"
- "previous SWEIS", "prior SWEIS"
- "of the Final SWEIS", "of the 2008 SWEIS"

**2. Regulatory citations** -- References like "10 CFR Part 830" or "42 U.S.C. ss 4321" are legal citations, not internal references. Filtered by detecting CFR, U.S.C., FR, Executive Order, and DOE Order patterns.

**3. External document table/figure citations** -- Patterns like "Source: NRC (2011), Table 4-12" reference tables in other publications. Filtered by detecting "Source:" followed by an author/year pattern within 150 characters before the match.

**4. Legal code chapter references** -- "Los Alamos County Code of Ordinance, Chapter 18" and "Title 18 USC, Chapter 40" are legal citations. Filtered by detecting "Code of Ordinance", "U.S.C.", or "United States Code" in the preceding context.

These filters reduced false positives from 33 to 26 in the first full run, with the remaining items being genuine candidates for human review.

## Dependencies

- **Python 3.10+**
- **PyMuPDF** (`pip install PyMuPDF`) -- PDF text extraction library. Chosen for speed, Windows compatibility, and no Java dependency.

## Usage

```bash
pip install PyMuPDF
python sweis_ref_checker.py
```

The script automatically finds all `.pdf` files in its directory and processes them. Output is written to `sweis_ref_report.txt` in the same directory.

## Output Report

The report (`sweis_ref_report.txt`) contains four sections:

1. **Summary Statistics** -- Total counts of references, targets, matched, and orphaned, broken down by type
2. **Potentially Orphaned References** -- Each unmatched reference with:
   - Reference type and ID
   - Raw text as it appeared in the document
   - Source volume and page number
   - Surrounding context (150-character window) for quick human review
   - Occurrence count (deduplicated, showing up to 5 locations)
3. **All Targets Found** -- Complete inventory of every section heading, table label, figure label, appendix header, and chapter heading found across all volumes
4. **All References Found** -- Complete list of every unique reference grouped by type, marked as OK or ORPHANED

## Initial Results (January 2025 Draft SWEIS)

| Metric | Count |
|--------|-------|
| Pages processed | 1,223 |
| Total unique references | 559 |
| Total unique targets | 939 |
| Matched | 533 (95.3%) |
| Potentially orphaned | 26 (4.7%) |

### Orphan Breakdown

| Type | Orphaned | Notes |
|------|----------|-------|
| Section | 15 | Sections 2.3.3, 2.4, 2.4.4, 2.4.5, 2.6.5, 3.1.3, 3.2.1.3, 3.2.1.4, 3.4.1.1, 3.4.1.3, 3.5.1, 4.2.1.8, 5.2.3, 5.8.1.3, 5.11.1.12 |
| Table | 6 | Tables 2.3-1, 3.5-1, 3.5-2, 5.5-14, A.2.2.-3, F.4-1 |
| Figure | 4 | Figures 1.1-2, 3.1-2, 3.2-1, 4.4-11 |
| Appendix | 1 | Appendix M (classified -- expected to be absent from public PDFs) |

### Notable Findings

- **Confirmed typo**: "Table A.2.2.-3" (extra dot) is referenced on Vol 2 page 48, but the actual table label immediately following it is "Table A.2.2-3" without the extra dot.
- **Classified appendix**: Appendix M is referenced 6 times but is classified and would not have a heading in the public PDF volumes. This is expected, not an error.
- **Deep section numbers**: Several orphaned sections (e.g., 3.2.1.3, 3.2.1.4, 3.4.1.1) use 4-level numbering that may indicate headings at a depth where formatting differs from what the regex captures, or sections that were renumbered.

## Known Limitations

- **Linked Content Relevance**: This tool looks for orphaned references only, it can't check to see if a linked reference is contextually accurate or relevant.
- **PDF text extraction quality**: PyMuPDF extracts text well but may miss headings embedded in images, non-standard fonts, or formatted as vector graphics rather than text.
- **Line-break sensitivity**: Section headings split across lines (e.g., "Section\n3.4.1.3") are handled by the reference extractor but may not be captured as targets if the heading itself spans lines.
- **Table of Contents**: TOC entries are extracted as targets alongside the actual headings, which improves matching but could occasionally create false matches for a heading that appears only in the TOC but not in the body (unlikely in practice).
- **Classified/restricted content**: Appendix L (export-controlled) and Appendix M (classified) are referenced but may not appear in the public PDF set.
- **External reference filter**: While improved through iteration, some edge cases may still slip through. The context-based filtering relies on nearby text patterns and may miss unusual citation formats.

## File Structure

```
SWEIS/
  draft-eis-0552-lanl-site-wide-summary-2025-01_0.pdf   (5.5 MB, Summary)
  draft-eis-0552-lanl-site-wide-vol1-2025-01_0.pdf      (22 MB, Volume 1)
  draft-eis-0552-lanl-site-wide-vol2-2025-01_0.pdf       (26 MB, Volume 2)
  sweis_ref_checker.py                                    (Reference checker script)
  sweis_ref_report.txt                                    (Generated report)
  README.md                                               (This file)
```
