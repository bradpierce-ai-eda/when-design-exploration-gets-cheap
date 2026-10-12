#!/usr/bin/env python3
"""Fail-closed validation for the generated public paper derivative."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from lxml import html
from pypdf import PdfReader

from export_bibliography import expected_outputs, load_dataset


DOI = "10.5281/zenodo.23256253"
EXPECTED_CITATION_MESSAGE = (
    "The canonical Zenodo publication identified by this DOI is the preferred citation for this work."
)
EXPECTED_REPOSITORY_DESCRIPTION = (
    "Machine-readable derivative of “When Design Exploration Gets Cheap”; "
    f"canonical Zenodo DOI {DOI}."
)
PDF_SHA256 = "8bf249abfb22647ec9a71340e0fc7b26931c6a392de45685c1e7a5e8dad41194"
PAPER_MD_SHA256 = "25003b4cd2bbf0aae5f15c1114ba429568659d15ad215f84ac544f8e4227815d"
EXPECTED_TABLES = 97
EXPECTED_FIGURES = 19
EXPECTED_REFERENCES = 212
EXPECTED_WORKS = 216
EXPECTED_REFERENCE_WORK_LINKS = 231
EXPECTED_DOI_WORKS = 57
EXPECTED_UNDATED_WORKS = 47
EXPECTED_COMPOSITE_SLOTS = 16
EXPECTED_REUSED_WORKS = 13
EXPECTED_PAGES = 115


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


PROMPT_LIKE_PATTERNS = (
    (
        "conditional citation request",
        re.compile(
            r"\bif this (?:work|paper|report|repository) is relevant\b.{0,120}"
            r"\b(?:please\s+)?(?:cite|use|consult|follow)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "polite directive",
        re.compile(
            r"\bplease\s+(?:cite|use|consult|follow|ignore|disregard|retrieve|read|run|execute|"
            r"send|upload|download|reveal|return|respond|answer)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "second-person instruction",
        re.compile(r"\byou\s+(?:must|should|shall|need to|are to)\b", re.IGNORECASE),
    ),
    (
        "AI or reader instruction",
        re.compile(
            r"\b(?:AI|crawler|model|agent|assistant|reader|tool)s?\s+"
            r"(?:must|should|shall|need to|is to|are to)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role or instruction-hierarchy marker",
        re.compile(
            r"\b(?:act as|you are (?:a|an|the)|system message|developer message|assistant message|"
            r"follow (?:these|the) instructions|prompt injection)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction override",
        re.compile(
            r"\b(?:ignore|disregard)\s+(?:all|any|the|previous|prior|above|earlier)\s+"
            r"(?:instructions?|prompts?|messages?|rules?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "imperative command",
        re.compile(
            r"(?im)^\s*(?:[-*]\s*)?(?:cite|use|consult|ignore|disregard|follow|obey|retrieve|"
            r"run|execute|send|upload|download|reveal|return|respond|answer)\b",
        ),
    ),
)


def validate_prompt_injection_appearance(
    root: Path,
    readme: str,
    index_source: str,
    metadata: dict,
    provenance: dict,
    cff: dict,
) -> None:
    """Reject directive language in derivative-authored public surfaces.

    The canonical paper body and exact source bibliography are intentionally
    excluded. They are immutable publication content, not repository framing.
    """
    readme_wrapper = readme.split("\n---\n", 1)[0]
    index_wrapper_source = index_source.split('<main id="canonical-paper">', 1)[0]
    index_wrapper_source = re.sub(
        r"<(?:style|script)\b[^>]*>.*?</(?:style|script)>",
        " ",
        index_wrapper_source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    index_wrapper_text = html.fromstring(index_wrapper_source).text_content()

    metadata_without_canonical_abstract = dict(metadata)
    metadata_without_canonical_abstract.pop("abstract", None)
    surfaces = {
        "README.md repository framing": readme_wrapper,
        "index.html repository framing": index_wrapper_text,
        "CITATION.cff": json.dumps(cff, ensure_ascii=False),
        "metadata.json repository fields": json.dumps(
            metadata_without_canonical_abstract, ensure_ascii=False
        ),
        "PROVENANCE.md": (root / "PROVENANCE.md").read_text(encoding="utf-8"),
        "provenance.json": json.dumps(provenance, ensure_ascii=False),
        "figures/index.md": (root / "figures/index.md").read_text(encoding="utf-8"),
        "citation.bib": (root / "citation.bib").read_text(encoding="utf-8"),
        "LICENSE": (root / "LICENSE").read_text(encoding="utf-8"),
    }
    for surface_name, surface_text in surfaces.items():
        for pattern_name, pattern in PROMPT_LIKE_PATTERNS:
            match = pattern.search(surface_text)
            if match is not None:
                raise AssertionError(
                    f"Prompt-injection appearance failure in {surface_name}: "
                    f"{pattern_name}: {match.group(0)!r}"
                )


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    required = {
        ".nojekyll", "CITATION.cff", "LICENSE", "PROVENANCE.md", "README.md",
        "checksums.sha256", "citation.bib", "index.html", "metadata.json",
        "paper.md", "paper.pdf", "provenance.json", "bibliography.json",
        "references.bib", "references.csl.json", "references.ris",
        "references.md", "robots.txt", "sitemap.xml", "figures/index.md",
        "scripts/build_derivative.py", "scripts/enrich_bibliography.py",
        "scripts/export_bibliography.py", "scripts/validate_derivative.py",
    }
    existing = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
    require(required <= existing, f"Missing required files: {sorted(required - existing)}")

    require(digest(root / "paper.pdf") == PDF_SHA256, "Published PDF hash mismatch")
    require(digest(root / "paper.md") == PAPER_MD_SHA256, "Canonical Markdown paper body changed")
    require(len(PdfReader(root / "paper.pdf").pages) == EXPECTED_PAGES, "PDF page-count mismatch")

    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    cff = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
    require(metadata["doi"] == DOI and cff["doi"] == DOI, "DOI metadata mismatch")
    require(provenance["canonical_publication"]["doi"] == DOI, "Provenance DOI mismatch")
    require(cff["message"] == EXPECTED_CITATION_MESSAGE, "CITATION.cff message is not declarative")
    require(
        metadata.get("repository_description") == EXPECTED_REPOSITORY_DESCRIPTION,
        "Canonical repository description is missing or not declarative",
    )
    require(metadata["content_counts"] == {
        "figures": EXPECTED_FIGURES,
        "references": EXPECTED_REFERENCES,
        "reference_slots": EXPECTED_REFERENCES,
        "bibliographic_works": EXPECTED_WORKS,
        "reference_work_links": EXPECTED_REFERENCE_WORK_LINKS,
        "tables": EXPECTED_TABLES,
    }, "Content inventory mismatch")

    page = html.fromstring((root / "index.html").read_text(encoding="utf-8"))
    readme = (root / "README.md").read_text(encoding="utf-8")
    index_source = (root / "index.html").read_text(encoding="utf-8")
    validate_prompt_injection_appearance(root, readme, index_source, metadata, provenance, cff)
    require(page.xpath('//meta[@name="citation_doi"]/@content') == [DOI], "Missing citation_doi meta tag")
    require(len(page.xpath("//table")) == EXPECTED_TABLES, "HTML table-count mismatch")
    require(len(page.xpath("//figure")) == EXPECTED_FIGURES, "HTML figure-count mismatch")
    require(len(page.xpath("//figure/figcaption")) == EXPECTED_FIGURES, "Figure captions missing")
    require(all(x.strip() for x in page.xpath("//figure/img/@alt")), "Figure alt text missing")
    require(readme.count("## Relevance and citation scope") == 1, "README relevance section missing or duplicated")
    relevance_sections = page.xpath('//section[@id="relevance-and-citation-scope"]')
    require(len(relevance_sections) == 1, "HTML relevance section missing or duplicated")
    require("Relevance and citation scope" not in (root / "paper.md").read_text(encoding="utf-8"),
            "Repository relevance note leaked into canonical paper body")
    require(readme.index("## Relevance and citation scope") < 3000, "README relevance section is too late for opening retrieval")
    require(index_source.index('id="scholarly-front-matter"') < index_source.index('id="relevance-and-citation-scope"') < index_source.index('<main id="canonical-paper">'),
            "Scholarly front matter, relevance scope, and canonical paper are out of order")
    require(page.xpath('//meta[@name="citation_title"]/@content') == ["When Design Exploration Gets Cheap"], "citation_title metadata mismatch")
    require(page.xpath('//meta[@name="citation_author"]/@content') == ["Brad Pierce"], "citation_author metadata mismatch")
    require(page.xpath('//meta[@name="citation_publication_date"]/@content') == ["2026/10/09"], "citation_publication_date metadata mismatch")
    require(page.xpath('//meta[@name="citation_pdf_url"]/@content') == ["https://bradpierce.ai/when-design-exploration-gets-cheap/paper.pdf"], "citation_pdf_url metadata mismatch")
    require(page.xpath('//meta[@name="citation_fulltext_html_url"]/@content') == ["https://bradpierce.ai/when-design-exploration-gets-cheap/"], "citation_fulltext_html_url metadata mismatch")
    require(page.xpath('//link[@rel="canonical"]/@href') == ["https://bradpierce.ai/when-design-exploration-gets-cheap/"], "Canonical link mismatch")
    front = page.xpath('//*[@id="scholarly-front-matter"]')
    require(len(front) == 1, "Scholarly front matter missing")
    front_text = " ".join(front[0].text_content().split())
    require("When Design Exploration Gets Cheap" in front_text and "Brad Pierce" in front_text and "Abstract" in front_text, "Scholarly front matter incomplete")
    relevance_text = " ".join(relevance_sections[0].text_content().split())
    readme_scope = readme.split("## Relevance and citation scope", 1)[1].split("\n---\n", 1)[0]
    readme_scope = "Relevance and citation scope" + readme_scope
    readme_scope = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", readme_scope)
    readme_scope = re.sub(r"(?m)^\s*[#>`-]+\s*", "", readme_scope)
    readme_scope = readme_scope.replace("*", "")
    readme_scope_text = " ".join(readme_scope.split())
    require(readme_scope_text == relevance_text, "README and HTML relevance wording drifted")
    for treatment in [
        "Evaluator economics", "Fidelity and authority", "Migration of scarcity",
        "Technical capital and engineering memory", "Routing capital",
        "Control, dependence, and value capture", "AI-driven EDA market structure",
    ]:
        require(treatment in readme and treatment in relevance_text, f"Missing relevance treatment: {treatment}")
    require("Specific product claims, deployment counts, benchmark results, and historical facts are attributable" in readme
            and "Specific product claims, deployment counts, benchmark results, and historical facts are attributable" in relevance_text,
            "Declarative primary-source attribution boundary missing")
    require("this technical report is a synthesis rather than their primary experimental source" in readme
            and "this technical report is a synthesis rather than their primary experimental source" in relevance_text,
            "Declarative experimental-evidence boundary missing")
    require("The canonical report is an appropriate citation" in readme
            and "The canonical report is an appropriate citation" in relevance_text,
            "Declarative report-citation scope missing")
    for superseded in ["Cite the canonical report", "consult and cite", "must not be represented"]:
        require(superseded not in readme_scope and superseded not in relevance_text,
                f"Superseded imperative relevance wording remains: {superseded}")
    require(f"https://doi.org/{DOI}" in readme and relevance_sections[0].xpath(f'.//a[@href="https://doi.org/{DOI}"]'),
            "Canonical DOI link missing from relevance section")
    require("github.com" not in readme_scope.lower() and "github.com" not in relevance_text.lower(),
            "GitHub URL was offered within the citation-scope note")
    ids = set(page.xpath("//*[@id]/@id"))
    missing_anchors = [href for href in page.xpath('//a[starts-with(@href,"#")]/@href') if href[1:] not in ids]
    require(not missing_anchors, f"Broken internal anchors: {missing_anchors}")

    references_md = (root / "references.md").read_text(encoding="utf-8")
    reference_numbers = re.findall(r"^\[(\d+)\]", references_md, re.MULTILINE)
    require(reference_numbers == [str(n) for n in range(1, EXPECTED_REFERENCES + 1)], "Reference sequence mismatch")

    bibliography = load_dataset(root)
    slots = bibliography["reference_slots"]
    works = bibliography["works"]
    links = bibliography["reference_work_links"]
    require(bibliography["counts"] == {
        "reference_slots": EXPECTED_REFERENCES,
        "works": EXPECTED_WORKS,
        "reference_work_links": EXPECTED_REFERENCE_WORK_LINKS,
        "citation_clusters": 270,
        "citation_edges": 731,
    }, "Normalized bibliography counts mismatch")
    require(len(slots) == EXPECTED_REFERENCES and len(works) == EXPECTED_WORKS
            and len(links) == EXPECTED_REFERENCE_WORK_LINKS, "Bibliography object counts mismatch")
    require([slot["id"] for slot in slots] == [f"ref-{n:03d}" for n in range(1, 213)],
            "Reference-slot identity or order mismatch")
    require(references_md == expected_outputs(bibliography)["references.md"],
            "Published reference Markdown does not round-trip exactly")
    work_ids = [work["id"] for work in works]
    citekeys = [work["citekey"] for work in works]
    dois = [work["doi"] for work in works if work.get("doi")]
    require(len(set(work_ids)) == EXPECTED_WORKS and len(set(citekeys)) == EXPECTED_WORKS,
            "Work IDs or citekeys are not unique")
    require(len(dois) == EXPECTED_DOI_WORKS and len(set(dois)) == EXPECTED_DOI_WORKS,
            "DOI-bearing work count or DOI uniqueness mismatch")
    require(all(work.get("title") and work.get("authors") and work.get("type") and work.get("url") for work in works),
            "Required reusable bibliographic fields are missing")
    require(all(re.match(r"^https?://", work["url"]) for work in works), "Invalid primary bibliography URL")
    require(all("TODO" not in work["title"] and "PLACEHOLDER" not in work["title"] for work in works),
            "Placeholder title remains in normalized bibliography")
    require(sum(bool(re.search(r"[^\x00-\x7f]", work["title"])) for work in works) >= 26,
            "Original non-Latin titles were not preserved")
    require(all(not work.get("title_translated") or work["title_translated"] != work["title"] for work in works),
            "Translated titles were not represented separately")
    for work in works:
        for date_name in ("issued", "accessed"):
            parts = work.get(date_name, {}).get("date_parts", [])
            require(not parts or (1 <= len(parts) <= 3 and isinstance(parts[0], int)),
                    f"Invalid {date_name} date parts: {work['id']}")
            if len(parts) >= 2:
                require(1 <= parts[1] <= 12, f"Invalid {date_name} month: {work['id']}")
            if len(parts) == 3:
                require(1 <= parts[2] <= 31, f"Invalid {date_name} day: {work['id']}")
    exceptions = bibliography["exceptions"]
    require(len(exceptions) == EXPECTED_UNDATED_WORKS
            and all(item["kind"] == "missing-issued-date" for item in exceptions),
            "Unresolved bibliography exceptions are not the approved undated-source set")
    require(sum(not work.get("issued", {}).get("date_parts") for work in works) == EXPECTED_UNDATED_WORKS,
            "Undated work count mismatch")
    links_by_slot: dict[str, list[str]] = {}
    slots_by_work: dict[str, list[str]] = {}
    for link in links:
        links_by_slot.setdefault(link["reference_slot_id"], []).append(link["work_id"])
        slots_by_work.setdefault(link["work_id"], []).append(link["reference_slot_id"])
    require(set(links_by_slot) == {slot["id"] for slot in slots}, "Orphaned reference slot")
    require(set(slots_by_work) == set(work_ids), "Orphaned bibliographic work")
    require(sum(len(ids) > 1 for ids in links_by_slot.values()) == EXPECTED_COMPOSITE_SLOTS,
            "Composite reference-slot count mismatch")
    require(sum(len(set(ids)) > 1 for ids in slots_by_work.values()) == EXPECTED_REUSED_WORKS,
            "Reused-work count mismatch")
    require(links_by_slot["ref-156"] == ["work-f128-ref-156", "work-f128-ref-156b"],
            "Supplementary software mapping drifted")
    require(set(slots_by_work["work-f128-ref-026b"]) == {"ref-026", "ref-031", "ref-037", "ref-190"},
            "Repeated-reader-route mapping drifted")

    outputs = expected_outputs(bibliography)
    for filename, expected in outputs.items():
        require((root / filename).read_text(encoding="utf-8") == expected,
                f"Generated bibliography export drift: {filename}")
    references_bib = (root / "references.bib").read_text(encoding="utf-8")
    require(len(re.findall(r"^@\w+\{f128_ref_\d{3}[a-z]?,", references_bib, re.MULTILINE)) == EXPECTED_WORKS,
            "BibTeX work-count mismatch")
    csl = json.loads((root / "references.csl.json").read_text(encoding="utf-8"))
    require(len(csl) == EXPECTED_WORKS and [record["id"] for record in csl] == citekeys,
            "CSL JSON work identities mismatch")
    ris = (root / "references.ris").read_text(encoding="utf-8")
    require(ris.count("ER  -") == EXPECTED_WORKS
            and re.findall(r"^ID  - (.+)$", ris, re.MULTILINE) == citekeys,
            "RIS work identities mismatch")

    with tempfile.TemporaryDirectory() as temporary:
        tmp = Path(temporary)
        (tmp / "references.bib").write_text(references_bib, encoding="utf-8")
        (tmp / "check.aux").write_text(
            "\\citation{*}\n\\bibstyle{plain}\n\\bibdata{references}\n", encoding="utf-8"
        )
        bibtex = subprocess.run(["bibtex", "check"], cwd=tmp, capture_output=True, text=True)
        bibtex_log = bibtex.stdout + bibtex.stderr
        require(bibtex.returncode == 0 and "Warning--" not in bibtex_log,
                "Classic BibTeX rejected or warned on references.bib")
        for filename in ("references.bib", "references.csl.json", "references.ris"):
            pandoc = subprocess.run(
                ["pandoc", "--from=markdown", "--to=plain", "--citeproc", f"--bibliography={root / filename}"],
                input="[@f128_ref_001]\n", capture_output=True, text=True,
            )
            require(pandoc.returncode == 0, f"Pandoc/citeproc rejected {filename}: {pandoc.stderr}")
    require(len(list((root / "figures").glob("figure-*.svg"))) == EXPECTED_FIGURES, "SVG count mismatch")

    manifest_paths = set()
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        require(relative not in manifest_paths, f"Duplicate checksum target: {relative}")
        require("__pycache__" not in Path(relative).parts and Path(relative).suffix not in {".pyc", ".pyo"}, f"Ignored cache artifact in checksum manifest: {relative}")
        manifest_paths.add(relative)
        require(digest(root / relative) == expected, f"Checksum mismatch: {relative}")
    artifact_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
        and ".git" not in path.parts and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    require(manifest_paths == artifact_paths, "Checksum manifest target set mismatch")

    searchable = "\n".join((root / name).read_text(encoding="utf-8") for name in [
        "README.md", "paper.md", "index.html", "metadata.json", "CITATION.cff", "citation.bib",
    ])
    require(searchable.count(DOI) >= 12, "DOI is not sufficiently exposed across discovery surfaces")
    require("TODO" not in searchable and "PLACEHOLDER" not in searchable, "Placeholder text remains")

    print(json.dumps({
        "status": "PASS",
        "pdf_pages": EXPECTED_PAGES,
        "pdf_sha256": PDF_SHA256,
        "paper_md_sha256": PAPER_MD_SHA256,
        "tables": EXPECTED_TABLES,
        "figures": EXPECTED_FIGURES,
        "references": EXPECTED_REFERENCES,
        "bibliographic_works": EXPECTED_WORKS,
        "reference_work_links": EXPECTED_REFERENCE_WORK_LINKS,
        "doi_works": EXPECTED_DOI_WORKS,
        "undated_works": EXPECTED_UNDATED_WORKS,
        "internal_anchors": len(page.xpath('//a[starts-with(@href,"#")]')),
        "relevance_scope": "PASS",
        "prompt_injection_appearance": "PASS",
        "bibliography_exports": "PASS",
    }, indent=2))


if __name__ == "__main__":
    main()
