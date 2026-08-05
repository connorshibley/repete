"""Read-only access to the agent's stores (CLAUDE.md invariant #9).

The publisher gets THIS wrapper instead of `Ledger`: it exposes only read
methods, so a write from the publisher layer is not a policy violation —
it's an AttributeError. Journal and posts are read the same way.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import store as store_mod  # noqa: E402
from strategies.base import ENTRY_ACTIONS  # noqa: E402


class ReadOnlyLedger:
    """all_records / open_buys / closed_trades — and nothing else.

    Backed by a genuinely read-only store (no append method; sqlite opened
    mode=ro), so invariant #9 is structural: a write from the publisher is an
    AttributeError, not a policy we merely promise to honor. Pass `cfg` so the
    reader honors the agent's configured backend without mutating it."""

    def __init__(self, path: str, cfg: dict | None = None):
        self._store = (store_mod.read_only_reader(cfg, path) if cfg is not None
                       else store_mod.open_store_readonly(path))

    def all_records(self) -> list[dict]:
        return self._store.read_all()

    def open_buys(self) -> dict:
        """Mirror of `Ledger.open_buys` — ENTRY_ACTIONS, not `== "buy"`.

        Extended in lockstep with the writable Ledger deliberately. This is a
        MIRROR whose whole value is agreeing with the thing it mirrors; had
        only the writable side learned about shorts, the published dashboard
        and the bot would have disagreed about how many positions are open,
        which is precisely the class of contradiction the per-position marks
        were added to catch on 2026-07-25."""
        out = {}
        for r in self.all_records():
            if r.get("type") == "decision" and r.get("executed") \
                    and r.get("action") in ENTRY_ACTIONS:
                out[r["trade_id"]] = r
            elif r.get("type") == "outcome":
                out.pop(r.get("trade_id"), None)
        return out

    def closed_trades(self) -> list[dict]:
        decisions = {r["trade_id"]: r for r in self.all_records()
                     if r.get("type") == "decision"}
        out = []
        for r in self.all_records():
            if r.get("type") == "outcome" and r.get("trade_id") in decisions:
                merged = dict(decisions[r["trade_id"]])
                merged.update({k: r[k] for k in
                               ("exit_price", "pnl", "pnl_pct", "result")
                               if k in r})
                merged["exit_reason"] = r.get("exit_reason", "")
                merged["exit_ts"] = r.get("ts")
                out.append(merged)
        return out


def read_stream(path: str, cfg: dict | None = None) -> list[dict]:
    """Read any agent JSONL/sqlite stream (journal, posts) read-only."""
    reader = (store_mod.read_only_reader(cfg, path) if cfg is not None
              else store_mod.open_store_readonly(path))
    return reader.read_all()


def agent_paths(cfg: dict) -> dict:
    return {
        "ledger": cfg.get("memory", {}).get("ledger_path",
                                            "memory/ledger.jsonl"),
        "journal": cfg.get("x_posting", {}).get("journal_path",
                                                "memory/journal.jsonl"),
    }
