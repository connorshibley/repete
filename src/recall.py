"""Query the research record without forming an opinion about it.

Three layers hold this project's history and until now nothing joined them:
`knowledge/backtest_candidates.md` (6,193 lines of prose, read by grep),
`research/registrations.jsonl` (55 frozen specs, each carrying a `prior` and
`failure_modes` written before the run) and `research/verdicts.jsonl` (59 rows
of per-arm metrics and per-clause pass/fail). `register_gate.py` opens the
registrations only to ask whether a verdict exists; `run_gate.py` only appends.
No code in this repo has ever read a past result.

THE ONE RULE
------------
`scripts/gen_research_index.py` states the constraint this module must not
violate: *"an index that formed its own opinion of the record would be a second
source of truth."* So every string this module emits is one of

  (a) a byte-exact span from a named source file,
  (b) an identifier, count, path, ratio or ISO date,
  (c) a fixed template from `LABELS`.

There is no fourth category. Nothing is composed, paraphrased, ellipsed,
case-normalised or dash-normalised. `tests/test_recall_quotes.py` walks the
JSON output and fails on any string that is none of the three — the rule is
enforced, not promised, because a promise in a docstring is the shape of thing
this repo has twice found rotting (§23's monotonicity lesson sat in prose for
thirty-one sections before §55 made it a clause).

Search is deliberately dumb: BM25 over literal tokens, no stemming and no
synonyms. A stemmer is a small inference and it makes a hit unexplainable. The
cost is real and is printed on every search — an idea described in different
words will not be found, and false negatives are the expensive direction.

WHAT THE BODY HASH IS FOR
-------------------------
The record's entire evidentiary value is that it is append-only and never
retrofitted: a claim written before its result, in a file that only ever grows,
cannot be quietly reworded afterwards. Nothing in this repo enforced that.
`research/anchors.json` pins a sha256 per section, so a retroactive edit either
shows up in that file's diff or turns the suite red.
"""
import hashlib
import json
import math
import os
import re
import subprocess


import gatespec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RECORD = "knowledge/backtest_candidates.md"
DIVERGENCES = "docs/divergences.md"
SPECS_DIR = "research/specs"
REVIEWS_GLOB = "knowledge/source-review-"
ANCHORS = "research/anchors.json"
VERDICTS = "research/verdicts.jsonl"
PRIOR_READINGS = "research/prior_readings.jsonl"

# The first commit of docs/divergences.md. A divergence query for a date before
# this cannot be answered — s35, s37 and s38 all ran 2026-07-28, one day early —
# and the honest answer there is "no register existed", never today's table.
DIVERGENCES_FIRST_COMMIT = "2026-07-29"

# Sections numbered at or after this must carry a machine-readable trailer.
# Deliberately NOT back-filled into §1-§59: back-filling would edit the
# append-only record, which is the one thing it must not permit. The parser is
# fixed backwards, the trailer is required forwards, and nothing is retrofitted.
TRAILER_REQUIRED_FROM = 60

TRAILER = re.compile(r"^<!--\s*recall:\s*section=(\S+)\s+specs=(\S*)\s*-->\s*$",
                     re.M)

# §30 is the only §-numbered section that exists solely as an H3
# (`### §30 CANDIDATE — tighter down-regime gross cap. NOT ADOPTED.`). Every
# other `### §N` is a RESULT or ADDENDUM under an H2 of the same number. Listed
# explicitly rather than inferred so a future H3 section cannot be silently
# absorbed into its neighbour — the test names this set and fails if it grows.
H3_ONLY_SECTIONS = frozenset({"§30"})

DIRECTIONS = ("expected_pass", "expected_fail", "mixed", "no_expectation_stated")

# Fields whose string values are byte-exact spans of a named source file.
# Everything else in any output must be an identifier, a number, a path, a
# ratio, an ISO date, or a member of LABELS.
VERBATIM_FIELDS = frozenset({
    "heading", "excerpt", "title", "prior", "quote", "detail", "subheadings",
    "failure_modes", "open_line", "table",
})

