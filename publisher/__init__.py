"""Publisher — the subscription-publication layer (Phase B, 2026-07-22).

A separate service that READS the agent's stores and serves/e-mails tiered
content. Two invariants from CLAUDE.md govern everything here:

  #9  READ-ONLY: nothing in this package may write to the agent's state or
      influence a trading decision. A cycle behaves identically whether the
      publisher exists or not.
  #10 NO MONEY BEFORE THE GATES: checkout is refused in code until the
      revenue gates pass (publisher/gates.py; thresholds in PRODUCT.md).

External services (email, Stripe) default to dry-run/stub — real keys in
the environment flip behavior, never code edits.
"""
