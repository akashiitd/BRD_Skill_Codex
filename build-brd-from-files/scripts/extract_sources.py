#!/usr/bin/env python3
"""Extract reviewable text from mixed source files for BRD analysis.

The script intentionally reports low-quality or failed extraction rather than
silently using mojibake, binary fragments, or a blank scan as evidence.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic"}
LEGACY_OFFICE_EXTENSIONS = {".doc", ".xls", ".ppt", ".msg", ".odt", ".ods", ".odp"}


@dataclass
class Extracted:
    path: Path
    method: str
    text: str = ""
    notes: list[str] = field(default_factory=list)
    quality: str = "failed"


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(command: list[str], timeout: int = 90) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            errors="replace", timeout=timeout, check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = "".join(
        char for char in value
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cs"}
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def decode_bytes(raw: bytes) -> tuple[str, str]:
    candidates: list[tuple[float, str, str]] = []
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"):
        try:
            value = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not value:
            candidates.append((0, encoding, value))
            continue
        controls = sum(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in value)
        printable = sum(char.isprintable() or char in "\n\t" for char in value)
        replacement = value.count("\ufffd")
        score = printable / len(value) - controls / len(value) * 4 - replacement / len(value) * 5
        candidates.append((score, encoding, value))
    if not candidates:
        return "", "undecodable"
    _, encoding, value = max(candidates, key=lambda item: item[0])
    return clean_text(value), encoding


def text_metrics(value: str) -> tuple[int, float, float]:
    if not value:
        return 0, 1.0, 1.0
    visible = sum(char.isprintable() or char in "\n\t" for char in value)
    controls = sum(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in value)
    alphanumeric = sum(char.isalnum() for char in value)
    return alphanumeric, visible / len(value), controls / len(value)


def has_usable_text(value: str) -> bool:
    alphanumeric, visible_ratio, control_ratio = text_metrics(value)
    return alphanumeric >= 20 and visible_ratio >= 0.95 and control_ratio < 0.01


def xml_text(raw: bytes, tags: set[str]) -> list[str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    values = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in tags and element.text:
            values.append(element.text)
    return values


def zip_members(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def extract_docx(path: Path) -> Extracted:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            (name for name in archive.namelist() if re.match(r"word/(document|header\d+|footer\d+|comments)\.xml$", name)),
        )
        for member in members:
            root = ET.fromstring(archive.read(member))
            for paragraph in root.iter():
                if paragraph.tag.rsplit("}", 1)[-1] != "p":
                    continue
                values: list[str] = []
                for node in paragraph.iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    if local == "t" and node.text:
                        values.append(node.text)
                    elif local == "tab":
                        values.append("\t")
                    elif local in {"br", "cr"}:
                        values.append("\n")
                if values:
                    paragraphs.append("".join(values))
    return Extracted(path, "OOXML Word text", "\n".join(paragraphs))


def natural_key(value: str) -> list[object]:
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r"(\d+)", value)]


def extract_pptx(path: Path) -> Extracted:
    slides: list[str] = []
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)), key=natural_key,
        )
        for member in members:
            values = xml_text(archive.read(member), {"t"})
            number = re.search(r"slide(\d+)\.xml$", member)
            slides.append(f"[Slide {number.group(1) if number else '?'}]\n" + "\n".join(values))
    return Extracted(path, "OOXML PowerPoint text", "\n\n".join(slides))


def cell_reference_sort(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", value or "")
    if not match:
        return (10**9, 10**9)
    letters, row = match.groups()
    column = 0
    for char in letters:
        column = column * 26 + ord(char) - ord("A") + 1
    return int(row), column


def extract_xlsx(path: Path) -> Extracted:
    sections: list[str] = []
    with zipfile.ZipFile(path) as archive:
        members = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in members:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(xml_text(ET.tostring(item), {"t"})) for item in root if item.tag.rsplit("}", 1)[-1] == "si"]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in members:
            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rels = {
                item.attrib.get("Id", ""): item.attrib.get("Target", "")
                for item in rel_root if item.tag.rsplit("}", 1)[-1] == "Relationship"
            }
        sheets: list[tuple[str, str]] = []
        for sheet in workbook.iter():
            if sheet.tag.rsplit("}", 1)[-1] != "sheet":
                continue
            relationship = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), "")
            target = rels.get(relationship, "")
            if target:
                member = target.lstrip("/")
                if not member.startswith("xl/"):
                    member = "xl/" + member
                sheets.append((sheet.attrib.get("name", "Unnamed sheet"), member))
        for name, member in sheets:
            if member not in members:
                continue
            root = ET.fromstring(archive.read(member))
            rows: list[str] = []
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                cells: list[tuple[tuple[int, int], str]] = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    value = ""
                    cell_type = cell.attrib.get("t")
                    raw_value = next((node.text or "" for node in cell if node.tag.rsplit("}", 1)[-1] == "v"), "")
                    if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared):
                        value = shared[int(raw_value)]
                    elif cell_type == "inlineStr":
                        value = "".join(xml_text(ET.tostring(cell), {"t"}))
                    else:
                        value = raw_value
                    if value:
                        cells.append((cell_reference_sort(cell.attrib.get("r", "")), value))
                if cells:
                    rows.append(" | ".join(value for _, value in sorted(cells)))
            sections.append(f"[Sheet: {name}]\n" + "\n".join(rows))
    return Extracted(path, "OOXML Excel values", "\n\n".join(sections))


def extract_odf(path: Path) -> Extracted:
    with zipfile.ZipFile(path) as archive:
        return Extracted(path, "OpenDocument text", "\n".join(xml_text(archive.read("content.xml"), {"p", "h"})))


def extract_pdf(path: Path) -> Extracted:
    notes: list[str] = []
    if command_exists("pdftotext"):
        code, stdout, stderr = run(["pdftotext", "-layout", str(path), "-"])
        if code == 0:
            return Extracted(path, "pdftotext", stdout, notes)
        notes.append(f"pdftotext failed: {stderr.strip() or 'unknown error'}")
    else:
        notes.append("pdftotext is unavailable")
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        return Extracted(path, "pypdf", "\n\n".join(page.extract_text() or "" for page in reader.pages), notes)
    except Exception as exc:  # Import failure and malformed PDFs both need reporting.
        notes.append(f"pypdf fallback unavailable or failed: {exc}")
        return Extracted(path, "no PDF text extractor", "", notes)


def ocr(path: Path, kind: str) -> tuple[str, str]:
    if not command_exists("tesseract"):
        return "", "OCR unavailable: tesseract is not installed"
    if kind == "image":
        code, stdout, stderr = run(["tesseract", str(path), "stdout"], timeout=180)
        return stdout if code == 0 else "", stderr.strip() or "Tesseract failed"
    if not command_exists("pdftoppm"):
        return "", "OCR unavailable for PDF: pdftoppm is not installed"
    with tempfile.TemporaryDirectory(prefix="brd-ocr-") as directory:
        prefix = str(Path(directory) / "page")
        code, _, stderr = run(["pdftoppm", "-r", "200", "-png", str(path), prefix], timeout=240)
        if code != 0:
            return "", stderr.strip() or "Could not render PDF pages for OCR"
        pages: list[str] = []
        for image in sorted(Path(directory).glob("page-*.png"), key=lambda item: natural_key(item.name)):
            code, stdout, stderr = run(["tesseract", str(image), "stdout"], timeout=180)
            if code == 0 and stdout.strip():
                pages.append(f"[OCR {image.stem}]\n{stdout}")
            elif stderr.strip():
                pages.append(f"[OCR warning {image.stem}: {stderr.strip()}]")
        return "\n\n".join(pages), ""


def extract_legacy_office(path: Path) -> Extracted:
    notes: list[str] = []
    if command_exists("textutil"):
        code, stdout, stderr = run(["textutil", "-convert", "txt", "-stdout", str(path)])
        if code == 0:
            return Extracted(path, "macOS textutil", stdout, notes)
        notes.append(f"textutil failed: {stderr.strip() or 'unknown error'}")
    for command in (["antiword", str(path)], ["catdoc", str(path)]):
        if command_exists(command[0]):
            code, stdout, stderr = run(command)
            if code == 0:
                return Extracted(path, command[0], stdout, notes)
            notes.append(f"{command[0]} failed: {stderr.strip() or 'unknown error'}")
    return Extracted(path, "no legacy office extractor", "", notes)


def extract_text(path: Path) -> Extracted:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Extracted(path, "unreadable", "", [str(exc)])
    text, encoding = decode_bytes(raw)
    return Extracted(path, f"decoded {encoding}", text)


def extract_html(path: Path) -> Extracted:
    extracted = extract_text(path)
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", extracted.text)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    extracted.text = clean_text(html.unescape(value))
    extracted.method += " and HTML cleanup"
    return extracted


def extract(path: Path) -> Extracted:
    extension = path.suffix.lower()
    try:
        if extension in {".docx", ".docm"}:
            result = extract_docx(path)
        elif extension in {".pptx", ".pptm"}:
            result = extract_pptx(path)
        elif extension in {".xlsx", ".xlsm"}:
            result = extract_xlsx(path)
        elif extension in {".odt", ".ods", ".odp"}:
            result = extract_odf(path)
        elif extension == ".pdf":
            result = extract_pdf(path)
        elif extension in IMAGE_EXTENSIONS:
            result = Extracted(path, "no native image text", "")
        elif extension in LEGACY_OFFICE_EXTENSIONS:
            result = extract_legacy_office(path)
        elif extension in {".html", ".htm"}:
            result = extract_html(path)
        else:
            result = extract_text(path)
    except (OSError, ET.ParseError, zipfile.BadZipFile, ValueError) as exc:
        result = Extracted(path, "parser failure", "", [str(exc)])

    result.text = clean_text(result.text)
    if extension in IMAGE_EXTENSIONS or (extension == ".pdf" and not has_usable_text(result.text)):
        ocr_text, ocr_note = ocr(path, "image" if extension in IMAGE_EXTENSIONS else "pdf")
        if has_usable_text(clean_text(ocr_text)):
            result.text = clean_text(ocr_text)
            result.method += " + OCR"
            result.notes.append("Used OCR because no usable native text was extracted")
        else:
            result.notes.append(ocr_note or "OCR produced no usable text")

    if not result.text:
        result.quality = "failed"
    elif result.method.endswith(" + OCR"):
        # OCR can be extremely useful, but recognition errors are especially
        # risky in requirements work (for example, a missing "not" or a
        # mistaken identifier). Require a visual check before treating it as
        # final evidence.
        result.quality = "review"
        result.notes.append("OCR text recovered; visually verify material claims against the original before using it as evidence")
    elif has_usable_text(result.text):
        result.quality = "good"
    else:
        result.quality = "review"
        result.notes.append("Text may be incomplete, garbled, or too short; verify against the original before using it as evidence")
    return result


def source_paths(arguments: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in arguments:
        path = Path(raw).expanduser()
        if path.is_dir():
            paths.extend(sorted((child for child in path.rglob("*") if child.is_file()), key=lambda item: str(item).lower()))
        else:
            paths.append(path)
    return paths


def report_item(item: Extracted, maximum: int) -> str:
    text = item.text
    truncation = ""
    if len(text) > maximum:
        text = text[:maximum].rstrip()
        truncation = f"\n\n> Extraction truncated at {maximum:,} characters. Re-run with a larger `--max-chars` only if this source is relevant."
    notes = "\n".join(f"- {note}" for note in item.notes) or "- None"
    body = text if text else "_No usable text extracted._"
    return (
        f"## Source: {item.path.name}\n\n"
        f"- **Path:** `{item.path}`\n"
        f"- **Method:** {item.method}\n"
        f"- **Quality:** **{item.quality}**\n"
        f"- **Notes:**\n{notes}\n\n"
        f"### Extracted text\n\n{body}{truncation}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract normalized evidence from mixed BRD source files.")
    parser.add_argument("paths", nargs="+", help="Files or directories to extract. Directories are traversed recursively.")
    parser.add_argument("--output", required=True, help="Markdown manifest to create.")
    parser.add_argument("--max-chars", type=int, default=200_000, help="Maximum extracted characters per source in the manifest (default: 200000).")
    args = parser.parse_args()
    if args.max_chars <= 0:
        parser.error("--max-chars must be positive")

    sources = source_paths(args.paths)
    if not sources:
        parser.error("No files found")
    extracted = [extract(path) if path.exists() and path.is_file() else Extracted(path, "missing", "", ["Path does not exist or is not a file"]) for path in sources]
    summary = {
        "good": sum(item.quality == "good" for item in extracted),
        "review": sum(item.quality == "review" for item in extracted),
        "failed": sum(item.quality == "failed" for item in extracted),
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    document = (
        "# Source Extraction Manifest\n\n"
        "This file is working evidence, not a BRD. Use only sources marked **good** until review items are verified.\n\n"
        f"**Summary:** {len(extracted)} source(s): {summary['good']} good, {summary['review']} review, {summary['failed']} failed.\n\n"
        + "\n---\n\n".join(report_item(item, args.max_chars) for item in extracted)
    )
    output.write_text(document, encoding="utf-8")
    print(f"Created {output} ({len(extracted)} sources: {summary['good']} good, {summary['review']} review, {summary['failed']} failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