# A locator is not a fourth category smuggled in — it is two or more verbatim
# spans joined by a fixed separator, and `tests/test_recall_quotes.py` splits
# on that separator and checks every part. Naming it explicitly is the point:
# `§58 › ### The result` is a place in the record, not a sentence about it, and
# the difference has to be mechanical rather than a matter of taste.
LOCATOR_FIELDS = frozenset({"anchor", "key", "section"})
LOCATOR_SEP = " › "

# Every fixed string this module may emit. Category (c), and the whole of it.
LABELS = frozenset({
    "LEXICAL_ONLY",
    "scripts/recall.py anchors",
    "ranking is lexical; an idea described in different words will not appear here",
    "no registration",
    "no verdict recorded",
    "no spec file",
    "id-only",
    "id+header",
    "id+header+trailer",
    "the ratio counts per-spec verdict rows. Several sections register a "
    "conjunction where one failure sinks the claim, so a ratio is not a "
    "section verdict.",
    "passed",
    "failed",
    "expected_pass",
    "expected_fail",
    "mixed",
    "no_expectation_stated",
    "unread",
    "the author of a prior also sets the pass mark, so a prior and a gate are "
    "not independent. The base rate is the accuracy a constant \"it fails\" "
    "would score on the same specs, and it is printed beside the hit rate "
    "because a pessimist and a forecaster are indistinguishable until the two "
    "numbers separate.",
    "a reading is a dated, attributed reading OF the record, never a fact IN it",
    "no register existed on this date",
})


class RecallError(Exception):
    """Raised when two independent derivations disagree, or a source is absent.

    Never swallowed into a default. A recall layer that guesses when its
    sources conflict is the second source of truth this module exists to avoid.
    """


def _path(rel: str) -> str:
    return os.path.join(ROOT, rel)


def _read(rel: str) -> str:
    with open(_path(rel), encoding="utf-8") as f:
        return f.read()


# ---- parsing the record ----------------------------------------------------

_SECTION_RE = re.compile(r"^(§\d+(?:[–—-]§?\d+)?[a-z]?)\s*[—–-]?\s*(.*)$")
_NUMBERED_RE = re.compile(r"^(\d+\.)\s*(.*)$")


def split_heading(text: str) -> tuple[str, str]:
    """Split a heading's text into (key, title). Both are verbatim slices.

    The key is the heading's own leading token, unmodified. `## 1.` is NOT
    rewritten to `§1` — a rename is an opinion about equivalence, and §1 and
    §35 belong to different eras of this file for reasons the record explains.

    Keys are not unique: the record carries two `## METHOD NOTE` headings.
    Lookup returns every match rather than inventing a disambiguator.
    """
    m = _SECTION_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    m = _NUMBERED_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    # A named section: METHOD NOTE, METHOD NOTE 2, PHASE 0, ALREADY-SEEN
    # OBSERVATION. The name runs up to the first em-dash separator or the first
    # parenthetical, whichever comes first.
    cut = len(text)
    for sep in (" — ", " – ", " ("):
        i = text.find(sep)
        if i != -1:
            cut = min(cut, i)
    # The opening paren is deliberately NOT stripped: `short()` in the index
    # generator removes a parenthetical carrying a date, and it needs the paren
    # to find one. `ALREADY-SEEN OBSERVATION (2026-07-28) — ...` otherwise
    # keeps a dangling `2026-07-28)` in its subject column.
    return text[:cut].strip(), text[cut:].lstrip(" —–").strip()


def _leading_number(key: str) -> int | None:
    """The first section number in a key, or None. `§14–§17` -> 14, `1.` -> None.

    Only `§`-form keys carry a number here. `## 1.` deliberately does not: it
    is not `§1`, and treating it as one would be a rename this module has no
    standing to make.
    """
    m = re.match(r"^§(\d+)", key)
    return int(m.group(1)) if m else None


