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
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                raise  # not found: retrying won't help
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


# Stricter patterns for author affiliations: only names that point to one facility.
# (e.g. "SLAC" alone could mean LCLS or SSRL, "RIKEN SPring-8 Center" could mean SACLA or SPring-8.)
AFF_FACILITIES = {
    "LCLS": r"Linac Coherent Light Source|\bLCLS\b",
    "SACLA": r"\bSACLA\b",
    "European XFEL": r"European XFEL",
    "SwissFEL": r"SwissFEL",
    "PAL-XFEL": r"PAL-XFEL|Pohang Accelerator Laboratory",
    "ESRF": r"European Synchrotron Radiation Facility|\bESRF\b",
    "APS / BioCARS": r"BioCARS|Advanced Photon Source",
    "Diamond": r"Diamond Light Source",
    "SLS": r"Swiss Light Source",
    "MAX IV": r"MAX ?IV Laboratory",
    "SOLEIL": r"Synchrotron SOLEIL",
    "ALS": r"Advanced Light Source",
    "NSLS-II": r"National Synchrotron Light Source",
    "SSRL": r"Stanford Synchrotron Radiation",
    "Australian Synchrotron": r"Australian Synchrotron",
    "CLS": r"Canadian Light Source",
}
AFF_RE = {k: re.compile(v, re.I) for k, v in AFF_FACILITIES.items()}


def clean_aff(aff):
    """Full affiliation without e-mail addresses (no e-mails are published on the site)."""
    aff = re.sub(r"(Electronic address|E-?mail|Email)\s*:?.*$", "", aff or "", flags=re.I)
    aff = re.sub(r"\S+@\S+", "", aff)
    return re.sub(r"\s+", " ", aff).strip(" .,;")


def corresponding(people):
    """people: list of (name, [raw affiliations]). Corresponding authors are those whose affiliation
    carries an e-mail address (PubMed's convention). Falls back to the last author."""
    marked = [(n, a) for n, a in people if any("@" in x or "electronic address" in x.lower() for x in a)]
    inferred = not marked
    if inferred:
        marked = [p for p in people[-1:] if p[1]]
    out = []
    for n, affs in marked:
        cleaned = list(dict.fromkeys(c for c in (clean_aff(x) for x in affs) if c))
        if cleaned:
            out.append({"name": n, "affiliations": cleaned})
    return out, inferred


def facilities(text, affiliations=""):
    """Facilities named in the title/abstract, plus facilities that co-authors are affiliated with."""
    found = {k: "abstract" for k, rx in FAC_RE.items() if rx.search(text or "")}
    for k, rx in AFF_RE.items():
        if k not in found and rx.search(affiliations or ""):
            found[k] = "affiliation"
    return [{"name": k, "via": v} for k, v in sorted(found.items())]


# ---------------------------------------------------------------- Relevance
# Papers are shown on the site only when they look like biosciences / protein dynamics.
# Everything else goes to the review list (review page) so nothing is lost.
BIO_TERMS = [
    r"protein", r"enzym", r"receptor", r"rhodopsin", r"\bopsin", r"photosystem", r"photosynth",
    r"phytochrome", r"cryptochrome", r"photoreceptor", r"chromophore", r"fluorescent protein", r"\bGFP\b",
    r"\bLOV\b", r"photoactive yellow", r"\bPYP\b", r"myoglobin", r"h(a)?emoglobin", r"cytochrome",
    r"oxidase", r"reductase", r"hydrogenase", r"nitrogenase", r"lysozyme", r"thaumatin", r"insulin",
    r"kinase", r"protease", r"lactamase", r"photolyase", r"isomerase", r"synthase", r"dehydrogenase",
    r"active site", r"\bresidues?\b", r"amino acid", r"peptide", r"biomolecul", r"macromolecul",
    r"ribosom", r"\bDNA\b", r"\bRNA\b", r"nucleic", r"\bvir(us|al)\b", r"bacteri", r"membrane protein",
    r"transporter", r"ion channel", r"antibod", r"substrate", r"catalytic", r"cofactor", r"\bh(a)?em(e)?\b",
    r"flavin", r"\bFAD\b", r"\bFMN\b", r"retinal", r"allosteri", r"structural biology", r"inhibitor",
    r"metalloprotein", r"\bPSII\b", r"oxygen[- ]evolving", r"Mn4CaO5", r"\bactin\b", r"myosin",
    r"biolog", r"\bcells?\b", r"drug",
]
# Hard non-bio topics: these hold a paper back unless it also has clear bioscience terms.
OTHER_TERMS = [
    r"perovskite", r"nanoparticle", r"\bmaterials?\b", r"\balloys?\b", r"metal oxide", r"semiconductor",
    r"\bplasmas?\b", r"warm dense", r"shock[- ]?(wave|compress|ed)", r"magneti[cz]",
    r"\bspin\b", r"ferroelectric", r"superconduct", r"thin films?", r"graphene", r"phonon",
    r"attosecond", r"gas[- ]phase", r"photoioni[sz]ation", r"\bionization\b", r"metal-organic framework",
    r"\bMOFs?\b", r"zeolite", r"batter(y|ies)", r"electrode", r"photovoltaic", r"solar cell",
    r"spin[- ]crossover", r"transition[- ]metal complex", r"coordination complex", r"heterogeneous catalys",
    r"liquid metal", r"high[- ]pressure", r"diamond anvil", r"\bmelting\b",
]
# Machine and instrument topics: only hold a paper back when nothing points to (macro)molecular crystallography.
INSTRUMENT_TERMS = [
    r"accelerator", r"undulator", r"electron (beam|bunch)", r"\blasing\b", r"\bSASE\b", r"self-seed",
    r"beamline design", r"detector", r"commissioning", r"upgrade", r"X-ray optics", r"laser[- ]driven",
]
# Methods for (macro)molecular crystallography and imaging: these papers belong on the site
# even without a named protein (sample delivery, data quality, radiation damage, SPI...).
MX_TERMS = [
    r"serial (femtosecond |synchrotron |millisecond )?crystallograph", r"\bS[FS]X\b", r"\bTR-S[FS]X\b",
    r"macromolecular crystallograph", r"protein crystallograph", r"time-resolved (X-ray )?crystallograph",
    r"microcrystals?", r"fixed[- ]targets?", r"sample delivery", r"radiation damage", r"droplet[- ]on[- ]demand",
    r"\bLaue\b", r"single[- ]particle imaging", r"\bSPI\b", r"diffraction before destruction",
    r"mix(-and-|ing )inject", r"pump[- ]probe crystallograph",
]

