#!/usr/bin/env python3
"""Generate every public bibliography view from bibliography.json."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DOI = "10.5281/zenodo.23256253"


def load_dataset(root: Path) -> dict[str, Any]:
    data = json.loads((root / "bibliography.json").read_text(encoding="utf-8"))
    slots = data["reference_slots"]
    works = data["works"]
    links = data["reference_work_links"]
    assert [slot["number"] for slot in slots] == list(range(1, 213))
    assert [slot["id"] for slot in slots] == [f"ref-{n:03d}" for n in range(1, 213)]
    assert len({work["id"] for work in works}) == len(works)
    assert len({work["citekey"] for work in works}) == len(works)
    work_ids = {work["id"] for work in works}
    slot_ids = {slot["id"] for slot in slots}
    assert all(link["work_id"] in work_ids and link["reference_slot_id"] in slot_ids for link in links)
    linked_slots = {link["reference_slot_id"] for link in links}
    assert linked_slots == slot_ids
    return data


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# References",
        "",
        f"Canonical paper DOI: [{DOI}](https://doi.org/{DOI})",
        "",
    ]
    for slot in data["reference_slots"]:
        lines.extend([f"[{slot['number']}] {slot['published_markdown']}", ""])
    return "\n".join(lines).rstrip() + "\n"


def bib_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
        "_": r"\_",
    }
    return "".join(replacements.get(char, char) for char in value)


def bib_authors(authors: list[dict[str, str]]) -> str | None:
    rendered: list[str] = []
    for author in authors:
        if author.get("literal"):
            rendered.append("{" + bib_escape(author["literal"]) + "}")
        elif author.get("family"):
            family = bib_escape(author["family"])
            given = bib_escape(author.get("given", ""))
            rendered.append(f"{family}, {given}".rstrip(", "))
    return " and ".join(rendered) if rendered else None


BIB_TYPES = {
    "article-journal": "article",
    "paper-conference": "inproceedings",
    "book": "book",
    "chapter": "incollection",
    "report": "techreport",
    "standard": "misc",
    "manuscript": "unpublished",
    "software": "misc",
    "dataset": "misc",
    "webpage": "misc",
}


def bib_type(work: dict[str, Any]) -> str:
    """Choose a classic-BibTeX-safe type without inventing required fields."""
    work_type = work["type"]
    if work_type == "article-journal" and not work.get("container_title"):
        return "misc"
    if work_type in {"paper-conference", "chapter"} and not work.get("container_title"):
        return "misc"
    if work_type == "book" and not work.get("publisher"):
        return "misc"
    if work_type == "report" and not (work.get("institution") or work.get("publisher")):
        return "misc"
    return BIB_TYPES.get(work_type, "misc")


def render_bibtex(data: dict[str, Any]) -> str:
    entries: list[str] = []
    for work in data["works"]:
        entry_type = bib_type(work)
        fields: list[tuple[str, str]] = []
        author = bib_authors(work.get("authors", []))
        if author:
            fields.append(("author", author))
        fields.append(("title", bib_escape(work["title"])))
        notes: list[str] = []
        if work.get("title_translated"):
            notes.append(f"English title: {work['title_translated']}")
        if entry_type == "unpublished":
            notes.append(f"arXiv preprint {work['arxiv']}" if work.get("arxiv") else "Preprint or unpublished manuscript")
        if notes:
            fields.append(("note", bib_escape(". ".join(notes))))
        issued = work.get("issued", {}).get("date_parts", [])
        if issued:
            fields.append(("year", str(issued[0])))
            if len(issued) > 1:
                fields.append(("month", str(issued[1])))
        mapping = [
            ("container_title", "journal" if work["type"] == "article-journal" else "booktitle"),
            ("volume", "volume"), ("issue", "number"), ("pages", "pages"),
            ("edition", "edition"), ("language", "language"),
            ("doi", "doi"), ("isbn", "isbn"), ("url", "url"),
        ]
        for source_key, bib_key in mapping:
            value = work.get(source_key)
            if value:
                fields.append((bib_key, bib_escape(str(value))))
        if entry_type == "techreport":
            institution = work.get("institution") or work.get("publisher")
            if institution:
                fields.append(("institution", bib_escape(str(institution))))
        elif work.get("publisher"):
            fields.append(("publisher", bib_escape(str(work["publisher"]))))
        elif work.get("institution"):
            fields.append(("institution", bib_escape(str(work["institution"]))))
        accessed = work.get("accessed", {}).get("date_parts", [])
        if accessed:
            fields.append(("urldate", "-".join(str(x) for x in accessed)))
        fields.append(("keywords", bib_escape("; ".join(work.get("reference_slots", [])))))
        fields.append(("annotation", bib_escape(work["published_context"])))
        body = ",\n".join(f"  {key} = {{{value}}}" for key, value in fields)
        entries.append(f"@{entry_type}{{{work['citekey']},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"


def csl_authors(authors: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for author in authors:
        if author.get("literal"):
            output.append({"literal": author["literal"]})
        else:
            person = {key: author[key] for key in ("family", "given") if author.get(key)}
            if person:
                output.append(person)
    return output


def render_csl(data: dict[str, Any]) -> str:
    records: list[dict[str, Any]] = []
    for work in data["works"]:
        record: dict[str, Any] = {
            "id": work["citekey"],
            "type": work["type"],
            "title": work["title"],
        }
        mapping = {
            "container_title": "container-title",
            "volume": "volume", "issue": "issue", "pages": "page",
            "publisher": "publisher", "edition": "edition",
            "language": "language", "doi": "DOI", "isbn": "ISBN", "url": "URL",
        }
        for source_key, csl_key in mapping.items():
            if work.get(source_key):
                record[csl_key] = work[source_key]
        authors = csl_authors(work.get("authors", []))
        if authors:
            record["author"] = authors
        if work.get("issued", {}).get("date_parts"):
            record["issued"] = {"date-parts": [work["issued"]["date_parts"]]}
        if work.get("accessed", {}).get("date_parts"):
            record["accessed"] = {"date-parts": [work["accessed"]["date_parts"]]}
        notes = []
        if work.get("title_translated"):
            notes.append(f"English title: {work['title_translated']}.")
        notes.append(f"Published reference slots: {', '.join(work.get('reference_slots', []))}.")
        notes.append(f"Published context: {work['published_context']}")
        record["note"] = " ".join(notes)
        records.append(record)
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


RIS_TYPES = {
    "article-journal": "JOUR", "paper-conference": "CPAPER", "book": "BOOK",
    "chapter": "CHAP", "report": "RPRT", "standard": "STAND",
    "manuscript": "UNPB", "software": "COMP", "dataset": "DATA", "webpage": "ELEC",
}


def render_ris(data: dict[str, Any]) -> str:
    records: list[str] = []
    for work in data["works"]:
        lines = [f"TY  - {RIS_TYPES.get(work['type'], 'GEN')}", f"ID  - {work['citekey']}"]
        for author in work.get("authors", []):
            if author.get("literal"):
                lines.append(f"AU  - {author['literal']}")
            elif author.get("family"):
                lines.append(f"AU  - {author['family']}, {author.get('given', '')}".rstrip())
        lines.append(f"TI  - {work['title']}")
        if work.get("title_translated"):
            lines.append(f"TT  - {work['title_translated']}")
        mapping = [
            ("container_title", "T2"), ("volume", "VL"), ("issue", "IS"),
            ("pages", "SP"), ("publisher", "PB"), ("language", "LA"),
            ("doi", "DO"), ("url", "UR"),
        ]
        for source_key, tag in mapping:
            if work.get(source_key):
                lines.append(f"{tag}  - {work[source_key]}")
        issued = work.get("issued", {}).get("date_parts", [])
        if issued:
            lines.append("PY  - " + str(issued[0]))
            lines.append("DA  - " + "/".join(str(x) for x in issued))
        for alternate in work.get("alternate_urls", []):
            lines.append(f"L2  - {alternate}")
        lines.append("N1  - Published reference slots: " + ", ".join(work.get("reference_slots", [])))
        lines.append("ER  -")
        records.append("\n".join(lines))
    return "\n\n".join(records) + "\n"


def expected_outputs(data: dict[str, Any]) -> dict[str, str]:
    return {
        "references.md": render_markdown(data),
        "references.bib": render_bibtex(data),
        "references.csl.json": render_csl(data),
        "references.ris": render_ris(data),
    }


def export_all(root: Path, check: bool = False) -> None:
    data = load_dataset(root)
    outputs = expected_outputs(data)
    for filename, content in outputs.items():
        path = root / filename
        if check:
            assert path.read_text(encoding="utf-8") == content, f"Generated output drift: {filename}"
        else:
            path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    export_all(args.root.resolve(), check=args.check)
    data = load_dataset(args.root.resolve())
    print(json.dumps({
        "status": "PASS",
        "reference_slots": len(data["reference_slots"]),
        "works": len(data["works"]),
        "links": len(data["reference_work_links"]),
        "mode": "check" if args.check else "write",
    }))


if __name__ == "__main__":
    main()