def sections(rel: str = RECORD) -> list[dict]:
    """Every section of the record, in file order.

    A section is any `## ` heading, plus any `### §N` heading with no `## §N`
    counterpart (see `H3_ONLY_SECTIONS`). A section's text runs to the next
    heading at or above its own level.
    """
    lines = _read(rel).splitlines(keepends=True)
    h2_numbers = set()
    for line in lines:
        if line.startswith("## "):
            n = _leading_number(split_heading(line[3:].strip())[0])
            if n is not None:
                h2_numbers.add(n)

    starts = []
    for i, line in enumerate(lines):
        if line.startswith("## "):
            starts.append((i, 2, line[3:].strip()))
        elif line.startswith("### "):
            # An H3 is its own section only when its BASE NUMBER has no H2
            # anywhere. `### §19b RESULT`, `### §20a ADOPTED`, `### §33b
            # RESULT` and `### §7–§9 RESULTS` are all sub-headings of an H2
            # that exists; §30 is the one §-numbered section that was never
            # given an H2, which is why the index has always missed it.
            key = split_heading(line[4:].strip())[0]
            n = _leading_number(key)
            if n is not None and n not in h2_numbers:
                starts.append((i, 3, line[4:].strip()))

    out = []
    for n, (i, level, text) in enumerate(starts):
        end = len(lines)
        for j, lvl, _ in starts[n + 1:]:
            # An H3-only section ends at the next heading of ANY level it could
            # not contain; an H2 ends only at the next H2.
            if lvl <= level:
                end = j
                break
        # Trailing blank lines are stripped BEFORE hashing. The last section's
        # body runs to end-of-file, so without this every append would move the
        # previous section's hash and demand a regeneration — a guard that
        # fires on ordinary work is one that gets switched off by whoever tires
        # of it first. Trailing whitespace is not content; a reworded sentence
        # is, and that still moves the hash.
        body = "".join(lines[i:end]).rstrip()
        key, title = split_heading(text)
        out.append({
            "key": key,
            "heading": lines[i].rstrip("\n"),
            "title": title,
            "start_line": i + 1,
            "level": level,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "subheadings": [ln.rstrip("\n") for ln in lines[i + 1:end]
                            if ln.startswith("### ")],
            "trailer": _trailer_of(body),
            "_body": body,
        })
    return out


def _trailer_of(body: str) -> dict | None:
    m = TRAILER.search(body)
    if not m:
        return None
    specs = [s for s in m.group(2).split(",") if s]
    return {"section": m.group(1), "specs": specs}


def section_number(key: str) -> int | None:
    """The integer in a `§N` key, or None for `1.`, `METHOD NOTE`, `§14–§17`."""
    m = re.fullmatch(r"§(\d+)", key)
    return int(m.group(1)) if m else None


# ---- the join --------------------------------------------------------------

_SPEC_ID_RE = re.compile(r"^s(\d+)([a-z]?)$")
_SPEC_HEADER_RE = re.compile(r"^#\s*§(\d+)")


def section_of_spec_id(spec_id: str) -> str:
    """Derivation 1: `s58a` -> `§58`, read off the identifier alone."""
    m = _SPEC_ID_RE.match(spec_id)
    if not m:
        raise RecallError(f"{spec_id}: not a recognisable spec id")
    return f"§{int(m.group(1))}"


def section_of_spec_file(spec_id: str) -> str | None:
    """Derivation 2: the `# §58a — ...` comment on the spec file's first line.

    Returns None when the file is absent — a registration whose spec file was
    never committed still joins by id, and the output says which derivations
    were available rather than implying both agreed.
    """
    path = _path(os.path.join(SPECS_DIR, f"{spec_id}.yaml"))
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _SPEC_HEADER_RE.match(line)
            if m:
                return f"§{int(m.group(1))}"
            if line.strip() and not line.startswith("#"):
                return None
    return None


