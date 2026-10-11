#!/usr/bin/env python3
"""Create the normalized bibliography dataset from the published list and register.

This is an enrichment tool, not part of ordinary deterministic rebuilds. The
finished bibliography.json is the frozen source for public bibliography exports.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


USER_AGENT = "F128-bibliography-enrichment/1.0 (mailto:brad@bradpierce.com)"
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTHS = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}
GENERIC_LABEL = re.compile(
    r"^(?:DOI\b.*|arXiv\b.*|publisher page|standards page|official page|.*(?:article|page|PDF|filing|documentation|repository|posting))$",
    re.I,
)
MERGED_LOCATOR_SLOTS = {20, 27, 182}
SUPPLEMENTARY_SLOTS = {156, 162, 181}
ACADEMIC_TYPES = {"journal/conference", "preprint/repository", "book/report/other"}
MANUAL_AUTHORITIES: dict[str, dict[str, Any]] = {
    "10.12263/dzxb.20250971": {
        "provider": "Official journal page",
        "url": "https://www.ejournal.org.cn/en/article/doi/10.12263/DZXB.20250971/",
        "record": {
            "type": "article-journal",
            "title": "大语言模型在电子设计自动化中的应用综述",
            "authors": [{"literal": name} for name in ["王静", "林立宇", "李磊", "陈霖谊", "李征", "来耀", "李炎", "姚佳飞", "曾晓洋", "郭宇锋"]],
            "issued": [2026, 6, 25], "container_title": "电子学报", "volume": "54", "issue": "6",
            "pages": "1-17", "publisher": "电子学报", "language": "zh",
            "url": "https://www.ejournal.org.cn/en/article/doi/10.12263/DZXB.20250971/",
        },
    },
    "10.16383/j.aas.c230368": {
        "provider": "Official journal page",
        "url": "https://www.aas.net.cn/article/doi/10.16383/j.aas.c230368",
        "record": {
            "type": "article-journal", "title": "芯片智能布线方法研究综述",
            "authors": [{"literal": "周展文"}, {"literal": "卓汉逵"}],
            "issued": [2024], "container_title": "自动化学报", "volume": "50", "issue": "9",
            "pages": "1671-1703", "publisher": "自动化学报", "language": "zh",
            "url": "https://www.aas.net.cn/article/doi/10.16383/j.aas.c230368",
        },
    },
}

# Values explicitly present in the published reference string but not reliably
# represented by the selected authority record. These are evidence-preserving
# completions, not inferred bibliographic facts.
MANUAL_WORK_FIELDS: dict[str, dict[str, Any]] = {
    "f128_ref_024": {"publisher": "MIT Press"},
    "f128_ref_077": {"type": "book", "publisher": "Harper & Row"},
    "f128_ref_078": {"type": "book", "publisher": "Free Press"},
    "f128_ref_144": {"type": "paper-conference", "container_title": "MLCAD 2026"},
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def markdown_entries(text: str) -> list[tuple[int, str]]:
    entries = re.findall(r"(?ms)^\[(\d+)\]\s+(.*?)(?=\n\n\[\d+\]|\Z)", text)
    parsed = [(int(number), body.strip()) for number, body in entries]
    assert [number for number, _ in parsed] == list(range(1, 213))
    return parsed


def parse_links(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cursor = 0
    while True:
        delimiter = text.find("](", cursor)
        if delimiter < 0:
            break
        depth = 1
        start = delimiter - 1
        while start >= 0:
            if text[start] == "]" and (start == 0 or text[start - 1] != "\\"):
                depth += 1
            elif text[start] == "[" and (start == 0 or text[start - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    break
            start -= 1
        if start < 0:
            cursor = delimiter + 2
            continue
        end = delimiter + 2
        depth = 1
        while end < len(text):
            if text[end] == "(" and text[end - 1] != "\\":
                depth += 1
            elif text[end] == ")" and text[end - 1] != "\\":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if depth:
            raise ValueError("Unclosed Markdown URL")
        output.append({
            "label": text[start + 1:delimiter].replace("\\_", "_"),
            "url": text[delimiter + 2:end].replace("\\_", "_").replace("\\%", "%").replace("\\&", "&"),
            "start": start,
            "end": end + 1,
        })
        cursor = end + 1
    return output


def markdown_plain(text: str) -> str:
    links = parse_links(text)
    for link in reversed(links):
        text = text[:link["start"]] + link["label"] + text[link["end"]:]
    return re.sub(r"\\([_&%])", r"\1", text)


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("https://doi.org/"):
        doi = urllib.parse.unquote(url.split("doi.org/", 1)[1]).strip().lower()
        return "https://doi.org/" + doi
    return url.rstrip("/")


def doi_from_url(url: str) -> str | None:
    normalized = normalize_url(url)
    if normalized.startswith("https://doi.org/10."):
        return normalized.split("doi.org/", 1)[1]
    return None


def title_score(label: str) -> tuple[int, int]:
    cleaned = label.strip(" \"“”'.,;:")
    if GENERIC_LABEL.match(cleaned):
        return (0, len(cleaned))
    return (1, len(cleaned))


def best_label(links: list[dict[str, Any]]) -> str:
    return max((link["label"] for link in links), key=title_score).strip(" \"“”'.,;")


def clean_author_prefix(prefix: str) -> str | None:
    prefix = re.sub(r"^.*?(?:See also|; and|\. and)\s+", "", prefix, flags=re.I)
    prefix = prefix.strip(" \"“”'.,;:()")
    prefix = re.sub(r"^(?:and)\s+", "", prefix, flags=re.I)
    return prefix or None


def literal_authors(prefix: str | None, source_type: str) -> list[dict[str, str]]:
    if not prefix:
        return []
    if source_type not in ACADEMIC_TYPES or re.search(
        r"\b(?:Project|University|Agency|Authority|Department|Ministry|Foundation|Institute|Laboratory|Lab|Systems|Technologies|Careers|Newsroom|Synopsys|Cadence|Siemens|Apple|Google|NVIDIA|Program|Council|Administration|Commission|Society)\b",
        prefix,
        re.I,
    ):
        return [{"literal": prefix}]
    if "et al." in prefix:
        return [{"literal": prefix}]
    personal_list = re.sub(r",?\s+and\s+", ", ", prefix)
    parts = personal_list.split(",")
    parts = [part.strip(" ,") for part in parts if part.strip(" ,")]
    return [{"literal": part} for part in parts] if parts else [{"literal": prefix}]


def detect_language(title: str, script: str | None) -> str | None:
    if re.search(r"[\u3040-\u30ff]", title):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", title):
        return "ko"
    if re.search(r"[\u0400-\u04ff]", title):
        return "ru"
    if script == "Han" or re.search(r"[\u3400-\u9fff]", title):
        return "zh"
    return None


def date_parts(text: str, accessed: bool = False) -> list[int]:
    scope = text
    if accessed:
        match = re.search(r"accessed\s+([^.;]+)", text, re.I)
        if not match:
            return []
        scope = match.group(1)
    full = re.search(
        r"\b(" + "|".join(MONTH_NAMES) + r")\s+(\d{1,2}),\s+(19\d{2}|20\d{2})\b",
        scope,
    )
    if full:
        return [int(full.group(3)), MONTHS[full.group(1)], int(full.group(2))]
    month_year = re.search(r"\b(" + "|".join(MONTH_NAMES) + r")\s+(19\d{2}|20\d{2})\b", scope)
    if month_year:
        return [int(month_year.group(2)), MONTHS[month_year.group(1)]]
    year = re.search(r"\b(19\d{2}|20\d{2})\b", scope)
    return [int(year.group(1))] if year else []


def inferred_type(source_type: str, context: str, url: str) -> str:
    lower = context.lower()
    if "github.com/" in url and "supplementary" in lower:
        return "software"
    if source_type == "journal/conference":
        return "paper-conference" if re.search(r"conference|proceedings|symposium|workshop", lower) else "article-journal"
    if source_type == "preprint/repository":
        return "manuscript"
    if source_type == "book/report/other":
        if "isbn" in lower:
            return "book"
        if re.search(r"standard|v&v\s*\d|iso\s|iec\s", lower):
            return "standard"
        return "report"
    if source_type == "government/program":
        return "report" if re.search(r"plan|strategy|report|filing", lower) else "webpage"
    return "webpage"


def fetch_json(url: str, attempts: int = 3) -> dict[str, Any] | None:
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
    return None


class MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, list[str]] = defaultdict(list)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        name = (values.get("name") or values.get("property") or "").lower()
        if name and values.get("content"):
            self.meta[name].append(values["content"].strip())


def fetch_landing_url(url: str, provider: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            page = raw.decode(charset, errors="replace")
            final_url = response.geturl()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    parser = MetaParser()
    parser.feed(page)
    meta = parser.meta
    title = (meta.get("citation_title") or meta.get("dc.title") or meta.get("og:title") or [None])[0]
    if title:
        title = re.sub(r"[_|]\s*(?:国家预印本平台|National Preprint Platform)\s*$", "", title).strip()
    authors = meta.get("citation_author") or meta.get("dc.creator") or []
    date = (meta.get("citation_date") or meta.get("citation_publication_date") or meta.get("dc.date") or meta.get("citation_year") or [None])[0]
    issued = [int(x) for x in re.findall(r"\d+", date or "")[:3]]
    first = (meta.get("citation_firstpage") or [None])[0]
    last = (meta.get("citation_lastpage") or [None])[0]
    conference = (meta.get("citation_conference_title") or [None])[0]
    journal = (meta.get("citation_journal_title") or [None])[0]
    record = {
        "type": "paper-conference" if conference else ("article-journal" if journal else "manuscript"),
        "title": title,
        "authors": [{"literal": author} for author in authors],
        "issued": issued,
        "container_title": conference or journal or (meta.get("dc.source") or [None])[0],
        "volume": (meta.get("citation_volume") or [None])[0],
        "issue": (meta.get("citation_issue") or [None])[0],
        "pages": f"{first}-{last}" if first and last else first,
        "publisher": (meta.get("citation_publisher") or meta.get("dc.publisher") or [None])[0],
        "language": (meta.get("dc.language") or [None])[0],
        "url": final_url,
    }
    return {"provider": provider, "url": final_url, "record": record}


def fetch_doi_landing(doi: str) -> dict[str, Any] | None:
    return fetch_landing_url(f"https://doi.org/{doi}", "Official DOI landing page")


def fetch_arxiv(ids: list[str], cache_path: Path) -> dict[str, Any]:
    cache = read_json(cache_path) if cache_path.exists() else {}
    missing = [identifier for identifier in ids if identifier not in cache]
    if missing:
        query = urllib.parse.urlencode({"id_list": ",".join(missing), "max_results": len(missing)})
        request = urllib.request.Request(
            "https://export.arxiv.org/api/query?" + query,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                root = ET.fromstring(response.read())
            atom = "{http://www.w3.org/2005/Atom}"
            for entry in root.findall(atom + "entry"):
                identifier = (entry.findtext(atom + "id") or "").rstrip("/").split("/")[-1]
                identifier = re.sub(r"v\d+$", "", identifier)
                published = entry.findtext(atom + "published") or ""
                date = [int(x) for x in re.findall(r"\d+", published)[:3]]
                cache[identifier] = {
                    "provider": "arXiv", "url": f"https://export.arxiv.org/api/query?id_list={identifier}",
                    "record": {
                        "type": "manuscript",
                        "title": " ".join((entry.findtext(atom + "title") or "").split()),
                        "authors": [{"literal": author.findtext(atom + "name") or ""} for author in entry.findall(atom + "author")],
                        "issued": date, "container_title": "arXiv", "publisher": "arXiv",
                        "url": f"https://arxiv.org/abs/{identifier}",
                    },
                }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError):
            pass
        for identifier in missing:
            cache.setdefault(identifier, {"provider": "unavailable", "url": f"https://arxiv.org/abs/{identifier}", "record": None})
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cache


def fetch_doi(doi: str) -> dict[str, Any]:
    if doi in MANUAL_AUTHORITIES:
        return MANUAL_AUTHORITIES[doi]
    encoded = urllib.parse.quote(doi, safe="")
    crossref_url = f"https://api.crossref.org/works/{encoded}"
    crossref = fetch_json(crossref_url)
    if crossref and crossref.get("status") == "ok" and crossref.get("message"):
        return {"provider": "Crossref", "url": crossref_url, "record": crossref["message"]}
    datacite_url = f"https://api.datacite.org/dois/{encoded}"
    datacite = fetch_json(datacite_url)
    if datacite and datacite.get("data", {}).get("attributes"):
        return {"provider": "DataCite", "url": datacite_url, "record": datacite["data"]["attributes"]}
    landing = fetch_doi_landing(doi)
    if landing and landing.get("record", {}).get("title"):
        return landing
    return {"provider": "unavailable", "url": f"https://doi.org/{doi}", "record": None}


def authority_cache(dois: list[str], cache_path: Path) -> dict[str, Any]:
    cache = read_json(cache_path) if cache_path.exists() else {}
    missing = [doi for doi in dois if doi not in cache or cache[doi].get("provider") == "unavailable"]
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_doi, doi): doi for doi in missing}
            for future in concurrent.futures.as_completed(futures):
                doi = futures[future]
                cache[doi] = future.result()
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cache


CROSSREF_TYPES = {
    "journal-article": "article-journal", "proceedings-article": "paper-conference",
    "book": "book", "book-chapter": "chapter", "report": "report",
    "posted-content": "manuscript", "dataset": "dataset", "standard": "standard",
}


def first_value(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value not in (None, "") else None


def normalized_authority(result: dict[str, Any]) -> dict[str, Any]:
    provider = result["provider"]
    record = result.get("record") or {}
    if provider == "Crossref":
        date = []
        for key in ("published-print", "published-online", "published", "issued", "created"):
            parts = record.get(key, {}).get("date-parts", [])
            if parts:
                date = parts[0]
                break
        authors = []
        for author in record.get("author", []):
            person = {key: author[key] for key in ("family", "given") if author.get(key)}
            if person:
                authors.append(person)
        return {
            "type": CROSSREF_TYPES.get(record.get("type")),
            "title": first_value(record, "title"),
            "authors": authors,
            "issued": date,
            "container_title": first_value(record, "container-title"),
            "volume": first_value(record, "volume"), "issue": first_value(record, "issue"),
            "pages": first_value(record, "page") or first_value(record, "article-number"),
            "publisher": first_value(record, "publisher"),
            "language": first_value(record, "language"),
            "isbn": first_value(record, "ISBN"),
            "url": first_value(record, "URL"),
        }
    if provider == "DataCite":
        titles = record.get("titles", [])
        creators = record.get("creators", [])
        authors = []
        for creator in creators:
            if creator.get("familyName"):
                authors.append({"family": creator["familyName"], "given": creator.get("givenName", "")})
            elif creator.get("name"):
                authors.append({"literal": creator["name"]})
        type_name = record.get("types", {}).get("resourceTypeGeneral", "").lower()
        type_map = {
            "text": "report", "journalarticle": "article-journal",
            "conferencepaper": "paper-conference", "preprint": "manuscript",
            "software": "software", "dataset": "dataset", "book": "book",
        }
        return {
            "type": type_map.get(type_name.replace(" ", "")),
            "title": titles[0].get("title") if titles else None,
            "authors": authors,
            "issued": [int(record["publicationYear"])] if record.get("publicationYear") else [],
            "publisher": record.get("publisher"),
            "language": record.get("language"),
            "url": record.get("url"),
        }
    if provider in {"Official DOI landing page", "Official journal page", "Official scholarly page", "arXiv"}:
        return record
    return {}


def similarity(left: str, right: str) -> float:
    clean = lambda s: re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()
    return difflib.SequenceMatcher(None, clean(left), clean(right)).ratio()


def merge_authority(work: dict[str, Any], result: dict[str, Any], exceptions: list[dict[str, Any]]) -> None:
    normalized = normalized_authority(result)
    source = {
        "source": result["url"], "authority": result["provider"],
        "status": "verified" if result["provider"] != "unavailable" else "unavailable",
    }
    if normalized.get("title"):
        score = similarity(work["published_title"], normalized["title"])
        published_non_latin = bool(re.search(r"[^\x00-\x7f]", work["published_title"]))
        authority_ascii = not bool(re.search(r"[^\x00-\x7f]", normalized["title"]))
        if score < 0.55 and published_non_latin and authority_ascii:
            work["title_translated"] = work.get("title_translated") or normalized["title"].strip(" .,")
            work["field_provenance"]["title_translated"] = [source]
        elif score < 0.55:
            exceptions.append({
                "work_id": work["id"], "kind": "title-conflict", "published": work["published_title"],
                "authority": normalized["title"], "similarity": round(score, 3), "source": result["url"],
            })
        else:
            work["title"] = normalized["title"].strip(" .,").replace("{", "").replace("}", "")
            work["field_provenance"]["title"] = [source]
    if normalized.get("authors"):
        published_text = " ".join(
            author.get("literal") or " ".join(filter(None, [author.get("given"), author.get("family")]))
            for author in work.get("published_authors", [])
        )
        authority_text = " ".join(
            author.get("literal") or " ".join(filter(None, [author.get("given"), author.get("family")]))
            for author in normalized["authors"]
        )
        if re.search(r"[^\x00-\x7f]", published_text) and not re.search(r"[^\x00-\x7f]", authority_text):
            work["author_alternates"] = normalized["authors"]
        else:
            work["authors"] = normalized["authors"]
            work["field_provenance"]["authors"] = [source]
    if normalized.get("type"):
        work["type"] = normalized["type"]
        work["field_provenance"]["type"] = [source]
    field_map = {
        "issued": "issued", "container_title": "container_title", "volume": "volume",
        "issue": "issue", "pages": "pages", "publisher": "publisher", "language": "language", "isbn": "isbn",
    }
    for source_key, target in field_map.items():
        value = normalized.get(source_key)
        if value:
            work[target] = {"date_parts": value} if target == "issued" else value
            work["field_provenance"][target] = [source]
    if work.get("doi"):
        work["field_provenance"]["doi"] = [source]
    work["authority_record"] = {"provider": result["provider"], "url": result["url"]}


def role_for(slot: int, index: int, body: str) -> str:
    if index == 0:
        return "primary"
    if slot in SUPPLEMENTARY_SLOTS:
        return "supplementary"
    if slot in {12, 36, 44, 45}:
        return "representative-member"
    if "See also" in body:
        return "see-also"
    return "supporting"


def manual_author(slot: int, index: int) -> str | None:
    values = {
        (1, 1): "Synopsys", (3, 1): "Cadence Design Systems", (12, 0): "Synopsys", (12, 1): "Synopsys",
        (26, 1): "Jesse Allen", (31, 1): "Jesse Allen", (37, 1): "Jesse Allen",
        (36, 0): "Google XLS", (36, 1): "Chisel", (36, 2): "SpinalHDL", (36, 3): "Amaranth HDL",
        (44, 0): "Fraunhofer IAF / IIS", (44, 1): "Fraunhofer IAF / IIS",
        (45, 0): "India Design Linked Incentive / ChipIN", (45, 1): "CaretEDA",
        (46, 1): "APRIL", (47, 1): "Caesarea Labs", (49, 1): "Synopsys",
        (156, 1): "OSCC Project", (162, 1): "QiMeng-IPRC", (181, 1): "SNU MLLab",
    }
    return values.get((slot, index))


def manual_title(slot: int, index: int) -> str | None:
    values = {
        (156, 1): "AiEDA software repository",
        (162, 1): "QiMeng-Xpiler implementation repository",
        (181, 1): "Rule2DRC implementation repository",
    }
    return values.get((slot, index))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    register_data = read_json(args.register.resolve())
    headers = register_data["reference_register_rows"][0]
    rows = register_data["reference_register_rows"][1:]
    assert len(rows) == 212
    register = {int(row[0]): dict(zip(headers, row + [None] * (len(headers) - len(row)))) for row in rows}
    entries = markdown_entries((root / "references.md").read_text(encoding="utf-8"))

    slots: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    all_dois: set[str] = set()
    for number, body in entries:
        row = register[number]
        slot = {
            "id": f"ref-{number:03d}", "number": number,
            "published_markdown": body, "published_text": markdown_plain(body),
            "source_type": row.get("Source type"), "script": row.get("Script"),
            "metadata_authority": row.get("Metadata authority"), "confidence": row.get("Confidence"),
            "disposition": row.get("Disposition"), "adjudication_notes": row.get("Notes"),
        }
        slots.append(slot)
        links = parse_links(body)
        by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
        order: list[str] = []
        for link in links:
            url = normalize_url(link["url"])
            if url not in by_url:
                order.append(url)
            by_url[url].append(link | {"url": url})
        groups: list[list[str]] = [order] if number in MERGED_LOCATOR_SLOTS else [[url] for url in order]
        assert groups
        for index, urls in enumerate(groups):
            relevant = [link for url in urls for link in by_url[url]]
            cited_title = best_label(relevant)
            title = manual_title(number, index) or cited_title
            role = role_for(number, index, body)
            primary_link = max(relevant, key=lambda link: title_score(link["label"]))
            prefix = clean_author_prefix(body[:primary_link["start"]]) if index == 0 else None
            author_override = manual_author(number, index)
            authors = [{"literal": author_override}] if author_override else literal_authors(prefix, row.get("Source type") or "")
            dois = [doi_from_url(url) for url in urls if doi_from_url(url)]
            doi = dois[0] if dois else None
            if doi:
                all_dois.add(doi)
            occurrence = {
                "slot": number, "slot_id": slot["id"], "index": index, "role": role,
                "urls": urls, "doi": doi, "published_title": cited_title, "normalized_title": title,
                "title_translated": row.get("English translation") if index == 0 else None,
                "original_title": row.get("Original-language title") if index == 0 else None,
                "authors": authors, "issued": date_parts(markdown_plain(body)),
                "accessed": date_parts(
                    " ".join(filter(None, [row.get("Current entry"), row.get("Proposed exact form")])),
                    accessed=True,
                ),
                "source_type": row.get("Source type") or "web/other", "script": row.get("Script"),
                "metadata_authority": row.get("Metadata authority"), "published_context": markdown_plain(body),
                "title_authority": "official repository identity" if manual_title(number, index) else "published reference and verified register",
            }
            occurrences.append(occurrence)

    cache_path = args.cache.resolve() if args.cache else root.parent / "bibliography_authority_cache.json"
    cache = authority_cache(sorted(all_dois), cache_path)
    identity_to_work: dict[str, dict[str, Any]] = {}
    works: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    suffix_count: Counter[int] = Counter()
    exceptions: list[dict[str, Any]] = []

    for occurrence in occurrences:
        identity = f"doi:{occurrence['doi']}" if occurrence["doi"] else "url:" + occurrence["urls"][0]
        existing = identity_to_work.get(identity)
        is_new = existing is None
        if is_new:
            number = occurrence["slot"]
            suffix_count[number] += 1
            suffix = "" if suffix_count[number] == 1 else chr(ord("a") + suffix_count[number] - 1)
            citekey = f"f128_ref_{number:03d}{suffix}"
            title = occurrence["original_title"] or occurrence["normalized_title"]
            work = {
                "id": "work-" + citekey.replace("_", "-"), "citekey": citekey,
                "type": inferred_type(occurrence["source_type"], occurrence["published_context"], occurrence["urls"][0]),
                "title": title.strip(" .,"), "published_title": occurrence["published_title"].strip(" .,"),
                "title_translated": occurrence["title_translated"], "authors": occurrence["authors"],
                "published_authors": occurrence["authors"],
                "issued": {"date_parts": occurrence["issued"]} if occurrence["issued"] else {},
                "accessed": {"date_parts": occurrence["accessed"]} if occurrence["accessed"] else {},
                "container_title": None, "volume": None, "issue": None, "pages": None,
                "publisher": None, "institution": None, "edition": None,
                "language": detect_language(title, occurrence["script"]), "script": occurrence["script"],
                "doi": occurrence["doi"], "isbn": None,
                "url": f"https://doi.org/{occurrence['doi']}" if occurrence["doi"] else occurrence["urls"][0],
                "alternate_urls": [url for url in occurrence["urls"] if url != (f"https://doi.org/{occurrence['doi']}" if occurrence["doi"] else occurrence["urls"][0])],
                "reference_slots": [occurrence["slot_id"]],
                "published_context": occurrence["published_context"],
                "published_contexts": [{"reference_slot_id": occurrence["slot_id"], "text": occurrence["published_context"]}],
                "field_provenance": {
                    "title": [{"source": occurrence["slot_id"], "authority": occurrence["title_authority"], "status": "verified"}],
                    "url": [{"source": occurrence["slot_id"], "authority": occurrence["metadata_authority"], "status": "verified"}],
                },
            }
            identity_to_work[identity] = work
            works.append(work)
        else:
            work = existing
            if occurrence["slot_id"] not in work["reference_slots"]:
                work["reference_slots"].append(occurrence["slot_id"])
                work["published_contexts"].append({"reference_slot_id": occurrence["slot_id"], "text": occurrence["published_context"]})
            for url in occurrence["urls"]:
                if url != work["url"] and url not in work["alternate_urls"]:
                    work["alternate_urls"].append(url)
            if occurrence["role"] == "primary" and work.get("first_role") not in (None, "primary"):
                work["published_title"] = occurrence["published_title"].strip(" .,")
                work["title"] = (occurrence["original_title"] or occurrence["normalized_title"]).strip(" .,")
                if occurrence["title_translated"]:
                    work["title_translated"] = occurrence["title_translated"]
                if occurrence["authors"]:
                    work["authors"] = occurrence["authors"]
                    work["published_authors"] = occurrence["authors"]
                if occurrence["issued"]:
                    work["issued"] = {"date_parts": occurrence["issued"]}
        if "first_role" not in work:
            work["first_role"] = occurrence["role"]
        links.append({
            "reference_slot_id": occurrence["slot_id"], "work_id": work["id"],
            "role": occurrence["role"], "order": occurrence["index"] + 1,
        })

    for work in works:
        if not work.get("doi"):
            continue
        result = cache.get(work["doi"], {"provider": "unavailable", "url": work["url"], "record": None})
        merge_authority(work, result, exceptions)

    arxiv_works: dict[str, dict[str, Any]] = {}
    for work in works:
        if work.get("doi"):
            continue
        match = re.search(r"arxiv\.org/abs/([^/?#]+)", work["url"], re.I)
        if match:
            arxiv_works[re.sub(r"v\d+$", "", match.group(1))] = work
    arxiv = fetch_arxiv(sorted(arxiv_works), root.parent / "bibliography_arxiv_cache.json")
    for identifier, work in arxiv_works.items():
        result = arxiv.get(identifier, {"provider": "unavailable", "url": work["url"], "record": None})
        if result["provider"] != "unavailable":
            work["arxiv"] = identifier
            merge_authority(work, result, exceptions)

    scholarly_hosts = {"proceedings.mlr.press", "www.usenix.org", "papers.neurips.cc"}
    scholarly = [
        work for work in works
        if not work.get("doi") and urllib.parse.urlparse(work["url"]).netloc in scholarly_hosts
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_landing_url, work["url"], "Official scholarly page"): work for work in scholarly}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result and result.get("record", {}).get("title"):
                merge_authority(futures[future], result, exceptions)

    for work in works:
        manual = MANUAL_WORK_FIELDS.get(work["citekey"], {})
        for field, value in manual.items():
            work[field] = value
            work["field_provenance"][field] = [{
                "source": work["reference_slots"][0],
                "authority": "published reference string",
                "status": "verified",
            }]

    for work in works:
        work.pop("first_role", None)
        if not work["authors"]:
            exceptions.append({"work_id": work["id"], "kind": "missing-authorship", "status": "unresolved"})
        if not work.get("issued", {}).get("date_parts"):
            exceptions.append({"work_id": work["id"], "kind": "missing-issued-date", "status": "unresolved"})
        work["reference_slots"].sort()
        work["alternate_urls"] = sorted(set(work["alternate_urls"]))

    completeness_fields = ["title", "authors", "issued", "accessed", "type", "url"]
    completeness = {}
    for field in completeness_fields:
        present = sum(bool(
            work.get(field) and (field not in {"issued", "accessed"} or work[field].get("date_parts"))
        ) for work in works)
        completeness[field] = {"present": present, "missing": len(works) - present}

    dataset = {
        "schema_version": "1.0",
        "purpose": "Reusable structured representation of every bibliographic object already present in the published numbered references.",
        "canonical_publication": {"doi": "10.5281/zenodo.23256253", "url": "https://doi.org/10.5281/zenodo.23256253"},
        "authority": {
            "published_reference_strings": "Final PDF and final source document",
            "crosswalk": "F128 master reference register and citation crosswalk",
            "structured_metadata": "Responsible DOI registry or official source surface; unresolved values remain absent",
        },
        "generated_on": dt.date.today().isoformat(),
        "counts": {
            "reference_slots": len(slots), "works": len(works), "reference_work_links": len(links),
            "citation_clusters": 270, "citation_edges": 731,
        },
        "field_completeness": completeness,
        "reference_slots": slots, "works": works, "reference_work_links": links,
        "adjudications": [dict(zip(register_data["adjudication_rows"][0], row)) for row in register_data["adjudication_rows"][1:]],
        "exceptions": exceptions,
    }
    (root / "bibliography.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "reference_slots": len(slots), "works": len(works), "links": len(links),
        "doi_records": len(all_dois), "authority_unavailable": sum(cache[doi]["provider"] == "unavailable" for doi in all_dois),
        "exceptions": len(exceptions), "field_completeness": completeness,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
