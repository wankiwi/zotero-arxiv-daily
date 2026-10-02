# Research Square recovery and proposed Zotero email action

## Research Square diagnosis and verification

Scheduled run `36943099282` delivered 45 additional records; 14 were Research Square papers with empty abstracts. The OpenAlex records lacked `abstract_inverted_index`. Abstract recovery then queried their versionless deduplication DOI, for which Crossref returned 404. An exact-version check of `10.21203/rs.3.rs-11039691/v1` returned HTTP 200 with an abstract, whereas the versionless DOI returned 404. Its OpenAlex record returned HTTP 200 without an abstract.

Recovery now uses the versioned DOI stored on each paper. Both DOI/version and normalized title must match. Crossref is tried before OpenAlex; unknown versions are not guessed. The existing bounded, streaming metadata transport limits responses to 2 MB, serializes requests at least one second apart, sets connection/read timeouts of 5/20 seconds, rejects redirects, performs no retries, and stops a provider after HTTP 401/403/429. Selected-paper `abstracts.max_papers` still bounds the work. Missing metadata stays visible in logs/status.

Live verification of all 14 affected papers recovered 14 abstracts from Crossref (953–2787 characters after markup cleaning). Provenance points to the exact-version Crossref metadata URL. Verification used detached in-memory papers; no saved history, ranking, delivery, budget, or runtime configuration was changed. The stable versionless DOI remains the deduplication identity, so this fix does not re-email older versions.

## Default-off email confirmation interface

`email.zotero_action_origin` defaults to `null`. If a future approved service is configured, use an HTTPS origin such as `https://papers.example.org` (no path, user information, query, or fragment). Email cards then link to `/zotero/confirm?paper=<public-paper-id-hash>` with an explicit login/confirmation label. The hash is a public deterministic lookup identifier, **not authorization**. No Zotero key, GitHub token, collection name/key, or bearer capability is included. HTML/plain-text rendering is tested; existing single-column and summary behavior remains intact.

There is no deployed service or Zotero-write implementation in this change. Keep this setting disabled until the architecture and permissions have been approved and the following server contract has been implemented and tested:

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