def join(regs: dict | None = None) -> dict:
    """spec id -> {section, source}. Refuses when the derivations disagree.

    Two independent derivations because a single one is a naming habit wearing
    the clothes of a fact. They agree on all 55 registrations today; the day
    they stop, that is a finding and not something to average over.
    """
    regs = gatespec.registrations(_path(gatespec.REGISTRATIONS)) if regs is None else regs
    out = {}
    for spec_id in sorted(regs):
        by_id = section_of_spec_id(spec_id)
        by_file = section_of_spec_file(spec_id)
        if by_file is not None and by_file != by_id:
            raise RecallError(
                f"{spec_id}: the id derives {by_id} but the spec file header "
                f"derives {by_file}. One of them is wrong and this module will "
                f"not choose.")
        trailer_src = "id-only" if by_file is None else "id+header"
        out[spec_id] = {"section": by_id, "source": trailer_src}
    return out


def specs_by_section(regs: dict | None = None) -> dict:
    """section key -> sorted spec ids registered against it."""
    out: dict[str, list[str]] = {}
    for spec_id, rec in join(regs).items():
        out.setdefault(rec["section"], []).append(spec_id)
    return {k: sorted(v) for k, v in out.items()}


def first_registered_section(regs: dict | None = None) -> int:
    """The lowest section number carrying a registration — computed, not stated.

    Rendered beside sections that have no spec, so a reader joins two facts
    rather than being told a claim about any particular section.
    """
    nums = [section_number(v["section"]) for v in join(regs).values()]
    return min(n for n in nums if n is not None)


# ---- verdicts --------------------------------------------------------------

def verdicts(rel: str = VERDICTS) -> list[dict]:
    """Every verdict row, in file order. Re-runs appear as extra rows."""
    path = _path(rel)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def latest_verdicts(rel: str = VERDICTS) -> dict:
    """spec id -> its most recent verdict row."""
    out = {}
    for row in verdicts(rel):
        out[row["id"]] = row
    return out


def gate_result(section_key: str, regs: dict | None = None,
                rel: str = VERDICTS) -> str:
    """`0/8`, `3/4`, or `—`. A COUNT, never a word.

    A ratio is arithmetic over rows and stays inside the one rule; "rejected"
    would be this module's opinion of those rows and does not. The count is
    also not a section verdict — several sections register a conjunction where
    one failure sinks the whole claim, and §44 reads 3/4 while having failed.
    That caveat ships as a footnote wherever this value is rendered.
    """
    ids = specs_by_section(regs).get(section_key, [])
    if not ids:
        return "—"
    latest = latest_verdicts(rel)
    rows = [latest[i] for i in ids if i in latest]
    if not rows:
        return "—"
    return f"{sum(1 for r in rows if r.get('passed'))}/{len(rows)}"


def clause_lines(row: dict) -> list[dict]:
    """A verdict's clauses, whether it registered a candidate or a family.

    Family specs put their per-clause results in `per_arm` and leave `clauses`
    empty — all twelve §37/§38/§58 rows do. A reader of `clauses` alone sees
    nothing for a third of §58, so both shapes are unwrapped here once.
    """
    if row.get("clauses"):
        return [{"arm": None, **c} for c in row["clauses"]]
    out = []
    for arm in sorted(row.get("per_arm") or {}):
        for c in row["per_arm"][arm]:
            out.append({"arm": arm, **c})
    return out


# ---- anchors ---------------------------------------------------------------

def build_anchors() -> dict:
    """The manifest. Identity is (key, body_sha256); start_line is a courtesy."""
    by_section = specs_by_section()
    out = []
    for s in sections():
        joined = by_section.get(s["key"], [])
        trailer = s["trailer"]
        if trailer is not None and sorted(trailer["specs"]) != sorted(joined):
            raise RecallError(
                f"{s['key']}: the trailer declares specs "
                f"{sorted(trailer['specs'])} but the registrations join "
                f"{sorted(joined)}")
        out.append({
            "key": s["key"],
            "heading": s["heading"],
            "start_line": s["start_line"],
            "body_sha256": s["body_sha256"],
            "subheadings": s["subheadings"],
            "specs": joined,
            "spec_source": ("id+header+trailer" if trailer is not None
                            else "id+header" if joined else "no registration"),
        })
    return {"generated_by": "scripts/recall.py anchors",
            "source": RECORD,
            "sections": out}


