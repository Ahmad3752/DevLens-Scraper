# Job Board Scraper

**Multi-board job scraper for Pakistan**

Scrape LinkedIn,Rozee,Mustakbil and Indeed for job listings in Pakistan. Cleans and enriches each posting, deduplicates via Redis and upserts everything to Supabase.

---

## Table of Contents

- [Overview](#overview)
- [Pipeline](#pipeline)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Deployment](#deployment)
- [Roles Covered](#roles-covered)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

`main.py` iterates over a configured list of permitted roles and runs the full pipeline for each one using a configurable number of parallel workers (default: sequential). The spider fetches listing pages and individual job postings using Scrapling's `StealthyFetcher` which handles Cloudflare challenges and anti-bot detection. Each parser extracts structured fields from the board's HTML. Roles that fail during the main pass are retried once sequentially after all other roles complete.

New jobs are checked against a Redis processed set so duplicates are never written twice. Unique jobs pass through the enricher which cleans the description, matches skills against a master Excel list, parses experience level and year ranges, normalises salary strings and detects education and job type. Enriched records are upserted to Supabase in batches of 200.

A separate `digital_scout_node` in `pipeline/scout.py` handles interactive, query-driven scraping with role-allowlist enforcement. It is not called by `main.py` but is available for integration into a wider agent workflow.

## Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  Permitted roles (PERMITTED_ROLES_1 / _2 env var)       │
└──────────────────────────┬──────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    │ Worker Pool │
                    └──────┬──────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Spider  (scraper/spider.py)                            │
│  StealthyFetcher with Cloudflare bypass                 │
│  One parser per board - LinkedIn and Indeed             │
│  Each with adaptive CSS selectors                       │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Redis Deduplication  (services/redis.py)               │
│  SHA-256 job ID checked against processed set           │
│  TTL-based expiry (default 24 h)                        │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Enricher  (pipeline/enricher.py)                       │
│  Description cleaning + section splitting               │
│  Skill extraction from master Excel list                │
│  Experience level + year-range parsing                  │
│  Salary normalisation + education/job-type detection    │
│  Enrichment confidence score per job                    │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Supabase Upsert  (services/supabase.py)                │
│  Batched upsert on conflict (job_id)                    │
│  200 records per batch                                  │
└─────────────────────────────────────────────────────────┘
```

---

## Features

- **Multi-board scraping** — LinkedIn, Indeed, Rozee, and Mustakbil with two alternating role sets
- **Anti-bot bypass** — Scrapling `StealthyFetcher` with Cloudflare solver and headless/headful fallback
- **Role allowlist** — Only scrapes roles listed in `SCRAPER_ROLE_KEYS` - rejects everything else
- **Redis deduplication** — SHA-256 job IDs tracked in a TTL-based Redis set; duplicates never re-processed
- **Description cleaning** — HTML entity decoding, whitespace normalisation, section splitting, sentence deduplication
- **Skill extraction** — Word-boundary regex matching against a configurable master Excel skill list
- **Experience parsing** — Detects level (entry/junior/mid/senior/lead) and min/max year ranges
- **Salary normalisation** — Parses currency, amount range and period from free-text salary strings
- **Parallel workers** — Configurable thread pool for scraping multiple roles simultaneously
- **Failed role retry** — Roles that fail during the main pass are retried once sequentially after all others complete
- **Batched upsert** — Supabase upsert in configurable batches with conflict resolution on `job_id`
- **Enrichment confidence** — Scores each job 0–1 based on how much structured data was extracted
- **Stale job cleanup** — Marks jobs older than a configurable number of days as inactive

---

## Tech Stack

- **Scraping** — Scrapling 0.4.7 (`StealthyFetcher`)
- **Parsers** — Per-board CSS selector parsers with adaptive fallback chains
- **Enrichment** — pandas, regex, openpyxl
- **Deduplication / Queue** — Redis 7.4
- **Database** — Supabase (PostgreSQL via `supabase-py`)
- **State Management** — LangChain Core (message types)
- **API** — FastAPI with uvicorn
- **Runtime** — Python 3.12+, uv package manager

---

## Prerequisites

- Python 3.12+
- Redis (local or remote; Upstash recommended for production)
- Supabase project with service role key
- Scrapling fetcher extras (installs Playwright/Camoufox automatically via `scrapling[fetchers]`)
- Master skill list Excel file (default: `data/skills_master.xlsx`)

---

## Getting Started

```bash
git clone https://github.com/Ahmad3752/Scrapling-Job-Boards-Scrapper.git
cd Scrapling-Job-Board-Scrapper

# Install dependencies
uv sync

# Download Playwright browser binaries (required for StealthyFetcher)
uv run playwright install

# Copy and fill in environment variables
cp .env.example .env
```

Edit `.env` with your Supabase credentials and Redis URL, then run:

```bash
uv run python main.py
```

The scraper will run through all permitted roles and insert unique jobs into Supabase. It runs locally in sequential mode by default. Configure `JOB_SCRAPING_WORKERS` to run multiple roles in parallel.

---

## Deployment

### GitHub Actions (Recommended)

The scraper is deployed via **GitHub Actions** with scheduled runs every 12 hours. Each run:
- Scrapes all configured boards (LinkedIn, Indeed, Rozee, Mustakbil)
- Processes all permitted roles
- Deduplicates against Redis processed set (24-hour TTL)
- Enriches and upserts to Supabase in batches of 200

**Setup:**

1. Push the repo to GitHub
2. Go to **Settings → Secrets and variables → Actions** and add these secrets:

| Secret | Example |
|:---|:---|
| `SUPABASE_URL` | `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Your service role key |
| `SCRAPER_REDIS_URL` | `rediss://default:password@upstash-endpoint:6379` |
| `JOB_SCRAPING_BOARDS` | `linkedin,indeed,rozee,mustakbil` |
| `JOB_SCRAPING_MAX_PAGES_PER_BOARD` | `1` |
| `JOB_SCRAPING_MAX_JOBS_PER_BOARD` | `15` |
| `JOB_SCRAPING_WORKERS` | `2` |
| `JOB_SCRAPING_FETCH_TIMEOUT_MS` | `60000` |
| `JOB_SCRAPING_DOWNLOAD_DELAY` | `2.0` |
| `JOB_STALE_AFTER_DAYS` | `7` |

3. GitHub Actions will automatically run the scraper every 12 hours based on the workflow schedule in `.github/workflows/scrape.yml`.

### Local Deployment

For testing and development:

```bash
# Run once locally
uv run python main.py

# For continuous monitoring, wrap with cron:
# 0 */12 * * * cd /path/to/Scrapling-Job-Boards-Scrapper && uv run python main.py
```

### FastAPI Web Service

The repo includes a FastAPI web service (`app.py`) for integrating with external schedulers or dashboards:

```bash
uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

Available endpoints:

- `GET /healthz` — Health check
- `GET /run-scraper` — Trigger scraper manually
- `GET /scraper-status` — Get scraper status

---

## Roles Covered

The scraper supports 8 canonical developer roles configured via `SCRAPER_ROLE_KEYS`:

- **backend** — Backend Developer
- **frontend** — Frontend Developer
- **full_stack** — Full Stack Developer
- **mobile** — Mobile Developer
- **ai_ml** — AI/ML Engineer
- **devops** — DevOps Engineer
- **data_engineer** — Data Engineer
- **qa_automation** — QA Automation Engineer

Each role is scraped across all configured boards (LinkedIn, Indeed, Rozee, Mustakbil) every 12 hours. The role mapping is defined in `core/devlens_roles.py` and can be customized by updating environment variables.

---

## Project Structure

```
main.py                    ← Entry point: runs full pipeline for all permitted roles
app.py                     ← FastAPI web service for manual triggering and status checks
pyproject.toml             ← Dependencies (managed with uv)
.env.example               ← Environment variable template

core/
  devlens_roles.py         ← Role label mapping and query generation
  settings.py              ← Env-backed settings singleton
  state.py                 ← AgentState and JobData TypedDicts
  role_filters.py          ← Role allowlist enforcement helpers

scraper/
  spider.py                ← Multi-board scrape coordinator (JobScraperSpider)
  normalization.py         ← Job post normalization utilities
  boards/
    base.py                ← BaseJobParser ABC with shared utilities
    linkedin.py            ← LinkedIn parser
    indeed.py              ← Indeed parser
    rozee.py               ← Rozee parser
    mustakbil.py           ← Mustakbil parser

pipeline/
  enricher.py              ← JobEnricher: cleaning, skill/experience/salary extraction
  enricher_node.py         ← LangChain node wrapper around JobEnricher
  scout.py                 ← digital_scout_node: query-driven scraping with role guard

services/
  redis.py                 ← Redis queue and deduplication service
  supabase.py              ← Supabase CRUD operations

data/
  job_cache/               ← Cached job data (e.g., ai_ml.json)
  skills_master.xlsx       ← Master skill list for enrichment

tests/
  test_*.py                ← Unit tests for core modules

static/
  index.html, styles.css, app.js  ← Frontend dashboard
```

---

## Contributing

1. Open an issue describing the bug or feature before starting any work
2. Fork the repo and create a branch from `main`
3. Make your changes and reference the issue in your PR
4. Run tests locally: `uv run pytest tests/`
5. Submit a pull request for review

---


