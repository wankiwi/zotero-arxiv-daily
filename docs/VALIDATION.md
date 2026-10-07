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


## All-source default and Research Square update

The latest regression suite passed **141 tests**, with 1 unchanged slow model test deselected. Actionlint and Ruff checks also passed. Tests cover missing/empty `PAPER_CONFIG`, the `all`/`default` aliases, preprint-only and individual-platform configurations, mixed-source overrides, common lookback overrides, all-category date queries, pagination guards, date/type filtering, primary versus cross-listed arXiv categories, and Research Square version/state identity. The former arXiv/email defaults remain available as `legacy`.

Live public API checks on 2026-09-30 succeeded:

| Source | Retrieved candidates | Scope |
| --- | ---: | --- |
| Research Square | 720 | Complete paginated Crossref query, date window 2026-09-29 through 2026-09-30, after version deduplication and withdrawal filtering |
| bioRxiv | 322 | Complete paginated API date window 2026-09-29 through 2026-09-30, all categories |
| medRxiv | 97 | Complete API date window 2026-09-29 through 2026-09-30, all categories |
| arXiv | 10 | Debug limit, all-category submitted-date API query over the preceding 24 hours |

Research Square returned a publicly marked `WITHDRAWN` record. The retriever now suppresses explicitly labelled withdrawn/retracted records, selecting the newest available version before conversion so an older version is not substituted within the fetched window. The DOI link keeps its actual version while recommendation/state/RSS identifiers ignore `/vN`. Newer versions of already recommended papers do not generate a second recommendation.

These public metadata checks do not establish personal Zotero ingestion, LLM/SMTP delivery, complete live Nature catalogue coverage or online Actions/Pages deployment; the limitations above still apply. The live reports are stored outside application state in `/workspace/.setup/validation/preprints-*.json`.

## Review fixes — 2026-09-30

The follow-up review reproduced four defects using isolated fixtures: arXiv announcements delayed beyond a one-day submission window, distinct query-based article URLs sharing an identity, non-DOI Atom IDs masking DOI links, and one malformed Crossref record discarding valid records from the same journal. All four regression tests now pass.

Additional checks cover canonical DOI identity in Zotero exclusion and saved state, migration of old delivery keys, recovery from corrupt catalogue caches, explicit ISSNs overriding stale discoveries, unsafe RSS article links, HTML escaping inside RSS descriptions, and pending deliveries surviving new Zotero/ranking failures. Malformed journal entries preserve valid results while still reporting incomplete retrieval.

Validation performed after the fixes:

- Full suite, including actual local embedding inference: **164 passed, 0 failed, 0 skipped/deselected**. One OmegaConf warning concerns an existing empty environment-variable default.
- Default suite: **163 passed, 1 slow test deselected**.
- Full-suite source coverage: **88%** (journal retriever 85%, local reranker 97%).
- Ruff F401/F821/F841 checks passed for source/scripts and the added review regression file.
- Actionlint 1.7.7 passed for the daily, manual test and CI workflows; `git diff --check` passed.

Application credentials were excluded from the test environment. SMTP, Zotero and paid API transports used fixtures. The embedding model was downloaded anonymously into an isolated temporary cache and then tested offline. These checks do not establish real email receipt, Pages deployment or complete live publisher coverage.

The daily workflow now accepts an optional single-run `recipient` that overrides configured recipients without changing repository secrets or the scheduled recipient. It logs SMTP acceptance counts and generated RSS item counts; SMTP acceptance is not proof of inbox delivery. Future Test runs retain generated RSS as a `test-rss` artifact and have a 90-minute timeout. The existing Test run at the pre-fix commit is unaffected by these changes.

A formal daily workflow run with `output=both`, the explicitly confirmed recipient and the repaired branch is still required. At validation time, the saved environment's GitHub CLI API request returned `Forbidden`; the available connector could read run state but exposed no workflow-dispatch operation. No new live run or email was initiated by this review.


## Repository-wide redundancy audit — 2026-10-01

Reviewed all tracked source modules, scripts, tests/fixtures, workflows, configuration, documentation and declared dependencies, with an independent reference audit. Removed the unreachable non-budget prompt paths and GPT tokenizer, unused old empty-email renderer, unused affiliation mock response, retired Docker test servers and unsupported Docker deployment guide. Consolidated equivalent word normalization, sparse abstract decoding, Research Square DOI version identity and repeated cached journal construction. Removed unused imports/locals and superseded email previews; current preview render caveats are retained in `docs/previews/README.md`.