def load_anchors(rel: str = ANCHORS) -> dict:
    return json.loads(_read(rel))


def render_anchors(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


# ---- the corpus ------------------------------------------------------------

def _review_files() -> list[str]:
    d = _path("knowledge")
    return sorted(
        os.path.join("knowledge", n) for n in os.listdir(d)
        if n.startswith(os.path.basename(REVIEWS_GLOB)) and n.endswith(".md"))


def chunks() -> list[dict]:
    """Every searchable unit, each carrying the anchor it came from.

    The record is split at H3 boundaries inside each section rather than kept
    whole, because several sections run past 250 lines and an excerpt that size
    is not a quote, it is the file.
    """
    out = []
    for s in sections():
        parts, cur, cur_head = [], [], None
        for line in s["_body"].splitlines(keepends=True):
            if line.startswith("### "):
                parts.append((cur_head, "".join(cur)))
                cur, cur_head = [line], line.rstrip("\n")
            else:
                cur.append(line)
        parts.append((cur_head, "".join(cur)))
        for head, text in parts:
            if not text.strip():
                continue
            anchor = s["key"] if head is None else f"{s['key']} › {head}"
            out.append({"corpus": "record", "anchor": anchor,
                        "source_path": RECORD, "excerpt": text.rstrip("\n"),
                        "body_sha256": s["body_sha256"]})

    text = _read(DIVERGENCES)
    cur, head = [], None
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if head is not None:
                out.append({"corpus": "divergences", "anchor": head,
                            "source_path": DIVERGENCES,
                            "excerpt": "".join(cur).rstrip("\n")})
            head, cur = line[3:].strip(), [line]
        else:
            cur.append(line)
    if head is not None:
        out.append({"corpus": "divergences", "anchor": head,
                    "source_path": DIVERGENCES,
                    "excerpt": "".join(cur).rstrip("\n")})

    for rel in _review_files():
        out.append({"corpus": "reviews", "anchor": os.path.basename(rel),
                    "source_path": rel, "excerpt": _read(rel).rstrip("\n")})

    regs = gatespec.registrations(_path(gatespec.REGISTRATIONS))
    for spec_id in sorted(regs):
        spec = regs[spec_id]["spec"]
        out.append({"corpus": "priors", "anchor": f"{spec_id}{LOCATOR_SEP}title",
                    "source_path": gatespec.REGISTRATIONS,
                    "excerpt": spec["title"]})
        out.append({"corpus": "priors", "anchor": f"{spec_id}{LOCATOR_SEP}prior",
                    "source_path": gatespec.REGISTRATIONS,
                    "excerpt": spec["prior"]})
        for n, fm in enumerate(spec.get("failure_modes") or []):
            out.append({"corpus": "priors",
                        "anchor":
                            f"{spec_id}{LOCATOR_SEP}failure_mode {n}",
                        "source_path": gatespec.REGISTRATIONS, "excerpt": fm})

    for row in verdicts():
        for c in clause_lines(row):
            if c.get("detail"):
                arm = f"{LOCATOR_SEP}{c['arm']}" if c.get("arm") else ""
                out.append({"corpus": "verdicts",
                            "anchor": f"{row['id']}{arm}{LOCATOR_SEP}"
                                      f"({c['id']}) {c['rule']}",
                            "source_path": VERDICTS, "excerpt": c["detail"]})
    return out


# ---- search ----------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def search(terms: list[str], corpus: str = "all", limit: int = 10,
           regex: bool = False) -> dict:
    """BM25 over literal tokens. No stemming, no synonyms, no expansion.

    Every hit reports which of the query's terms matched it, so a reader can
    see WHY it ranked rather than trusting that it should have. The footer
    states the failure mode this cannot bound: an idea described in different
    words will not appear at all.
    """
    docs = [c for c in chunks() if corpus == "all" or c["corpus"] == corpus]

    if regex:
        pat = re.compile(terms[0], re.I | re.M)
        hits = [(0.0, d, [terms[0]]) for d in docs if pat.search(d["excerpt"])]
    else:
        query = [t.lower() for t in terms]
        toks = [_tokens(d["excerpt"]) for d in docs]
        n = len(docs)
        avgdl = (sum(len(t) for t in toks) / n) if n else 0.0
        df: dict[str, int] = {}
        for t in toks:
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        k1, b = 1.5, 0.75
        hits = []
        for d, t in zip(docs, toks):
            if not t:
                continue
            score, matched = 0.0, []
            counts = {w: t.count(w) for w in set(query) & set(t)}
            for w in query:
                f = counts.get(w, 0)
                if not f:
                    continue
                matched.append(w)
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * (f * (k1 + 1)) / (
                    f + k1 * (1 - b + b * len(t) / avgdl))
            if score > 0:
                hits.append((score, d, sorted(set(matched))))

    hits.sort(key=lambda h: (-h[0], h[1]["corpus"], h[1]["anchor"]))
    by_section = specs_by_section()
    latest = latest_verdicts()
    results = []
    for _, d, matched in hits[:limit]:
        key = d["anchor"].split(" › ")[0]
        specs = [{"id": i, "passed": bool(latest[i]["passed"])}
                 for i in by_section.get(key, []) if i in latest]
        results.append({"anchor": d["anchor"], "corpus": d["corpus"],
                        "source_path": d["source_path"],
                        "excerpt": d["excerpt"], "matched_terms": matched,
                        "specs": specs})
    return {"results": results,
            "searched": {"chunks": len(docs), "corpus": corpus},
            "note": "LEXICAL_ONLY",
            "footer": "ranking is lexical; an idea described in different "
                      "words will not appear here"}


