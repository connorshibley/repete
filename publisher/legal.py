"""Legal page scaffolding — DRAFTS for attorney review, not in force.

These exist so counsel has something concrete to mark up, and so the
revenue gate has a real artifact to check (publisher.legal_pages_final
stays false until the reviewed versions replace these).
"""

DRAFT_BANNER = ("<div style='background:#fde68a;color:#78350f;padding:10px "
                "16px;font-weight:700'>DRAFT — REQUIRES ATTORNEY REVIEW — "
                "NOT IN FORCE. No subscriptions are being sold.</div>")

TERMS = DRAFT_BANNER + """
<h1>Terms of Service (DRAFT)</h1>
<p>Repete is a paper-trading publication. By subscribing you acknowledge:</p>
<ul>
<li>All content is impersonal commentary published on a regular schedule to
all subscribers alike. Nothing is tailored to your situation.</li>
<li>Nothing published is investment advice, an offer, or a solicitation.</li>
<li>All trading shown is SIMULATED. No real money is traded.</li>
<li>No performance, simulated or otherwise, predicts future results.</li>
<li>You are solely responsible for your own investment decisions.</li>
</ul>
<p>[Attorney: governing law, arbitration, refunds, liability caps,
termination, IP.]</p>"""

PRIVACY = DRAFT_BANNER + """
<h1>Privacy Policy (DRAFT)</h1>
<p>We store: your email address, subscription status, and hashes of
single-use login tokens. We never store passwords (none exist) or payment
card details (Stripe processes payments). We do not sell or share your
data. Unsubscribing stops all email; you may request deletion at any
time.</p>
<p>[Attorney: data retention, processors list, jurisdiction disclosures.]</p>"""

RISK = DRAFT_BANNER + """
<h1>Risk Disclosure (DRAFT)</h1>
<p>Trading securities involves substantial risk of loss. The strategies
described are experimental, automated, and SIMULATED with paper money;
simulated results have inherent limitations and do not reflect real
execution, liquidity, or the discipline required to hold through drawdowns.
Markets can and do behave unlike any historical period. Never trade money
you cannot afford to lose. Consult a licensed financial advisor before
making investment decisions.</p>"""
