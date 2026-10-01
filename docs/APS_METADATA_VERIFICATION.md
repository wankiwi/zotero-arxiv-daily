# APS metadata provider verification

Checked 2026-10-01 at 10:13 UTC from the existing WSL Ubuntu-22.04 environment, against main `3607e4b`. Six unauthenticated GETs total: one per DOI per Crossref/OpenAlex provider, no retries or redirects. All six responses matched the exact requested DOI and normalized title. None supplied an abstract or an abstract version.

| DOI | Crossref REST (new check) | OpenAlex (new check) | Semantic Scholar (earlier evidence) | APS Harvest (earlier evidence) |
|---|---|---|---|---|
| `10.1103/b28p-yb2t` | HTTP 200; no abstract | HTTP 200; no abstract | Prior independent lookup: HTTP 200, null abstract; not rechecked | Not requested after known access denial; availability unknown |
| `10.1103/k9sp-h4c3` | HTTP 200; no abstract | HTTP 200; no abstract | Prior independent lookup: HTTP 200, complete indexed abstract; DOI/title matched, version unverified. WSL provider subsequently reported blocked access; exact earlier status was not retained | Earlier unauthenticated HTTP 401; not repeated |
| `10.1103/9cdj-fp6x` | HTTP 200; no abstract | HTTP 200; no abstract | Prior independent lookup: HTTP 404; not rechecked | Not requested after known access denial; availability unknown |

Earlier Semantic Scholar observations were supplied by independent research in the same task, not reproduced by this six-request check. The WSL block could have been 401, 403, or 429; do not misreport it specifically as rate limiting. Current transport now logs the exact status and stops the provider on all three. No network switch, proxy, browser impersonation, credential extraction, or blocked endpoint retry was used for this verification. An Actions network probe was deliberately not dispatched while that denial remains unresolved.

## What can recover these abstracts

The existing implementation already supports exact-DOI Crossref and OpenAlex recovery, followed by DOI-and-title-verified Semantic Scholar for APS and strictly DOI-linked/versioned arXiv manuscripts. A successful metadata lookup with a null abstract is missing source data, not a transient failure that extra retries can fix. No production integration or retry changes were justified by this check.

`9cdj-fp6x` already has a verified DOI-linked arXiv v2 manuscript fallback (`https://arxiv.org/abs/2605.13226v2`), explicitly labeled as a manuscript. That does not establish an APS publisher-version abstract. The older title-similar preprint for `b28p-yb2t` has a materially different abstract and no verified DOI link and remains rejected. Feed excerpts remain excluded as full abstracts. `k9sp-h4c3` has promising indexed data in earlier Semantic Scholar evidence, but delivery from the production path remains unverified while access is blocked.

## Harvest access prerequisite

The [official Harvest documentation](https://harvest.aps.org/docs/harvest-api) allows anonymous JSON access to some articles, such as certain open-access articles; it does not promise anonymous access to every DOI. It also supports APS-issued API tokens or authorized IP ranges. Article JSON contains `data.abstract.value`, with article identifiers available for DOI verification. The known HTTP 401 is an access/entitlement failure, not evidence that the article has no abstract.

Repository secret **names only** were inspected: no dedicated APS Harvest or Semantic Scholar credential was configured. This does not establish whether the user has institutional entitlement elsewhere. To proceed with denied Harvest resources, request access from `help@aps.org` as directed by the [Harvest homepage](https://harvest.aps.org/), specifying the metadata/abstract use case and these DOIs. After APS grants access, a dedicated credential can be configured securely; credentials must not be pasted into issues or public configuration. No new credential was requested, read, or stored by this check.

This check does not alter delivery history, runtime configuration, RSS settings, schedules, or the daily LLM budget. It sends no mail and performs no LLM or paid-service call.