# ---- priors and calibration ------------------------------------------------

def priors() -> list[dict]:
    """Every frozen prior, verbatim, beside what actually happened.

    Zero judgement. This alone is more than the repo has today: `prior` is a
    mandatory field on all 55 registrations and no code has ever read one.
    """
    regs = gatespec.registrations(_path(gatespec.REGISTRATIONS))
    joined = join(regs)
    latest = latest_verdicts()
    out = []
    for spec_id in sorted(regs):
        row = latest.get(spec_id)
        out.append({
            "id": spec_id,
            "section": joined[spec_id]["section"],
            "claim": regs[spec_id]["spec"]["claim"],
            "spec_sha256": regs[spec_id]["spec_sha256"],
            "prior": regs[spec_id]["spec"]["prior"],
            "outcome": ("passed" if row["passed"] else "failed") if row
                       else "no verdict recorded",
        })
    return out


def readings(rel: str = PRIOR_READINGS) -> dict:
    """spec id -> the most recent reading. Append-only on disk.

    A sidecar and not a spec field, necessarily: `gatespec.canonical()` hashes
    the whole parsed spec and `register_gate.py` refuses re-registration once a
    verdict exists, so no field can ever be added to any of the 55 frozen
    specs. Every row is a dated, attributed reading OF the record, never a fact
    IN it.
    """
    path = _path(rel)
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["id"]] = rec
    return out


def validate_reading(rec: dict, regs: dict) -> None:
    """A reading must quote the prior it claims to read, byte for byte.

    Two consequences, both wanted. A reading cannot be attached to a prior it
    does not quote. And a re-registration moves `spec_sha256`, so a reading of
    the superseded text surfaces as a mismatch instead of silently describing a
    prior that no longer exists.
    """
    if rec.get("direction") not in DIRECTIONS:
        raise RecallError(f"{rec.get('id')}: direction must be one of {DIRECTIONS}")
    reg = regs.get(rec["id"])
    if reg is None:
        raise RecallError(f"{rec['id']}: no registration to read")
    if rec["spec_sha256"] != reg["spec_sha256"]:
        raise RecallError(
            f"{rec['id']}: reading is bound to spec_sha256 "
            f"{rec['spec_sha256'][:12]} but the registration is "
            f"{reg['spec_sha256'][:12]} — the prior was re-registered and this "
            f"reading describes text that is no longer the goalpost")
    if rec["quote"] not in reg["spec"]["prior"]:
        raise RecallError(
            f"{rec['id']}: the quote does not appear byte-exact in the prior")