BIO_RE = [re.compile(t, re.I) for t in BIO_TERMS]
OTHER_RE = [re.compile(t, re.I) for t in OTHER_TERMS]
INSTR_RE = [re.compile(t, re.I) for t in INSTRUMENT_TERMS]
MX_RE = [re.compile(t, re.I) for t in MX_TERMS]


def hits(regexes, text):
    return sorted({m.group(0).lower() for rx in regexes if (m := rx.search(text))})


def relevance(text):
    bio, other = hits(BIO_RE, text), hits(OTHER_RE, text)
    instr, mx = hits(INSTR_RE, text), hits(MX_RE, text)
    keep = (
        (bio and not other)                                   # bioscience, no materials/physics topic
        or (len(bio) >= 2 and len(bio) > len(other))          # mixed, but bioscience dominates
        or (mx and not other)                                 # crystallography/imaging method paper
    )
    # Instrument terms alone never keep a paper; they only matter when nothing else applies.
    return bool(keep), bio + mx, other + instr


SERVER_NAMES = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv", "res sq": "Research Square",
                "arxiv": "arXiv", "chemrxiv": "ChemRxiv"}

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
        authors, affs, people = [], [], []
        for au in a.findall("AuthorList/Author"):
            if au.findtext("CollectiveName"):
                authors.append(au.findtext("CollectiveName"))
                affs.append("")
                continue
            last = au.findtext("LastName") or ""
            ini = au.findtext("Initials") or ""
            if last:
                name = f"{last} {ini}".strip()
                raw = [x.text or "" for x in au.findall("AffiliationInfo/Affiliation") if x.text]
                authors.append(name)
                affs.append(" | ".join(raw))
                people.append((name, raw))
        aff = next((x.split(" | ")[0] for x in reversed(affs) if x), "")
        ptypes = [t.text for t in a.findall("PublicationTypeList/PublicationType") if t.text]
        mesh = [m.text for m in mc.findall("MeshHeadingList/MeshHeading/DescriptorName") if m.text]
        mesh += [k.text for k in mc.findall("KeywordList/Keyword") if k.text]
        out.append({
            "pmid": pmid, "doi": norm_doi(doi), "title": title, "abstract": abstract,
            "journal": journal, "date": date, "authors": authors,
            "institution": short_inst(aff), "pubtypes": ptypes,
            "affiliations": " | ".join(sorted(set(x for x in affs if x))), "mesh": mesh,
            "corresponding": corresponding(people),
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
        authors, aff, all_affs, people = [], "", set(), []
        for au in (r.get("authorList") or {}).get("author", []):
            name = f"{au.get('lastName', '')} {au.get('initials', '')}".strip() or au.get("fullName") or au.get("collectiveName", "")
            details = (au.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", [])
            if name:
                authors.append(name)
                people.append((name, [d["affiliation"] for d in details if d.get("affiliation")]))
            all_affs.update(d.get("affiliation", "") for d in details if d.get("affiliation"))
            if details and details[0].get("affiliation"):
                aff = details[0]["affiliation"]  # ends up as the last author's
        abstract = re.sub(r"<[^>]+>", "", r.get("abstractText") or "")
        out.append({
            "doi": doi, "title": re.sub(r"<[^>]+>", "", r.get("title") or "").strip(" ."),
            "abstract": abstract, "date": r.get("firstPublicationDate") or "",
            "authors": authors, "institution": short_inst(aff),
            "server": (r.get("bookOrReportDetails") or {}).get("publisher") or "bioRxiv",
            "affiliations": " | ".join(sorted(all_affs)),
            "corresponding": corresponding(people),
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
        "corresponding": ([{"name": c.get("author_corresponding", ""),
                            "affiliations": [clean_aff(c.get("author_corresponding_institution", ""))]}], False),
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

    # PubMed also indexes preprints (bioRxiv, Research Square, arXiv...). Treat those as preprints.
    def is_preprint(a):
        return ("Preprint" in a["pubtypes"] or a["doi"].startswith("10.1101/")
                or a["journal"].lower() in SERVER_NAMES)
    pm_pre = [a for a in articles if is_preprint(a)]
    articles = [a for a in articles if not is_preprint(a)]
    log(f"PubMed: {len(articles)} journal articles, {len(pm_pre)} preprints")
    epmc = {p["doi"]: p for p in epmc_preprints()}
    preprints = []
    for a in pm_pre:
        epmc.pop(a["doi"], None)  # same preprint found twice: keep the PubMed record
        preprints.append({
            "doi": a["doi"], "pmid": a["pmid"], "title": a["title"], "abstract": a["abstract"],
            "date": a["date"], "authors": a["authors"], "institution": a["institution"],
            "server": SERVER_NAMES.get(a["journal"].lower(), a["journal"]),
            "affiliations": a["affiliations"], "corresponding": a["corresponding"], "mesh": a["mesh"],
        })
    preprints += list(epmc.values())
    known = {p["doi"] for p in preprints}
    for doi in sorted(d for d in inc_dois if d.startswith("10.1101/") and d not in known):
        rec = biorxiv_record(doi)
        if rec:
            preprints.append(rec)
    log(f"Preprints: {len(preprints)}; looking up published versions")
    for i, p in enumerate(preprints, 1):
        if "published" not in p:
            p["published"] = biorxiv_published(p["doi"]) if p["doi"].startswith("10.1101/") else ""
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
            target["preprint"] = {"doi": p["doi"], "date": p["date"], "server": p["server"], "pmid": p.get("pmid", "")}
            merged += 1
        else:
            entries.append({
                "type": "preprint", "pmid": p.get("pmid", ""), "doi": p["doi"], "title": p["title"],
                "abstract": p["abstract"], "journal": p["server"], "date": p["date"],
                "authors": p["authors"], "institution": p["institution"],
                "published_doi": p["published"], "preprint": None,
                "affiliations": p.get("affiliations", ""), "mesh": p.get("mesh", []),
                "corresponding": p.get("corresponding", ([], True)),
            })
    log(f"Merged {merged} preprints with their published version")
    for a in articles:
        entries.append({
            "type": "article", "pmid": a["pmid"], "doi": a["doi"], "title": a["title"],
            "abstract": a["abstract"], "journal": a["journal"], "date": a["date"],
            "authors": a["authors"], "institution": a["institution"], "published_doi": "",
            "preprint": a["preprint"], "pubtypes": a["pubtypes"],
            "affiliations": a["affiliations"], "mesh": a["mesh"],
            "corresponding": a["corresponding"],
        })

    def excluded(e):
        keys = {e["pmid"], e["doi"]} | ({e["preprint"]["doi"]} if e["preprint"] else set())
        return bool({k.lower() for k in keys if k} & exclude)

    before = len(entries)
    entries = [e for e in entries if not excluded(e)]
    log(f"Excluded {before - len(entries)} entries via curation/exclude.txt")

    for e in entries:
        e["facilities"] = facilities(f"{e['title']} {e['abstract']}", e.pop("affiliations", ""))
        e["curated"] = bool({(e["pmid"] or "").lower(), (e["doi"] or "").lower()} & include)
        keep, bio, other = relevance(f"{e['title']} {e['abstract']} {' '.join(e.pop('mesh', []))}")
        e["status"] = "shown" if (keep or e["curated"]) else "review"
        if e["curated"] and not keep:
            log(f"  Curated paper the rules would hold back: {e['pmid'] or e['doi']}  bio={bio} other={other}")
        e["bio_terms"], e["other_terms"] = bio, other
        people, inferred = e.pop("corresponding")
        e["corresponding"] = {"people": people, "inferred": inferred}
    entries.sort(key=lambda e: e["date"] or "", reverse=True)
    n_review = sum(e["status"] == "review" for e in entries)
    log(f"Relevance: {len(entries) - n_review} shown, {n_review} sent to review")
    fac = sum(bool(e["facilities"]) for e in entries)
    log(f"Facility found for {fac}/{len(entries)} papers")

    json.dump({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "pubmed_query": term,
        "count": len(entries),
        "papers": entries,
    }, sys.stdout, ensure_ascii=False)
    log(f"Done: {len(entries)} papers")


if __name__ == "__main__":
    main()
