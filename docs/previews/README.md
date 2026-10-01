# Email previews

All papers are synthetic. Generate HTML/plain text with `uv run --frozen python scripts/preview_email.py`; no credentials, network calls or delivery are used.

- `daily`: 25 journals, 15 preprints, 5 random picks, independently numbered in three vertically stacked sections.
- `showcase`: successful Chinese summary, missing abstract, failed-summary fallback and a long affiliation.
- `empty` and `shortage`: empty groups and insufficient eligible papers.
- PNGs verify a single column at desktop and mobile widths. Affiliations use a deterministic character limit, not CSS clipping.

Successful AI summaries replace original abstracts in both email formats; original abstracts and affiliations remain complete in internal data. Without a valid summary, email shows the original abstract or a truthful unavailable notice.

Presentation tables, inline CSS and Aptos/system fallbacks require no media query, external images, scripts or tracking pixels. Browser checks do not establish exact Gmail, Outlook or Apple Mail rendering; clients may simplify corners or clip long digests. Attribution and license remain in README and LICENSE.
