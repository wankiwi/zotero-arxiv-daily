# Validation record — 2026-09-30

Local cloud validation of the journal/RSS extension. Live figures below are observations for the UTC publication window 2026-09-23 through 2026-09-30, not fixed expected counts for future runs.

## Passed

- Default regression suite: 117 passed, 1 slow model test deselected.
- Slow model test, run separately: 1 passed, 117 other tests deselected. The actual `jinaai/jina-embeddings-v5-text-nano-retrieval` model downloaded and performed embedding inference.
- Actionlint checks for the daily, manual test and CI workflows.
- Ruff checks for unused imports, undefined names and unused locals in `src/` and `scripts/`.
- Local real Git integration: state-branch restore/save preserved the working branch and staged user changes.

## Live public journal retrieval

| Journal | Candidates |
| --- | ---: |
| JACS | 126 |
| JCTC | 19 |
| PRL | 109 |
| Nature | 92 |
| Science | 44 |
| Science Advances | 91 |
| JCP | 59 |
| JPCL | 49 |
| Nature Chemistry | 11 |
| Nature Physics | 15 |
| Nature Reviews Chemistry | 5 |
| Nature Reviews Materials | 4 |

All 12 selected journal queries completed with no unresolved Crossref failures. Several publisher feeds returned HTTP 403; the Crossref fallback succeeded. JACS/JCTC retrieval took approximately 5.5 seconds in this instance; the other six core journals took approximately 12.1 seconds. These are individual observed runs, not benchmark guarantees.

The first live run exposed a performance bug: filtering by Crossref index date paged through historical articles that had recently been reindexed, reaching the page cap or HTTP 429. The query now filters publication dates before pagination and still checks the article's actual publication metadata. A regression assertion protects the query filter. Retesting JACS/JCTC returned 145 recent candidates without retrieval failure.

## Live model and RSS smoke check

145 live JACS/JCTC candidates were scored against a **synthetic computational chemistry interest profile**, using the actual configured local embedding model. This does not validate the user's personal Zotero recommendations. The top five recommendations generated valid RSS XML. Reloading isolated test state and writing the feed again preserved the same five unique GUIDs and introduced no duplicate items.

The smoke check used no LLM API, personal library or SMTP account. Its outputs are outside the repository's application state in `/workspace/.setup/validation/`.

## Blocked or not performed

- Complete Nature catalogue refresh: anonymous publisher redirects require `idp.nature.com`, which the running proxy denied. The necessary domain was added to the environment draft, but runtime access remains unverified. The four Nature subjournal checks above do not establish complete family coverage.
- Personal Zotero ingestion/recommendation: `ZOTERO_ID` and `ZOTERO_KEY` were absent from the current process and local `.env`. Their requirements were saved in environment settings.
- Real LLM requests and SMTP delivery: no usable application credentials were present in this cloud instance. No message was sent.
- GitHub Actions execution and Pages deployment: `GH_TOKEN` and native Git read/write access were available, but GitHub API requests were denied by the proxy. `api.github.com` was added to the environment draft. Existing GitHub Actions Secrets could not be inspected, and no online workflow/deployment was triggered.

Resume blocked checks after the runtime network settings and required secure credentials are supplied. Do not treat mock transport tests or this synthetic interest profile as personal end-to-end validation.
