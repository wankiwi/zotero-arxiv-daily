# October 5 daily digest investigation

## Production evidence

The latest **Send emails daily** run is [37239640651](https://github.com/wankiwi/zotero-arxiv-daily/actions/runs/37239640651),
using `267c09116aab826bef3bfd565f1e9630f7cb4107`. It started on October 4 at
22:20:27 UTC (October 5 in Asia/Singapore).

The [recommend job](https://github.com/wankiwi/zotero-arxiv-daily/actions/runs/37239640651/job/111545566133)
recorded a bioRxiv `no posts found` response at 22:25:10. The program incorrectly
treated that legitimate empty category/date window as a source failure. At
22:59:45 SMTP nevertheless accepted **50 recommendations**, then
`executor.py:188` raised `RuntimeError: Pipeline completed with failures` for
the previously collected bioRxiv error. This was a retrieval-status bug after
successful email acceptance, rather than an SMTP failure.

No run artifacts were published. The stored output is the `recommendations.json`
file on `paper-state`, committed by [db422635](https://github.com/wankiwi/zotero-arxiv-daily/commit/db422635bccdd538500b9305fd0a3e44cbf15557)
at 22:59:47. Exactly 50 records were added at 22:59:39 and all 50 have
`channels.email=true`. Reconstructing the original renderer from those records
produces 50 repeated `Scored using` sentences.

| Stored October 5 digest | Count | Cause/evidence |
| --- | ---: | --- |
| Journals / preprints / random | 25 / 20 / 5 | Stored recommendation groups; CUSTOM_CONFIG agrees |
| Journal / OpenReview sources | 26 / 24 | Random picks contribute to the source totals |
| Authors missing | 25 | All 24 OpenReview papers plus one Nature Chemistry item |
| Affiliations missing | 28 | All 24 OpenReview papers plus four Nature-family items |
| AI summaries generated | 46 | All have `tldr_status=generated`, no `tldr_error` |
| AI summaries unavailable | 4 | All have `tldr_error=budget_unavailable`, with original input retained |
| Summary API/model errors | 0 | No stored `request_failed` or `model_unavailable` |
| Abstract strings missing | 0 | One string is only a correction publication notice, as described below |

The production budget reserves the entire UTC day's ¥0.20 in advance. With the
existing conservative bound, each request precharges
`(1152 × 3 + 96 × 9) / 1,000,000 = ¥0.004320`. Thus only 46 requests fit:
46 × ¥0.004320 = ¥0.198720, leaving ¥0.001280. The last four papers have all the
expected budget fallback fields. An offline concurrent replay of the actual
guard reproduces 46 successful synthetic summaries and four budget fallbacks.
`Model requests stopped=False` in the old log only reports the model-unavailable
flag, not the remaining budget. `BudgetRequests.call` checks and decrements its
own balance before dispatch, without setting that flag on exhaustion. The
stored per-paper error, durable reservation and concurrent replay agree. The
PR now logs per-reason counts (for this replay, `daily_budget_exhausted: 4`),
and calls the independent flag `model unavailable`.

After reviewing this evidence, the user explicitly authorized a **CNY0.30**
daily estimated cap on October 5. Fifty unchanged reservations cost CNY0.216,
leaving CNY0.084. The PR changes the default, hard maximum and mandatory workflow
budget override to 0.30, without reducing input/output tokens or changing
thinking, tariff, whole-day reservation, no-refund policy or prior ledger entries.
An existing CNY0.20 claim still blocks a same-UTC-day rerun at the new cap;
there is no top-up or fresh grant for an already reserved day. Missing usable
input or API errors can still prevent a summary despite sufficient budget.

The GitHub Actions repository **variable** `CUSTOM_CONFIG` was safely updated
from 0.20 to 0.30 and read back. Only the `llm.budget.daily_cny` scalar changed;
every other configuration byte was preserved. No literal credentials were
present in the variable and no secrets were accessed or updated. Quotas remain
25/20/5, weights 0.4/0.6. **Main still forces a 0.20 runtime budget** in
`scripts/prepare_workflow.py`, so the stored variable is 0.30 while production
runtime remains 0.20 until this PR is explicitly approved and merged. No merge,
workflow dispatch, paid validation or extra production email was performed.

Pre-ranking abstract recovery recovered 12/25 candidates. Publisher access was
blocked and Semantic Scholar returned HTTP 429. Those broader candidate recovery
limitations are separate from the final selected digest's four budget fallbacks.
Encrypted cache sealing/upload and history persistence both succeeded.

## Public metadata verification

Four Crossref DOI lookups on October 5 matched the stored titles exactly. The
deposited records contain no affiliations; the Nature Chemistry record also
contains no authors. OpenAlex returned the same exact DOIs and titles and
article-specific affiliations for three of the four. Sanitized public responses
are retained in `tests/fixtures/oct5_public_metadata.json` for offline replay.

| DOI | Crossref authors | Crossref affiliations | OpenAlex recovery |
| --- | ---: | ---: | --- |
| `10.1038/s43588-026-01059-w` | 2 | 0 | Affiliations recovered |
| `10.1038/s41557-026-02262-y` | 0 | 0 | Neither authors nor affiliations supplied |
| `10.1038/s41570-026-00882-z` | 6 | 0 | Affiliations recovered |
| `10.1038/s41586-026-11083-5` | 31 | 0 | Affiliations recovered; this is a correction notice |

The **unauthenticated local** request `GET https://api2.openreview.net/notes?id=A9wPaTiqh0`
returned **HTTP 403**. Diagnostic requests to that provider were then stopped,
with no alternate routes or identities attempted. The stored digest has neither
the original note payloads nor field ACLs. Consequently the precise reason for
each of the 24 missing OpenReview identities cannot be proved from available
evidence: do not describe all of them as confirmed anonymous submissions or
assume that production credentials failed. The production job's environment
shows both OpenReview credential variables present and masked; no values were
read. The released retriever authenticates through `/login` before reading notes,
and login rejection aborts that source. It produced these 24 new OpenReview
records, with no logged login/access failure. Together those facts establish
successful authenticated production retrieval, despite the separate local 403.
The released converter already reads public `authors`, so an omitted authors
extraction step is not established for these papers. Explicit public affiliation
fields were not read before this PR. The stored output cannot distinguish absent
fields, field ACL restrictions or anonymous placeholder values for individual
papers; future persisted status codes will make those cases visible.
Resolving that uncertainty requires an authorized official API response exposing
the relevant *public* fields. Restricted fields must remain excluded even when
the production client authenticates successfully.

The fourth journal is “Author Correction: Proteasome-guided haem signalling axis
contributes to T cell exhaustion”. Its 166-character stored abstract is only
`Nature, Published online: ...; doi:...` followed by the title. It did not get
an AI summary in this run because it was also one of the four budget fallbacks.
RSS conversion treated the publication notice as abstract input. It is now
removed while preserving this paper and any substantive text. The PR initially
broadened the existing title filter to Author/Publisher Correction prefixes;
that unintended scope change has been reverted in both RSS and Crossref paths.
The original main-branch filters and cover exclusion are retained, with no new
blanket correction, news or reply exclusion.

The duplicate merge bug is reproducible: `_journal` puts Crossref records before
publisher RSS; `deduplicate` kept the first record and filled only abstract,
full text, PDF URL and DOI. It dropped authors/affiliations present only in a
later same-title, same-DOI/version record. The fix fills those missing metadata
fields with their provenance, without overwriting existing data or borrowing
across versions. No raw duplicate records from this production run were retained.
Therefore **zero of today's missing authors are proven recoverable by this
merge fix**. The four public DOI fixtures prove three affiliation recoveries,
zero new authors; the authorless Nature Chemistry record is also authorless in
both Crossref and OpenAlex.

## Changes

- Accept explicit bioRxiv/medRxiv `no posts found` only when the collection and
  pagination totals are consistent with an empty result; keep malformed
  payloads, contradictory totals, incomplete pagination and real API errors fatal.
  An empty category does not discard papers from other categories.
- Preserve missing authors and affiliations when merging exact-title records
  for the same DOI/version. Keep Crossref corporate author names. Do not borrow
  metadata across DOI versions or different titles.
- Recover selected-paper metadata through free Crossref and OpenAlex work
  records with exact DOI/version and title verification. Preserve existing
  metadata, persist provenance, cap lookups at 50 papers/90 seconds, and share
  prior provider refusals with abstract recovery. Stop a provider after
  HTTP 401/403/429; never consult author-profile employers or paid enrichment.
- Read explicit public OpenReview affiliations and distinguish provided,
  missing, nonpublic and anonymous authors. Apply note and field ACLs before
  reading identity fields; do not deanonymize missing authors through searches.
- Retain the original article filters and strip precise RSS publication
  boilerplate plus a repeated title from summary input, preserving papers and
  substantive abstracts, including Author/Publisher Corrections, news and replies.
- Persist safe summary failure reason codes and display budget, billing, model
  or request degradation without provider response bodies. Only a successful
  generated summary hides the original abstract.
- Put one scoring explanation before the email cards, using their recorded
  effective weights. Handle keyword-only, library-only, empty and mixed pending
  digests. Retain a short per-paper title-only basis note when necessary.
- Apply the newly authorized CNY0.30 estimated daily budget in local defaults
  and workflow policy; retain all existing reservations and delivery history.

The reconstructed historical 50-paper digest has 25 authors and 25 affiliations
still unavailable after three verified affiliation recoveries. This replay
preserves historical membership, including the correction; it is not a forecast
of the next run's selection. No production email or delivery/budget state is
modified by the replay.

## Verification

- Red reproduction on the released source: `tests/test_oct5_regressions.py`
  produced 19 failures and eight passes before fixes.
- Locked-dependency suite: `uv run --frozen pytest --cov=src/zotero_arxiv_daily
  --cov-report=term-missing` — **812 passed, one slow model-download test
  deselected**, 90.2513% total statement coverage (3555/3939 statements).
- The 60 newly added cases cover empty responses, incomplete pagination,
  transport acceptance and repeat-run history, actual public metadata fixtures,
  identity/version mismatches, provider refusals, limits, field ACLs, budget
  capacity, original-abstract retention, and HTML/plain-text header placement.
- `uvx ruff check --select F401,F821,F841 src scripts tests` and
  `git diff --check` pass.

Workflow scheduling, the encrypted-cache implementation, Jina PyTorch model, score
conversion, quotas, configured weights, UTC 19:17 schedule, Asia/Singapore subject
date, RSS/Pages policy, Aptos single-column styling and Zotero confirmation link
are retained. Only the explicitly authorized nonsecret CUSTOM_CONFIG budget
scalar was changed. No credentials, local memory, secrets, production state,
paid diagnostic calls, merge or deployment are changed or performed.
