# Email previews

All examples use fictional papers and no credentials, network requests or email delivery. Regenerate HTML/plain text with `uv run --frozen python scripts/preview_email.py`.

- `daily`: 25 journals, 15 preprints and 5 random picks, original abstracts and missing metadata.
- `empty` and `shortage`: empty groups and insufficient eligible papers.
- PNGs: desktop and mobile visual checks of the current three-column/stacked template.

Presentation tables, inline CSS and Aptos/system font fallbacks support email clients. No external images, scripts or tracking pixels are used. Browser checks do not establish rendering in Gmail, Outlook or Apple Mail; clients may simplify rounded corners or clip a long digest. Original abstracts remain complete.

Template attribution and license remain in the repository README and LICENSE.