def calibrate() -> dict:
    """Score 55 frozen predictions against what happened. Counts, not a grade.

    No Brier score and no verdict on whether the project forecasts well. The
    confounder is structural and is reported rather than corrected for: the
    author of a prior also sets the pass mark, and EDGE stands at 1 pass in 15,
    so a constant "it fails" scores about 94% while carrying no information.
    The base rate ships beside the hit rate for exactly that reason.

    `unread` ids are reported and never dropped from the denominator. A
    calibration that quietly excluded what nobody had read would report the
    accuracy of the subset somebody chose to read.
    """
    regs = gatespec.registrations(_path(gatespec.REGISTRATIONS))
    read = readings()
    for rec in read.values():
        validate_reading(rec, regs)
    latest = latest_verdicts()
    joined = join(regs)

    cells = {(d, o): 0 for d in ("expected_pass", "expected_fail")
             for o in ("passed", "failed")}
    counts = {"mixed": 0, "no_expectation_stated": 0, "unread": 0}
    rows = []
    for spec_id in sorted(regs):
        row = latest.get(spec_id)
        outcome = ("passed" if row["passed"] else "failed") if row else None
        rec = read.get(spec_id)
        direction = rec["direction"] if rec else "unread"
        if direction in ("expected_pass", "expected_fail") and outcome:
            cells[(direction, outcome)] += 1
        else:
            # `unread` is a key of `counts` alongside `mixed` and
            # `no_expectation_stated`, so every non-scored direction lands here
            # and is counted. It is never dropped: a calibration that quietly
            # excluded what nobody had read would report the accuracy of the
            # subset somebody chose to read. (An `elif direction in counts`
            # guard used to sit above this, which made the branch below
            # unreachable — a mutation survived in it.)
            counts[direction] += 1
        rows.append({
            "id": spec_id, "section": joined[spec_id]["section"],
            "direction": direction,
            "outcome": outcome or "no verdict recorded",
            "quote": rec["quote"] if rec else None,
            "read_by": rec["read_by"] if rec else None,
            "approved_by": (rec.get("approved_by") if rec else None),
        })

    scored = sum(cells.values())
    hits = cells[("expected_pass", "passed")] + cells[("expected_fail", "failed")]
    # The baseline is computed on the SCORED subset, not on all registrations.
    # A hit rate over 51 specs compared against a base rate over 55 is two
    # different questions printed as though they were one, and the difference
    # between them would read as skill.
    scored_failed = (cells[("expected_pass", "failed")]
                     + cells[("expected_fail", "failed")])
    return {
        "cells": {f"{d}|{o}": v for (d, o), v in sorted(cells.items())},
        "counts": counts,
        "scored": scored,
        "hits": hits,
        "hit_rate": round(hits / scored, 4) if scored else None,
        "base_rate_always_fail": round(scored_failed / scored, 4) if scored else None,
        "predicted_pass": (cells[("expected_pass", "passed")]
                           + cells[("expected_pass", "failed")]),
        "registrations": len(regs),
        "rows": rows,
        "confounder": "the author of a prior also sets the pass mark, so a "
                      "prior and a gate are not independent. The base rate is "
                      "the accuracy a constant \"it fails\" would score on the "
                      "same specs, and it is printed beside the hit rate "
                      "because a pessimist and a forecaster are "
                      "indistinguishable until the two numbers separate.",
        "status": "a reading is a dated, attributed reading OF the record, "
                  "never a fact IN it",
    }


# ---- divergences, joined by date through git -------------------------------

