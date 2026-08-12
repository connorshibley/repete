#!/usr/bin/env python3
"""Query the research record. `python scripts/recall.py <command> [...]`

    recall.py search <terms…> [--corpus record|priors|reviews|divergences|verdicts|all]
    recall.py section §58 [--full]
    recall.py spec s58a
    recall.py verdicts [--section §58] [--failed] [--claim EDGE]
    recall.py metrics s58a [--arm coil10]
    recall.py priors [--section §58]
    recall.py divergences (--as-of 2026-08-09 | --for s57a)
    recall.py calibrate
    recall.py anchors [--check]
    recall.py audit

Every command takes `--json`. Every string in that output is a byte-exact span
of a named source file, an identifier/count/path/ratio/date, or one of
`recall.LABELS` — `tests/test_recall_quotes.py` walks the output and fails on
anything else. See `src/recall.py` for why that rule exists.

The one thing this tool will not do is tell you whether a result was good news.
`passed: true` is not that: §48 reads 4/4 and is the finding that survivorship
poisons the benchmark, and §51 reads 3/3 and is the section that sized the
inflation at +200pp. The ratio is a count of rows. Read the section.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import recall  # noqa: E402

FOOTNOTE = ("the ratio counts per-spec verdict rows. Several sections register "
            "a conjunction where one failure sinks the claim, so a ratio is "
            "not a section verdict.")


def _emit(payload, args, render):
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render(payload)


def cmd_search(args):
    out = recall.search(args.terms, corpus=args.corpus, limit=args.limit,
                        regex=args.regex)

    def render(p):
        for r in p["results"]:
            specs = "  ".join(
                f"{s['id']}={'passed' if s['passed'] else 'failed'}"
                for s in r["specs"])
            print(f"\n=== {r['anchor']}")
            print(f"    {r['source_path']}   matched: "
                  f"{' '.join(r['matched_terms'])}")
            if specs:
                print(f"    specs: {specs}")
            print()
            for line in r["excerpt"].splitlines()[:14]:
                print(f"    {line}")
        print(f"\n-- searched {p['searched']['chunks']} chunks "
              f"({p['searched']['corpus']}) --")
        print(f"-- {p['footer']} --")

    _emit(out, args, render)


def cmd_section(args):
    matches = [s for s in recall.sections() if s["key"] == args.key]
    if not matches:
        raise SystemExit(f"no section keyed {args.key} in {recall.RECORD}")
    by_section = recall.specs_by_section()
    latest = recall.latest_verdicts()
    payload = []
    for s in matches:
        ids = by_section.get(s["key"], [])
        payload.append({
            "key": s["key"], "heading": s["heading"],
            "source_path": recall.RECORD, "start_line": s["start_line"],
            "body_sha256": s["body_sha256"],
            "subheadings": s["subheadings"],
            "gate_result": recall.gate_result(s["key"]),
            "specs": [{
                "id": i,
                "outcome": ("passed" if latest[i]["passed"] else "failed")
                           if i in latest else "no verdict recorded",
                "ran_at": latest[i]["ran_at"] if i in latest else None,
            } for i in ids],
            "first_registered_section": recall.first_registered_section(),
            "excerpt": s["_body"].rstrip("\n") if args.full else None,
            "footnote": FOOTNOTE,
        })

    def render(rows):
        for s in rows:
            print(f"\n{s['heading']}")
            print(f"  {s['source_path']}:{s['start_line']}   "
                  f"sha {s['body_sha256'][:12]}")
            print(f"  gate result: {s['gate_result']}")
            if s["specs"]:
                for spec in s["specs"]:
                    print(f"    {spec['id']:8} {spec['outcome']}")
            else:
                print(f"    (no registration; first registered section is "
                      f"§{s['first_registered_section']})")
            for h in s["subheadings"]:
                print(f"  {h}")
            if s["excerpt"]:
                print()
                for line in s["excerpt"].splitlines():
                    print(f"  {line}")
            print(f"\n  -- {s['footnote']} --")

    _emit(payload, args, render)


def cmd_spec(args):
    regs = recall.gatespec.registrations(
        recall._path(recall.gatespec.REGISTRATIONS))
    if args.id not in regs:
        raise SystemExit(f"{args.id} is not registered")
    reg = regs[args.id]
    row = recall.latest_verdicts().get(args.id)
    payload = {
        "id": args.id,
        "section": recall.section_of_spec_id(args.id),
        "source_path": recall.gatespec.REGISTRATIONS,
        "registered_at": reg["registered_at"],
        "spec_sha256": reg["spec_sha256"],
        "claim": reg["spec"]["claim"],
        "title": reg["spec"]["title"],
        "snapshot": reg["spec"]["snapshot"]["path"],
        "prior": reg["spec"]["prior"],
        "failure_modes": reg["spec"].get("failure_modes") or [],
        "outcome": ("passed" if row["passed"] else "failed") if row
                   else "no verdict recorded",
        "ran_at": row["ran_at"] if row else None,
        "clauses": recall.clause_lines(row) if row else [],
    }

    def render(p):
        print(f"\n{p['id']}  {p['section']}  {p['claim']}  -> {p['outcome']}")
        print(f"  {p['title']}")
        print(f"  snapshot: {p['snapshot']}")
        print(f"  registered {p['registered_at']}  sha {p['spec_sha256'][:12]}")
        print("\n  PRIOR")
        for line in p["prior"].splitlines():
            print(f"    {line}")
        if p["failure_modes"]:
            print("\n  FAILURE MODES (written before the run)")
            for fm in p["failure_modes"]:
                print(f"    - {fm.splitlines()[0]}")
        if p["clauses"]:
            print("\n  CLAUSES")
            for c in p["clauses"]:
                arm = f"[{c['arm']}] " if c.get("arm") else ""
                mark = "PASS" if c["pass"] else "FAIL"
                print(f"    [{mark}] {arm}({c['id']}) {c['rule']}")
                if c.get("detail"):
                    print(f"           {c['detail']}")

    _emit(payload, args, render)


def cmd_verdicts(args):
    latest = recall.latest_verdicts()
    regs = recall.gatespec.registrations(
        recall._path(recall.gatespec.REGISTRATIONS))
    joined = recall.join(regs)
    rows = []
    for spec_id in sorted(latest):
        row = latest[spec_id]
        section = joined.get(spec_id, {}).get("section")
        if args.section and section != args.section:
            continue
        if args.failed and row["passed"]:
            continue
        if args.claim and regs[spec_id]["spec"]["claim"] != args.claim:
            continue
        rows.append({
            "id": spec_id, "section": section,
            "claim": regs[spec_id]["spec"]["claim"],
            "outcome": "passed" if row["passed"] else "failed",
            "ran_at": row["ran_at"],
            "source_path": recall.VERDICTS,
            "failed_clauses": [c for c in recall.clause_lines(row)
                               if not c["pass"]],
        })

    def render(rs):
        for r in rs:
            print(f"\n{r['id']:8} {r['section']:8} {r['claim']:11} "
                  f"{r['outcome']:7} {r['ran_at'][:10]}")
            for c in r["failed_clauses"]:
                arm = f"[{c['arm']}] " if c.get("arm") else ""
                print(f"    FAIL {arm}({c['id']}) {c['rule']}")
                if c.get("detail"):
                    print(f"         {c['detail']}")
        print(f"\n-- {len(rs)} rows --")
        print(f"-- {FOOTNOTE} --")

    _emit(rows, args, render)


def cmd_metrics(args):
    row = recall.latest_verdicts().get(args.id)
    if row is None:
        raise SystemExit(f"{args.id}: no verdict recorded")
    arms = row["arms"]
    if args.arm:
        if args.arm not in arms:
            raise SystemExit(f"{args.id} has no arm {args.arm}; "
                             f"arms are {sorted(arms)}")
        arms = {args.arm: arms[args.arm]}
    payload = {"id": args.id, "source_path": recall.VERDICTS,
               "ran_at": row["ran_at"], "arms": arms}

    def render(p):
        print(f"\n{p['id']}  ran {p['ran_at'][:10]}   {p['source_path']}")
        keys = ("total_return_pct", "profit_factor", "max_drawdown_pct",
                "n_trades", "avg_deployment_pct", "win_rate",
                "buy_hold_return_pct")
        print(f"\n  {'arm':14} " + " ".join(f"{k.split('_')[0][:9]:>10}"
                                            for k in keys))
        for name in sorted(p["arms"]):
            a = p["arms"][name]
            print(f"  {name:14} " + " ".join(
                f"{a.get(k, ''):>10}" for k in keys))

    _emit(payload, args, render)


def cmd_priors(args):
    rows = recall.priors()
    if args.section:
        rows = [r for r in rows if r["section"] == args.section]

    def render(rs):
        for r in rs:
            print(f"\n{r['id']:8} {r['section']:8} {r['claim']:11} "
                  f"-> {r['outcome']}")
            for line in r["prior"].splitlines():
                print(f"    {line}")
        print(f"\n-- {len(rs)} priors, verbatim from "
              f"{recall.gatespec.REGISTRATIONS} --")

    _emit(rows, args, render)


def cmd_divergences(args):
    out = (recall.divergences_for(args.for_spec) if args.for_spec
           else recall.divergences_as_of(args.as_of))

    def render(p):
        if p.get("spec_id"):
            print(f"\n{p['spec_id']} ran {p['ran_at']}")
        print(f"{p['source_path']} as of {p['as_of']} "
              f"-> commit {p['rev']} ({p['rev_committed'][:10]})")
        print(f"\n{p['open_line']}\n")
        print(p["table"])

    _emit(out, args, render)


def cmd_calibrate(args):
    out = recall.calibrate()

    def render(p):
        print("\n  PRIOR vs OUTCOME")
        print(f"    {'':18} {'passed':>8} {'failed':>8}")
        for d in ("expected_pass", "expected_fail"):
            print(f"    {d:18} " + " ".join(
                f"{p['cells'][f'{d}|{o}']:>8}" for o in ("passed", "failed")))
        print()
        for k, v in p["counts"].items():
            print(f"    {k:22} {v:>4}")
        print(f"\n    scored           {p['scored']:>4} of "
              f"{p['registrations']} registrations")
        if p["hit_rate"] is not None:
            print(f"    hit rate         {p['hit_rate']:>7.1%}")
        if p["base_rate_always_fail"] is not None:
            print(f"    base rate        {p['base_rate_always_fail']:>7.1%}"
                  f"   (a constant 'it fails', same specs)")
        print(f"    predicted a pass {p['predicted_pass']:>4}")
        print(f"\n  -- {p['confounder']} --")
        print(f"  -- {p['status']} --")

    _emit(out, args, render)


def cmd_anchors(args):
    built = recall.build_anchors()
    text = recall.render_anchors(built)
    path = recall._path(recall.ANCHORS)
    if args.check:
        current = recall._read(recall.ANCHORS) if os.path.exists(path) else ""
        if current != text:
            raise SystemExit(
                f"{recall.ANCHORS} is stale. Regenerate with "
                f"`python scripts/recall.py anchors`.")
        print(f"{recall.ANCHORS} is current "
              f"({len(built['sections'])} sections)")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {recall.ANCHORS} ({len(built['sections'])} sections)")


def cmd_audit(args):
    problems = recall.audit()
    for p in problems:
        print(f"!! {p}")
    if problems:
        raise SystemExit(f"{len(problems)} problem(s)")
    print("the record, the registrations and the verdicts agree")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search")
    p.add_argument("terms", nargs="+")
    p.add_argument("--corpus", default="all",
                   choices=["record", "priors", "reviews", "divergences",
                            "verdicts", "all"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--regex", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("section")
    p.add_argument("key")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_section)

    p = sub.add_parser("spec")
    p.add_argument("id")
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("verdicts")
    p.add_argument("--section")
    p.add_argument("--failed", action="store_true")
    p.add_argument("--claim", choices=list(recall.gatespec.CLAIM_TYPES))
    p.set_defaults(func=cmd_verdicts)

    p = sub.add_parser("metrics")
    p.add_argument("id")
    p.add_argument("--arm")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("priors")
    p.add_argument("--section")
    p.set_defaults(func=cmd_priors)

    p = sub.add_parser("divergences")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--as-of", dest="as_of")
    g.add_argument("--for", dest="for_spec")
    p.set_defaults(func=cmd_divergences)

    p = sub.add_parser("calibrate")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("anchors")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_anchors)

    p = sub.add_parser("audit")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except recall.RecallError as e:
        raise SystemExit(f"!! {e}")


if __name__ == "__main__":
    main()
