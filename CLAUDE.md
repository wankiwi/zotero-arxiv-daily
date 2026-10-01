# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero-arXiv-Daily recommends new journal, arXiv/bioRxiv/medRxiv, Research Square and OpenReview papers based on a user's Zotero library. It computes embedding similarity between new papers and the user's existing library, generates TLDRs via LLM, and delivers results by email. Designed to run in GitHub Actions. LLM calls use a durable CNY0.20 daily estimated budget; stale prices warn and continue under the configured policy.

## Commands

```bash
# Run the application
uv run src/zotero_arxiv_daily/main.py

# Run tests (excludes slow tests by default)
uv run pytest

# Run all tests including slow ones
uv run pytest -m ""

# Run a single test
uv run pytest tests/test_utils.py::TestGlobMatch -v

# Install/sync dependencies
uv sync
```

Static checks: `uvx ruff check --select F401,F821,F841 src scripts tests`; use actionlint for workflow YAML.

## Architecture

The app follows a linear pipeline orchestrated by `Executor` (`src/zotero_arxiv_daily/executor.py`):

1. **Fetch Zotero corpus** — retrieves user's library papers via pyzotero API
2. **Filter corpus** — applies `include_path` and `ignore_path` glob patterns without broadening their scope
3. **Retrieve new papers** — fetches configured journals, arXiv, bioRxiv/medRxiv, Research Square and OpenReview sources
4. **Rerank** — scores candidates by weighted similarity to corpus (newer Zotero papers weighted higher)
5. **Generate Chinese one-sentence TLDRs** — via the budget-guarded OpenAI-compatible API; affiliations come only from metadata
6. **Render + send email** — HTML email via SMTP

### Plugin Systems

**Retrievers** (`src/zotero_arxiv_daily/retriever/`): Register via `@register_retriever` decorator, discovered by `get_retriever_cls()`. Each retriever implements `_retrieve_raw_papers()` and `convert_to_paper()`.

**Rerankers** (`src/zotero_arxiv_daily/reranker/`): Register via `@register_reranker` decorator, discovered by `get_reranker_cls()`. Two implementations: `local` (sentence-transformers) and `api` (OpenAI-compatible embeddings endpoint).

### Configuration

Uses Hydra + OmegaConf. Presets inherit `config/base.yaml`; `default` selects `all`, and the scheduled workflow applies the `interests` profile plus sanitized runtime overrides. `legacy` retains the original configuration entry point. Environment variables are interpolated via `${oc.env:VAR_NAME,default}` syntax. Entry point uses `@hydra.main`.

### Data Classes

`Paper` and `CorpusPaper` in `src/zotero_arxiv_daily/protocol.py`. `Paper.generate_tldr` requires a reserved `BudgetRequests` guard. `generate_affiliations` remains a no-spend compatibility method returning existing metadata.

## Testing

Tests marked `@pytest.mark.slow` require heavy dependencies (e.g., sentence-transformers model download) and are skipped locally by default (`addopts = "-m 'not slow'"` in pyproject.toml). All other tests run with pure Python stubs (no Docker containers needed).

```bash
# Run tests (excludes slow tests)
uv run pytest

# Run all tests including slow ones
uv run pytest -m ""

# Run with coverage
uv run pytest --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

If gstack skills aren't working, run `cd .claude/skills/gstack && ./setup` to build the binary and register skills.

## Git Workflow

- PRs target `main` unless the user specifies another target.
- Preserve delivery history and budget state; never use test runs to send real email without explicit authorization.