def _git(args: list[str]) -> str:
    r = subprocess.run(["git", "-C", ROOT] + args, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RecallError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def divergences_as_of(when: str) -> dict:
    """The register as it stood at `when`, quoted from that revision.

    `when` is a date (resolved to end of that day) or a full ISO timestamp.
    The distinction is not pedantry: `s57a` ran at 00:39 on 2026-08-10 and the
    PR carrying §57's own write-up merged later the same day, so a date-only
    query would report a register the run could not have seen.

    Reports what the register SAID, which is not the same as what was true —
    `docs/divergences.md` documents itself having been wrong by nineteen days
    about #15. And it refuses outright for a date before the file existed
    rather than falling back to HEAD, because s35, s37 and s38 all ran one day
    early and today's table is not an answer about them.
    """
    if when[:10] < DIVERGENCES_FIRST_COMMIT:
        raise RecallError(
            f"{when}: no register existed on this date — {DIVERGENCES} was "
            f"first committed {DIVERGENCES_FIRST_COMMIT}")
    before = when if len(when) > 10 else f"{when} 23:59:59"
    rev = _git(["rev-list", "-1", f"--before={before}", "HEAD", "--",
                DIVERGENCES]).strip()
    if not rev:
        raise RecallError(f"{when}: no register existed on this date")
    committed = _git(["show", "-s", "--format=%cI", rev]).strip()
    text = _git(["show", f"{rev}:{DIVERGENCES}"])
    lines = text.splitlines()
    open_line = next((ln for ln in lines if "Open as of" in ln), "")
    # The FIRST contiguous run of table rows — the master status table. Taking
    # every pipe line in the file would splice rows from the per-divergence
    # deep-dives into one table that appears nowhere in the source, which is
    # composition wearing a quotation's clothes.
    start = next((i for i, ln in enumerate(lines) if ln.startswith("|")), None)
    table = ""
    if start is not None:
        end = start
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        table = "\n".join(lines[start:end])
    return {"as_of": when, "rev": rev[:12], "rev_committed": committed,
            "source_path": DIVERGENCES, "open_line": open_line,
            "table": table}


def divergences_for(spec_id: str) -> dict:
    """The register as it stood when `spec_id` actually ran, to the second."""
    row = latest_verdicts().get(spec_id)
    if row is None:
        raise RecallError(f"{spec_id}: no verdict recorded")
    out = divergences_as_of(row["ran_at"])
    out["spec_id"] = spec_id
    out["ran_at"] = row["ran_at"]
    return out


# ---- audit -----------------------------------------------------------------

def audit() -> list[str]:
    """Every way the layers currently disagree. Empty list means they do not."""
    problems = []
    try:
        joined = join()
    except RecallError as e:
        return [str(e)]

    h3_only = {s["key"] for s in sections() if s["level"] == 3}
    if h3_only != set(H3_ONLY_SECTIONS):
        problems.append(
            f"the record's H3-only sections are {sorted(h3_only)} but "
            f"H3_ONLY_SECTIONS declares {sorted(H3_ONLY_SECTIONS)} — a section "
            f"promoted or demoted a heading level, which is a decision to make "
            f"deliberately, not a set to widen silently")

    keys = {s["key"] for s in sections()}
    for spec_id, rec in sorted(joined.items()):
        if rec["section"] not in keys:
            problems.append(
                f"{spec_id} joins {rec['section']}, which is not a section of "
                f"{RECORD}")

    regs = gatespec.registrations(_path(gatespec.REGISTRATIONS))
    for row in verdicts():
        if row["id"] not in regs:
            problems.append(f"{row['id']} has a verdict but no registration")

    for s in sections():
        n = section_number(s["key"])
        if n is not None and n >= TRAILER_REQUIRED_FROM and s["trailer"] is None:
            problems.append(
                f"{s['key']} is at or after §{TRAILER_REQUIRED_FROM} and "
                f"carries no `<!-- recall: ... -->` trailer")

    try:
        built = build_anchors()
    except RecallError as e:
        problems.append(str(e))
        return problems
    if os.path.exists(_path(ANCHORS)):
        if render_anchors(built) != _read(ANCHORS):
            problems.append(f"{ANCHORS} is stale — regenerate it")

    for rec in readings().values():
        try:
            validate_reading(rec, regs)
        except RecallError as e:
            problems.append(str(e))
    return problems
