---
name: build-brd-from-files
description: Create a traceable Business Requirements Document (BRD) from a user-supplied list of source-file paths, including mixed office files, PDFs, scans, images, spreadsheets, notes, exports, and text files. Use when Codex must read source material, recover usable content from files that parse as junk or are scanned, consolidate evidence, and produce a reusable Markdown or Word BRD with requirements, use cases, decisions, and source traceability.
---

# Build BRD from Files

Create an evidence-grounded BRD from the files supplied by the user. Never treat a failed or low-quality extraction as evidence.

## Workflow

1. Confirm the supplied paths exist. If a path is a directory, inventory its files with `rg --files`; do not silently include unrelated sibling files.
2. Extract each source into a reviewable evidence file. Run:

   ```bash
   SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/build-brd-from-files"
   python3 "$SKILL_DIR/scripts/extract_sources.py" \
     --output work/source-extract.md \
     /absolute/path/to/source-1 /absolute/path/to/source-2
   ```

   Use an output path inside the current workspace. Increase `--max-chars` only for a source whose important content was visibly truncated.
3. Inspect the extraction manifest before analysis. It records the method, quality assessment, and text for each file.
4. Handle extraction quality before drafting:
   - Use entries marked **good** as evidence.
   - For **review** or **failed** entries, try a format-appropriate alternative once (for example, render a PDF or Word file and inspect it; use OCR for a scan; inspect the native file in its app if conversion fails).
   - Preserve exact uncertainty: label unusable or ambiguous material as `Requires validation`; do not infer missing facts from fragments or junk characters.
   - Cite material from a file only after it has passed this check.
5. Consolidate the evidence. De-duplicate only when the underlying intent is the same. Preserve conflicting stakeholder statements and flag them as decisions needed.
6. Write the BRD in Markdown. Save it in the user-facing output location requested by the user; otherwise use `outputs/BRD-<topic>.md` in the current workspace.
7. Create a `.docx` version only when requested. Use the `documents` skill to create, render, and visually verify the Word document. Keep the Markdown BRD as the canonical, auditable content.

## Extraction rules

- Use the bundled extractor before manually parsing a mixed list. It detects common text encodings and extracts `.docx`, `.pptx`, `.xlsx`, ODF, PDF, image, text, CSV, JSON, HTML, and selected legacy office files.
- The extractor uses OCR when `tesseract` is available and a PDF or image has no useful text layer. It never pretends OCR occurred when it was unavailable.
- Treat the manifest's quality indicator as a gate, not a guarantee. Check names, amounts, IDs, tables, and negations against the rendered original where they materially affect a requirement.
- Do not quote long passages. Capture a short source locator and a concise paraphrase instead.
- For every requirement, use a locator in this exact form: `[Source: filename | page/slide/sheet/section if known]`. If a precise location cannot be recovered, cite the filename and state `location not recovered`.

## BRD content

Adapt sections to the available evidence, but retain the following order. Mark absent information as **Not provided**; do not manufacture it.

1. **Document control** — title, version/date, prepared for, source inventory.
2. **Executive summary** — problem, proposed business outcome, and central evidence.
3. **Business context and problem statement**.
4. **Goals, objectives, and success measures** — distinguish supplied metrics from proposed measures.
5. **Scope** — in scope, out of scope, and scope needing confirmation.
6. **Stakeholders and user groups**.
7. **Current state and pain points**.
8. **Business requirements** — `BR-###`, statement, rationale, priority, source, and validation status.
9. **Functional requirements** — `FR-###`, statement, acceptance criteria where evidenced, priority, dependency, source, and validation status.
10. **Non-functional requirements** — `NFR-###`, measurable target only when supplied, source, and validation status.
11. **Business rules, data, reporting, and integrations** — include only applicable subsections.
12. **Use cases** — see the template below.
13. **Assumptions, constraints, dependencies, risks, open questions, and decisions required**.
14. **Traceability matrix** — requirement/objective ID → source locator(s) → related use case(s) → validation status.

Use a table for requirements and the traceability matrix. Keep paragraphs concise and business-oriented.

### Use-case template

For each evidenced user interaction or workflow, use:

```markdown
#### UC-###: <name>
- **Primary actor:**
- **Goal:**
- **Trigger:**
- **Preconditions:**
- **Main flow:**
  1.
- **Alternate flows / exceptions:**
- **Postconditions:**
- **Related requirements:**
- **Source:**
```

Do not turn a vague feature request into a detailed process. Where a use case is inferred from a requirement, label it **Proposed for validation** and identify the inference.

## Prioritization and accuracy

- Retain a source-provided priority exactly. When no priority is supplied, use `Not specified`; do not assign Must/Should/Could from intuition.
- Keep requirements, enhancement requests, and pain points distinct. Cross-reference related IDs rather than repeating the same finding.
- State conflicts, missing detail, and incompatible requirements plainly in **Open questions** or **Decisions required**.
- Separate source facts from analyst proposals. Prefix proposals with `Proposed:` and cite the evidence that motivated them.
- Never invent owners, dates, costs, integrations, volumes, compliance obligations, acceptance criteria, or KPIs.

## Reuse examples

Use the skill by naming it and supplying absolute paths. Specify a deliverable preference when it matters.

```text
Use $build-brd-from-files to create a BRD from these paths:
/Users/me/project/interviews.docx
/Users/me/project/workshop-notes.pdf
/Users/me/project/current-process.xlsx

Save a traceable Markdown BRD in outputs/ and identify source files that need validation.
```

```text
Use $build-brd-from-files to read the files in /Users/me/discovery/vendor-portal/.
Create a BRD for the vendor onboarding process, including use cases, functional and non-functional requirements, open questions, and a traceability matrix. Deliver both Markdown and a visually verified Word document.
```

```text
Use $build-brd-from-files on these scan-heavy files:
/Users/me/inputs/stakeholder-whiteboard.jpg
/Users/me/inputs/legacy-requirements.pdf

Do OCR if possible. Do not rely on unreadable text; list every item needing stakeholder validation. Draft the BRD only from verified content.
```

```text
Use $build-brd-from-files to consolidate these files into a gap-analysis BRD:
/Users/me/as-is-process.pptx
/Users/me/target-state.docx
/Users/me/support-tickets.csv

Highlight conflicts between the target state and support evidence, and leave all unsourced priorities as Not specified.
```

## Resource

- `scripts/extract_sources.py` — create a manifest and normalized text from a list of source paths. In a standard installation, run `python3 "${CODEX_HOME:-$HOME/.codex}/skills/build-brd-from-files/scripts/extract_sources.py" --help` for options.
