# Email preview

Fictional papers only. No Zotero library, account, credentials, network request or mail delivery is used to create these examples.

Reference: [upstream template](https://github.com/TideDra/zotero-arxiv-daily/blob/main/src/zotero_arxiv_daily/construct_email.py) and [upstream screenshot](https://github.com/TideDra/zotero-arxiv-daily/blob/main/assets/screenshot.png).

Regenerate HTML and text with `PYTHONPATH=src uv run python scripts/preview_email.py`.

- `email-preview.html` / `.txt`: four numbered entries, original abstract, unavailable AI fallback, fictional AI summary, missing metadata and a negative score.
- `empty-preview.html` / `.txt`: no recommendations, no invented sequence number.
- `desktop.png` and `mobile.png`: Chromium screenshots at 960px and 375px widths.

Layout uses presentation tables, inline CSS, system fonts and an Outlook fixed-width wrapper; no scripts, external styles, images or tracking pixels. Plain text is included as the first MIME alternative. Chromium checks also cover 50 long-title/long-abstract entries at 320/375/960px without horizontal overflow. Actual Gmail, Outlook and Apple Mail rendering has not been tested. Some clients may simplify rounded corners or clip long digests; abstracts are not shortened to avoid that limit.
