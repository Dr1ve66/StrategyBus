# Workspace

## Overview

This workspace now includes a standalone Python Flask web application in `flask_ai_app/` that implements the Russian-language two-agent AI review flow requested by the user. The original pnpm workspace artifacts remain present for the shared API server and canvas sandbox.

## Flask AI App

- **Location**: `flask_ai_app/`
- **Stack**: Python 3.11, Flask, Flask-SQLAlchemy, SQLite, Jinja2, Bootstrap 5 CDN, OpenAI Python SDK, ReportLab PDF generation
- **Run command**: `python flask_ai_app/app.py`
- **Port handling**: Reads `PORT` from the environment and defaults to `5000`
- **Database**: SQLite at `flask_ai_app/data/app.db` by default; tables are created automatically with `db.create_all()` at startup
- **Required secrets/env vars**:
  - `OPENAI_API_KEY` for AI generation
  - `APP_PASSWORD` for the `admin` login password
  - `SECRET_KEY` for Flask sessions; falls back to `SESSION_SECRET` when present
- **Optional prompt env vars**:
  - `PROMPT_A` overrides the Agent 1 system prompt
  - `PROMPT_B` overrides the Agent 2 system prompt

## Current AI Flow

- Agent 1 and Agent 2 prompts request from 1 to 10 JSON items.
- Users can select multiple cards at both stages; accepted cards are combined into a single saved selection/final result for downstream compatibility with the existing schema.
- Users can add a custom card at Agent 1 or Agent 2 via modal forms. Custom cards are saved as accepted immediately.
- Rejecting all currently active cards automatically requests a new AI batch and reloads the page.
- The result page includes a PDF download route with Cyrillic font support.
- The embedded Replit preview uses SameSite=None cookies plus `_auth` URL token propagation for iframe session persistence.

## Flask Routes

- `/login` — admin login
- `/` — primary textarea input form
- `/process` — creates a user input and runs Agent 1
- `/review/<input_id>` — Agent 1 review cards grouped by round
- `/more/<input_id>` — requests another Agent 1 round
- `/item1/accept/<response_id>`, `/item1/reject/<response_id>`, `/item1/save/<response_id>`, `/item1/custom/<input_id>` — Agent 1 actions
- `/continue/<input_id>` — saves accepted Agent 1 items and moves to Agent 2
- `/agent2/<selected_id>` — Agent 2 generation and review
- `/item2/accept/<response_id>`, `/item2/reject/<response_id>`, `/item2/save/<response_id>`, `/item2/custom/<selected_id>` — Agent 2 actions
- `/agent2/finish/<selected_id>` — saves accepted Agent 2 items as the final result
- `/result/<selected_id>` — final side-by-side result
- `/result/<selected_id>/pdf` — PDF export of the final result
- `/history` — request history
- `/logout` — clears session

## Existing Workspace Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Key Commands

- `python flask_ai_app/app.py` — run the Flask AI app
- `pnpm run typecheck` — full TypeScript typecheck across all packages
- `pnpm run build` — typecheck + build all TypeScript packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally
