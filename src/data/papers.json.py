"""Data loader for the Papers page.

Runs at build time (nightly on GitHub Actions). Fetches:
  * journal articles from PubMed (NCBI E-utilities)
  * bioRxiv preprints via Europe PMC search, with the published version looked up via the bioRxiv API
Merges each preprint with its published version, applies the manual curation lists in /curation,
and prints one JSON document to stdout. Progress messages go to stderr (visible in the build log).
Uses only the Python standard library.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CURATION = ROOT / "curation"
START_YEAR = 2009
NCBI_KEY = os.environ.get("NCBI_API_KEY", "").strip()
TOOL = "structural-dynamics-watch"
EMAIL = "strucdynwatch@gmail.com"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BIORXIV = "https://api.biorxiv.org/details/biorxiv"

# Search terms (title/abstract). Edit here to widen or narrow the feed.
TERMS = [
    "time-resolved crystallography",
    "time-resolved serial",
    "time-resolved X-ray crystallography",
    "TR-SFX",
    "TR-SSX",
    "serial femtosecond crystallography",
    "serial synchrotron crystallography",
    "time-resolved Laue",
    "pump-probe crystallography",
    "mix-and-inject",
    "temperature-jump crystallography",
    "molecular movie",
    "XFEL",
    "X-ray free-electron laser",
]

# Facility names searched for in title + abstract. Canonical name -> regex.
FACILITIES = {
    "LCLS": r"\bLCLS(-II)?\b|Linac Coherent Light Source",
    "SACLA": r"\bSACLA\b",
    "European XFEL": r"European XFEL|\bEuXFEL\b",
    "SwissFEL": r"\bSwissFEL\b",
    "PAL-XFEL": r"\bPAL-XFEL\b|Pohang Accelerator",
    "SHINE": r"\bSHINE\b(?= ?(XFEL|facility|free))",
    "FLASH": r"\bFLASH\b(?= ?(free|FEL|facility))",
    "ESRF": r"\bESRF(-EBS)?\b|European Synchrotron Radiation Facility",
    "APS / BioCARS": r"\bBioCARS\b|Advanced Photon Source|\bAPS\b(?= ?(beamline|synchrotron|-U))",
    "Diamond": r"Diamond Light Source|\bDiamond\b(?= ?(beamline|I24|synchrotron))",
    "SLS": r"Swiss Light Source|\bSLS\b",
    "PETRA III": r"PETRA[ -]?III|\bP14\b(?=.*PETRA)|T-REXX",
    "MAX IV": r"MAX ?IV",
    "SOLEIL": r"\bSOLEIL\b",
    "ALS": r"Advanced Light Source",
    "NSLS-II": r"NSLS-?II",
    "SSRL": r"\bSSRL\b|Stanford Synchrotron",
    "SPring-8": r"SPring-8",
    "SSRF": r"\bSSRF\b|Shanghai Synchrotron",
    "Photon Factory": r"Photon Factory",
    "BESSY II": r"BESSY",
    "Australian Synchrotron": r"Australian Synchrotron",
    "CHESS": r"\bCHESS\b|Cornell High Energy",
    "CLS": r"Canadian Light Source",
}
FAC_RE = {k: re.compile(v, re.I if k in ("Diamond", "ALS", "Australian Synchrotron", "Photon Factory") else 0)
          for k, v in FACILITIES.items()}

INST_WORDS = re.compile(
    r"Universit|Institut|Laborator|Center|Centre|College|School|Hospital|Academy|Facility|"
    r"XFEL|Synchrotron|ETH|EPFL|CNRS|INSERM|CEA|Max Planck|Max-Planck|DESY|SLAC|RIKEN|"
    r"Helmholtz|Paul Scherrer|Diamond|ESRF|Argonne|Brookhaven|Berkeley|Los Alamos|Lund|"
    r"Karolinska|Weizmann|Scripps|Rockefeller|Janelia|MRC|EMBL|Foundation|Inc\b|Ltd\b|GmbH",
    re.I,
)


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def http(url, params=None, data=None, tries=4, pause=0.0):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": f"{TOOL} ({EMAIL})"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out = r.read()
            if pause:
                time.sleep(pause)
            return out
        except (urllib.error.URLError, TimeoutError) as e:
            wait = 2 ** attempt
            log(f"  request failed ({e}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url[:120]}")


NCBI_PAUSE = 0.12 if NCBI_KEY else 0.4


def ncbi_params(**kw):
    p = {"tool": TOOL, "email": EMAIL, **kw}
    if NCBI_KEY:
        p["api_key"] = NCBI_KEY
    return p


def read_list(name):
    path = CURATION / name
    items = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                items.add(line.lower())
    return items


def norm_doi(doi):
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:120]


def short_inst(aff):
    if not aff:
        return ""
    aff = re.sub(r"\S+@\S+", "", aff)
    aff = re.sub(r"(Electronic address:|E-mail:).*", "", aff, flags=re.I)
    parts = [p.strip(" .;") for p in re.split(r"[,;]", aff) if p.strip(" .;")]
    for p in parts:
        if INST_WORDS.search(p) and not re.match(r"(Department|Dept|Division|Section|Unit|Group|Laboratory of|Lab of)\b", p, re.I):
            return p[:90]
    for p in parts:
        if INST_WORDS.search(p):
            return p[:90]
    return parts[0][:90] if parts else ""


def facilities(text):
    return sorted(k for k, rx in FAC_RE.items() if rx.search(text or ""))


# ---------------------------------------------------------------- PubMed

def pubmed_query():
    terms = " OR ".join(f'"{t}"[tiab]' for t in TERMS)
    return f"({terms}) AND {START_YEAR}:3000[dp]"


def pubmed_search(term, retmax=9999):
    raw = http(f"{EUTILS}/esearch.fcgi", ncbi_params(db="pubmed", term=term, retmax=retmax, retmode="json"),
               pause=NCBI_PAUSE)
    res = json.loads(raw)["esearchresult"]
    return int(res.get("count", 0)), res.get("idlist", [])


def text_of(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def ymd(el):
    if el is None:
        return ""
    y = el.findtext("Year")
    if not y:
        md = el.findtext("MedlineDate") or ""
        m = re.search(r"(19|20)\d\d", md)
        return f"{m.group(0)}-01-01" if m else ""
    mo = el.findtext("Month") or "1"
    mo = MONTHS.get(mo[:3].lower(), None) or (int(mo) if mo.isdigit() else 1)
    d = el.findtext("Day") or "1"
    d = int(d) if d.isdigit() else 1
    return f"{int(y):04d}-{mo:02d}-{d:02d}"


def parse_pubmed(xml_bytes):
    out = []
    root = ET.fromstring(xml_bytes)
    for art in root.findall("PubmedArticle"):
        mc = art.find("MedlineCitation")
        a = mc.find("Article")
        pmid = mc.findtext("PMID")
        title = text_of(a.find("ArticleTitle"))
        parts = []
        for ab in a.findall("Abstract/AbstractText"):
            label = ab.get("Label")
            txt = text_of(ab)
            parts.append(f"{label}: {txt}" if label and label.upper() != "UNLABELLED" else txt)
        abstract = " ".join(parts)
        journal = a.findtext("Journal/ISOAbbreviation") or a.findtext("Journal/Title") or ""
        date = ymd(a.find("ArticleDate")) or ""
        if not date:
            for h in art.findall("PubmedData/History/PubMedPubDate"):
                if h.get("PubStatus") in ("pubmed", "entrez"):
                    date = ymd(h)
                    break
        if not date:
            date = ymd(a.find("Journal/JournalIssue/PubDate"))
        doi = ""
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = aid.text or ""
        if not doi:
            for e in a.findall("ELocationID"):
                if e.get("EIdType") == "doi":
                    doi = e.text or ""
        authors, affs = [], []
        for au in a.findall("AuthorList/Author"):
            if au.findtext("CollectiveName"):
                authors.append(au.findtext("CollectiveName"))
                affs.append("")
                continue
            last = au.findtext("LastName") or ""
            ini = au.findtext("Initials") or ""
            if last:
                authors.append(f"{last} {ini}".strip())
                affs.append(au.findtext("AffiliationInfo/Affiliation") or "")
        aff = next((x for x in reversed(affs) if x), "")
        ptypes = [t.text for t in a.findall("PublicationTypeList/PublicationType") if t.text]
        out.append({
            "pmid": pmid, "doi": norm_doi(doi), "title": title, "abstract": abstract,
            "journal": journal, "date": date, "authors": authors,
            "institution": short_inst(aff), "pubtypes": ptypes,
        })
    return out


def pubmed_fetch(pmids):
    records = []
    pmids = list(pmids)
    for i in range(0, len(pmids), 200):
        chunk = pmids[i:i + 200]
        raw = http(f"{EUTILS}/efetch.fcgi", data=ncbi_params(db="pubmed", id=",".join(chunk), retmode="xml"),
                   pause=NCBI_PAUSE)
        records += parse_pubmed(raw)
        log(f"  PubMed: fetched {len(records)}/{len(pmids)}")
    return records


# ---------------------------------------------------------------- Preprints

def epmc_preprints():
    q = " OR ".join(f'TITLE:"{t}" OR ABSTRACT:"{t}"' for t in TERMS)
    query = f"(SRC:PPR) AND ({q}) AND (FIRST_PDATE:[{START_YEAR}-01-01 TO 2100-12-31])"
    cursor, results = "*", []
    while True:
        raw = http(EPMC, {"query": query, "format": "json", "resultType": "core", "pageSize": 1000,
                          "cursorMark": cursor}, pause=0.2)
        js = json.loads(raw)
        batch = js.get("resultList", {}).get("result", [])
        results += batch
        nxt = js.get("nextCursorMark")
        if not batch or not nxt or nxt == cursor:
            break
        cursor = nxt
    log(f"  Europe PMC: {len(results)} preprint hits (all servers); total reported {js.get('hitCount')}")
    out = []
    for r in results:
        doi = norm_doi(r.get("doi"))
        if not doi.startswith("10.1101/"):
            continue  # keep bioRxiv (and medRxiv, same DOI prefix)
        authors, aff = [], ""
        for au in (r.get("authorList") or {}).get("author", []):
            name = f"{au.get('lastName', '')} {au.get('initials', '')}".strip() or au.get("fullName") or au.get("collectiveName", "")
            if name:
                authors.append(name)
            details = (au.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", [])
            if details and details[0].get("affiliation"):
                aff = details[0]["affiliation"]  # ends up as the last author's
        abstract = re.sub(r"<[^>]+>", "", r.get("abstractText") or "")
        out.append({
            "doi": doi, "title": re.sub(r"<[^>]+>", "", r.get("title") or "").strip(" ."),
            "abstract": abstract, "date": r.get("firstPublicationDate") or "",
            "authors": authors, "institution": short_inst(aff),
            "server": (r.get("bookOrReportDetails") or {}).get("publisher") or "bioRxiv",
        })
    return out


def biorxiv_published(doi):
    try:
        js = json.loads(http(f"{BIORXIV}/{doi}/na/json", pause=0.15, tries=2))
        coll = js.get("collection") or []
        if coll:
            pub = coll[-1].get("published") or ""
            return "" if pub.upper() == "NA" else norm_doi(pub)
    except Exception as e:  # noqa: BLE001
        log(f"  bioRxiv lookup failed for {doi}: {e}")
    return ""


def biorxiv_record(doi):
    """A preprint by DOI straight from the bioRxiv API (for the include list)."""
    js = json.loads(http(f"{BIORXIV}/{doi}/na/json", pause=0.15, tries=2))
    coll = js.get("collection") or []
    if not coll:
        return None
    c = coll[-1]
    authors = [a.strip() for a in (c.get("authors") or "").split(";") if a.strip()]
    return {
        "doi": norm_doi(doi), "title": c.get("title", ""), "abstract": c.get("abstract", ""),
        "date": coll[0].get("date", ""), "authors": authors,
        "institution": short_inst(c.get("author_corresponding_institution", "")), "server": "bioRxiv",
        "published": "" if (c.get("published") or "NA").upper() == "NA" else norm_doi(c.get("published")),
    }


# ---------------------------------------------------------------- Build

def main():
    exclude = read_list("exclude.txt")
    include = read_list("include.txt")
    log(f"Curation: {len(include)} to include, {len(exclude)} to exclude")

    term = pubmed_query()
    count, pmids = pubmed_search(term)
    log(f"PubMed search: {count} hits")
    if count > len(pmids):
        log(f"  WARNING: only the first {len(pmids)} PubMed hits were retrieved")

    inc_pmids = {x for x in include if x.isdigit()}
    inc_dois = {x for x in include if not x.isdigit()}
    for doi in sorted(inc_dois):
        if doi.startswith("10.1101/"):
            continue
        _, ids = pubmed_search(f"{doi}[doi]", retmax=5)
        inc_pmids.update(ids)
    articles = pubmed_fetch(sorted(set(pmids) | inc_pmids, key=int))

    preprints = epmc_preprints()
    known = {p["doi"] for p in preprints}
    for doi in sorted(d for d in inc_dois if d.startswith("10.1101/") and d not in known):
        rec = biorxiv_record(doi)
        if rec:
            preprints.append(rec)
    log(f"Preprints: {len(preprints)}; looking up published versions")
    for i, p in enumerate(preprints, 1):
        if "published" not in p:
            p["published"] = biorxiv_published(p["doi"])
        if i % 50 == 0:
            log(f"  {i}/{len(preprints)}")

    by_doi = {a["doi"]: a for a in articles if a["doi"]}
    by_title = {norm_title(a["title"]): a for a in articles}
    entries = []
    for a in articles:
        a["preprint"] = None
    merged = 0
    for p in preprints:
        target = by_doi.get(p["published"]) if p["published"] else None
        if target is None:
            target = by_title.get(norm_title(p["title"]))
        if target is not None and target["preprint"] is None:
            target["preprint"] = {"doi": p["doi"], "date": p["date"], "server": p["server"]}
            merged += 1
        else:
            entries.append({
                "type": "preprint", "pmid": "", "doi": p["doi"], "title": p["title"],
                "abstract": p["abstract"], "journal": p["server"], "date": p["date"],
                "authors": p["authors"], "institution": p["institution"],
                "published_doi": p["published"], "preprint": None,
            })
    log(f"Merged {merged} preprints with their published version")
    for a in articles:
        entries.append({
            "type": "article", "pmid": a["pmid"], "doi": a["doi"], "title": a["title"],
            "abstract": a["abstract"], "journal": a["journal"], "date": a["date"],
            "authors": a["authors"], "institution": a["institution"], "published_doi": "",
            "preprint": a["preprint"], "pubtypes": a["pubtypes"],
        })

    def excluded(e):
        keys = {e["pmid"], e["doi"]} | ({e["preprint"]["doi"]} if e["preprint"] else set())
        return bool({k.lower() for k in keys if k} & exclude)

    before = len(entries)
    entries = [e for e in entries if not excluded(e)]
    log(f"Excluded {before - len(entries)} entries via curation/exclude.txt")

    for e in entries:
        e["facilities"] = facilities(f"{e['title']} {e['abstract']}")
        e["curated"] = bool({(e["pmid"] or "").lower(), (e["doi"] or "").lower()} & include)
    entries.sort(key=lambda e: e["date"] or "", reverse=True)

    json.dump({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "pubmed_query": term,
        "count": len(entries),
        "papers": entries,
    }, sys.stdout, ensure_ascii=False)
    log(f"Done: {len(entries)} papers")


if __name__ == "__main__":
    main()