The shared scientific abstract decoder also repairs a reproduced Research Square defect: encoded/plain inequalities must survive (`T<Tc and x>0`), while formatting tags and missing-abstract placeholders are handled explicitly. Regression tests cover this intentional correction. Unicode identity normalization remains casefold plus word extraction, without new NFKC normalization or historical-key changes.

Removed runtime declarations `tiktoken`, `gitignore-parser` and `aiosmtpd`; the lock also drops unused `atpublic` and `attrs`. All retained dependency versions are unchanged. Kept registry imports, model/PEFT/full-text dependencies, public configuration aliases and compatibility fields, local RSS support, the no-spend affiliations method, delivery-state migration and every budget/privacy/error guard.

Before/after comparisons produced identical request payloads and complete Paper results in seven abstract/full-text/Unicode/fallback cases. Ruff F401/F821/F841 passed across source/scripts/tests, checksum-verified actionlint passed, and regenerated current synthetic HTML/plain-text previews were byte-identical. The complete suite passed in a fresh frozen environment: **418 tests, 92% source coverage**, including actual local embedding inference. No application credentials or real transports were used by tests.


## Single-column email and bounded publisher recovery

Desktop (1440px) and mobile (390px) browser checks confirm three vertically stacked, independently numbered groups with no horizontal overflow. Synthetic previews cover successful Chinese summaries (no original abstract repeated), failed summaries, missing abstracts, long affiliations, empty and short batches. Full abstracts and affiliations remain in internal data; email affiliation text is deterministically limited to the configured character count.

Real metadata checks on the ten previously missing abstracts identified two publisher cover items, now excluded by precise front/back-cover labels. Of the eight research papers, three abstracts were recovered through DOI-verified OpenAlex metadata and two through DOI-verified public Nature abstract sections. Nature's ordinary anonymous authorize/transit redirects are supported within four GETs and a 2MB page limit. Three APS abstracts exist publicly, but ordinary HTTP returned 403 in the verification environment; the implementation records access-blocked status and does not bypass it or substitute a title-matched preprint. Neither the probe nor previews modified delivery history or sent email.

The user-updated SiliconFlow key passed isolated validation run [36838849520](https://github.com/wankiwi/zot2dailypaper/actions/runs/36838849520): one Chinese sentence, 91 prompt tokens and 48 completion tokens (139 total), one paid request, and unchanged delivery-history blob containing 95 records. The existing whole-day-no-refund guard reserved CNY0.20 for the UTC day; no subsequent paid validation or email was triggered.


## APS metadata and cover eligibility follow-up

Cover labels are now filtered across all sources before ranking and quota assignment, including random and pending batches. Research titles discussing covers/surfaces without a publisher-label delimiter remain eligible. History records are not deleted.

A real Requests adapter regression test reproduces a 3MB redirect response. The publisher-only session prevents Requests from eagerly consuming redirect bodies while preparing `Response.next`; manual redirect responses are closed unread. DOI/host checks and final-page limits remain enforced.

Official PRL accepted/recent feeds returned HTTP200 and exact DOI metadata, but the descriptions are truncated excerpts. The configured input now uses the official feeds.aps.org domain and does not treat these excerpts as full abstracts. APS Harvest documentation supports metadata access but the tested item requires authorization; no credential or identity substitution is attempted.

Public indexed/manuscript alternatives require exact DOI and normalized title. arXiv search only locates up to three candidates, and a match must carry the DOI and an explicit version. Its single-connection spacing is at least three seconds. Live verification recovered the DOI-linked arXiv v2 abstract for 10.1103/9cdj-fp6x; it is labeled manuscript metadata, not a publisher original. Semantic Scholar was blocked in this execution environment and stopped without retry. The b28p-yb2t preprint without the related DOI remains rejected. Of eight previously missing research-paper abstracts, the live check now recovers six (three OpenAlex, two Nature, one arXiv v2); two remain unavailable here. No email, paid model call, or history write occurred.


The bounded four-provider comparison, including new Crossref/OpenAlex exact-DOI checks and clearly separated prior Harvest/Semantic Scholar evidence, is recorded in [APS metadata verification](APS_METADATA_VERIFICATION.md), with [machine-readable results](aps-metadata-verification.json). No production transport change was needed: all six new lookups returned HTTP 200 with matching DOI/title but no abstract.
