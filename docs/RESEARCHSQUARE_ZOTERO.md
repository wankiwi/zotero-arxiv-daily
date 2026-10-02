# Research Square recovery and proposed Zotero email action

## Research Square diagnosis and verification

Scheduled run `36943099282` delivered 45 additional records; 14 were Research Square papers with empty abstracts. The OpenAlex records lacked `abstract_inverted_index`. Abstract recovery then queried their versionless deduplication DOI, for which Crossref returned 404. An exact-version check of `10.21203/rs.3.rs-11039691/v1` returned HTTP 200 with an abstract, whereas the versionless DOI returned 404. Its OpenAlex record returned HTTP 200 without an abstract.

Recovery now uses the versioned DOI stored on each paper. Both DOI/version and normalized title must match. Crossref is tried before OpenAlex; unknown versions are not guessed. The existing bounded, streaming metadata transport limits responses to 2 MB, serializes requests at least one second apart, sets connection/read timeouts of 5/20 seconds, rejects redirects, performs no retries, and stops a provider after HTTP 401/403/429. Selected-paper `abstracts.max_papers` still bounds the work. Missing metadata stays visible in logs/status.

Live verification of all 14 affected papers recovered 14 abstracts from Crossref (953–2787 characters after markup cleaning). Provenance points to the exact-version Crossref metadata URL. Verification used detached in-memory papers; no saved history, ranking, delivery, budget, or runtime configuration was changed. The stable versionless DOI remains the deduplication identity, so this fix does not re-email older versions.

## Default-off email confirmation interface

`email.zotero_action_origin` defaults to `null`. If a future approved service is configured, use an HTTPS origin such as `https://papers.example.org` (no path, user information, query, or fragment). Email cards then link to `/zotero/confirm?paper=<public-paper-id-hash>` with an explicit login/confirmation label. The hash is a public deterministic lookup identifier, **not authorization**. No Zotero key, GitHub token, collection name/key, or bearer capability is included. HTML/plain-text rendering is tested; existing single-column and summary behavior remains intact.

The user approved the authenticated confirmation architecture. An undeployed, mock-tested Python save core is now included; it is not wired to an HTTP listener or credentials. Keep the email setting disabled until a specific collection, hosting identity adapter, runtime credential handoff, and deployment have been verified. The server contract is:

1. Every GET is read-only, including requests from email link scanners. Require authenticated user sessions and authorize that user against the configured library before showing a paper.
2. Resolve only identifiers from the approved digest metadata. Never fetch arbitrary URLs from query parameters. Render an escaped preview of citation, original URL, and the server-configured target collection.
3. Save only on a deliberate authenticated POST, with session-bound CSRF protection, Origin validation, Secure/HttpOnly/SameSite cookies, and no state-changing GET route.
4. Enforce the target collection key server-side; do not accept arbitrary library/collection identifiers from the browser. An API credential may grant personal-library-wide writes, so this restriction must be enforced by the service.
5. Deduplicate against existing Zotero items by stable identifiers, add existing items to the collection without replacing other memberships, and use durable idempotency plus conflict-safe writes. Reconcile ambiguous responses before retrying.
6. Store credentials only server-side. Do not upload PDFs, broaden permissions, create credentials/collections, or deploy without the user's approval. Avoid third-party analytics and set `Referrer-Policy: no-referrer` on the confirmation page.

A lower-setup alternative is opening the paper and using the official Zotero Connector or mobile share flow with explicit collection selection. A normal email URL cannot guarantee placement in a chosen Zotero collection.

## Read-only permission and collection inspection

The manual Test workflow has a `zotero_readonly` mode. Supply `collection_name_sha256` as the SHA-256 of the exact target collection name; no collection name is included in public examples. The existing `ZOTERO_KEY` and `ZOTERO_ID` stay inside the runner. The script uses header authentication to `GET /keys/current`, verifies the configured owner, and paginates collections with a fixed bound. Output includes only personal-library permission booleans and the target-match count, never keys, IDs, names, bodies, or exception representations. Duplicate collection names are reported as ambiguous. No write API is present.

References: [Zotero read API](https://www.zotero.org/support/dev/web_api/v3/basics), [write API](https://www.zotero.org/support/dev/web_api/v3/write_requests).

## Validation outcome

The isolated runner inspection (`36962803688`) succeeded. The configured key owner matched; personal-library `library`, `notes`, `write`, and `files` flags were true. **Two collections matched the requested name**, so no target was selected and the email action must remain disabled until a specific collection is identified privately. Existing permission does not authorize this change to perform a live write. No credential was exported and the inspection performed zero library writes.

The initial draft passed 529 isolated tests and CI `36962773751`; an additional regression now rejects incomplete collection pagination rather than misreporting absence. Current delivery history contains 140 records (including the 45 from the latest scheduled run); verification must preserve that current state, not restore the older 95-record snapshot.

## Save core and hosting boundary

`zotero_save.py` implements an authenticated-session contract, same-origin POST plus session CSRF validation, trusted-digest-only paper lookup, bounded exact-identity Zotero search, collection-only PATCH preserving other memberships, citation creation with original URL and no attachments, and item/library version conflict guards. Durable SQLite reservations precede remote writes. Repeated successful POSTs return the recorded item; ambiguous responses are reconciled by reads and never blindly repeated. No arbitrary collection or library ID is accepted from a request. A host must supply verified sessions from its own authenticated session store; the Python dataclass is not an HTTP authentication mechanism.

A separate owner-private Sites application has now been built with ChatGPT sign-in, D1 and a server-only encryption secret. Its user-operated HTTPS setup form validates a personally submitted Zotero key and encrypts it with AES-GCM, a unique nonce, and owner/Site associated data. No agent retrieves or handles the Zotero key. HKDF separates storage and CSRF keys. The owner must explicitly choose among same-name collections using their full paths, keys and counts. There is no automatic first-match choice or new collection creation.

The Sites Worker implementation uses atomic D1 claims, authenticated same-origin POST with CSRF, exact-identity deduplication, version-conditional Zotero writes, and read-only reconciliation after ambiguous responses. Type checking, security tests using synthetic data, and the production Worker build passed. User setup and a deliberate live save are still required before enabling the email origin. The main repository and current runtime configuration remain unchanged while this PR is a draft.

Final repository validation: 552 isolated tests passed (one slow model test deselected), Ruff and actionlint passed, and CI `36963502379` passed. The read-only inspection remains run `36962803688`; no second inspection, email, paid model call, or live library write was performed.
