# Repository rename compatibility audit

The existing GitHub repository is renamed from `wankiwi/zotero-arxiv-daily` to
`wankiwi/zot2dailypaper`. Its repository ID remains `970666351`, its default branch
is `main`, and its upstream remains `TideDra/zotero-arxiv-daily`. This is a rename
of the existing fork, not a replacement repository.

## Code and authentication

The distribution, Python module and console command are `zot2dailypaper`.
Imports, log namespaces, HTTP User-Agent values, workflow module/coverage commands
and links to this fork follow that name. Upstream attribution, funding links and
the AGPL license retain their original identity. Dependency versions are unchanged.
The root `config/` remains authoritative for checkout runs; installed-wheel runs
receive an explicit absolute `--config-path` to deployment-managed configuration.

Private-cache authorization checks use only `wankiwi/zot2dailypaper`, `main` and
the permitted events. Old repository names and other forks are not trusted.
Existing gh authentication is used; no credential value, OAuth callback, token,
Actions secret or permission is changed. External integrations that match a slug
should update to the new URL; repository-ID-based integrations retain their identity.

## Durable identities deliberately retained

- `paper-state`, `recommendations.json`, `journal_catalog.json`, `feed.xml` and
  `llm_budget.json`, all state schemas and stable paper IDs.
- `embedding-private-v1-` and `embedding-validation-v1-`, `.embedding-cache/private.enc`,
  vector namespace inputs, cache format and magic bytes.
- AES-GCM associated data **exactly**
  `wankiwi/zotero-arxiv-daily:private-embedding-cache:v1` for both sealing and
  unsealing. This is a durable format identity, not the current authorization slug.
  Renaming it or rotating a key would invalidate existing ciphertext.
- Workflow filenames, the `daily-papers` concurrency group, schedules, quotas,
  scoring, source filters, SMTP, RSS-off policy and Zotero confirmation IDs.
- Existing `CUSTOM_CONFIG`, daily CNY0.30 policy, Asia/Singapore budget dates,
  v2 ledger and all legacy UTC reservations. No bootstrap, restore-to-old-snapshot
  or budget reset is needed.

## Rename-to-merge transition

The repository rename is an administrative action; code changes remain in a draft
PR until separately approved. The old `main` still compares `github.repository`
and `GITHUB_REPOSITORY` with the old slug. A run before this PR is merged would
skip cross-run encrypted embedding cache restore/save and recompute vectors;
the existing encrypted caches, history and budget are preserved. Its synthetic
cache validation jobs would also skip. Merge this reviewed PR before the next
daily run to restore the cache gate under the new name. No production workflow
is dispatched and no schedule is changed by this task.

## Zotero Site: required follow-up, not deployed here

The existing owner-private Site is
`https://zotero-paper-confirmation.kiwiwan.chatgpt.site`.
Its `lib/security.ts` binds encrypted credentials to Site and owner, independent
of the GitHub repository name; preserve both identities and all stored credentials.

In the Site's `app/api/[action]/route.ts`, the `SOURCE` constant currently reads:

```text
https://raw.githubusercontent.com/wankiwi/zotero-arxiv-daily/refs/heads/paper-state/recommendations.json
```

The exact follow-up change is:

```text
https://raw.githubusercontent.com/wankiwi/zot2dailypaper/refs/heads/paper-state/recommendations.json
```

Only that URL needs updating and rebuilding/redeploying after approval. The Site,
origin, login client, collection selection, encryption key/AAD and stored Zotero
credentials must stay intact. The old and new public digest endpoints are checked
after the rename without printing or saving paper content. No Site deployment or
live Zotero write is performed here.

GitHub's [rename documentation](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)
describes web/Git redirects. Do not create a repository at the old name; that
would remove its redirects. RSS/Pages output remains disabled.

## Verification on 2026-10-07

- Repository ID/fork/parent/default branch unchanged; main remains
  `b122c87707313e6a14244153a8b0a07ecc535c22` and `paper-state` remains
  `faa3a27cf7127e72b911c228d94c9aa5b98b0dfd` across the rename.
- All four state-file Git blob hashes, 13 existing cache IDs/keys/refs/sizes,
  13 secret names and the Actions variable name/update timestamp are unchanged.
  Secret values and variable values were not read or changed.
- All 1009 tests pass locally, including the cached-model test (offline), with
  91% coverage. A further 83 entry-point/cache/workflow tests pass after renaming.
- A clean frozen-lock environment and installed wheel pass all 43 module imports,
  metadata, console and module help checks with explicit deployment configuration.
  Ruff F401/F821/F841, actionlint 1.7.12, diff checks and local document links pass.
- Default configs, protected budget/state/ranking/Zotero code and dependency pins
  match PR14 except namespace changes; the exact deployed cache AAD is tested.
