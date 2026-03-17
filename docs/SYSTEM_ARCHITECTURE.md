# WishSpark System Architecture

## Purpose

WishSpark is not designed as a simple Shopify widget or single dashboard.

It is designed as a commerce intelligence system with two connected layers:

1. a Shopify-facing product layer for merchants
2. an internal intelligence and orchestration layer for analytics, signals, automations, and future AI-managed operations


## Current live architecture

Root path:

/opt/wishspark

Main components:

- dashboard
- backend
- docs
- infra
- logs
- tracker
- widget

Runtime stack:

- Frontend: Next.js
- Backend: FastAPI
- Process manager: PM2
- Database: Postgres (docker)
- Cache: Redis (docker)
- Proxy: Traefik


## Current request flow

User / Merchant
↓
Next.js dashboard
↓
FastAPI backend
↓
API routers
↓
services / engines
↓
Postgres + Redis

This is the current live serving architecture.


## Backend architecture

Main backend entrypoint:

backend/app/main.py

The backend is structured in layers:

- api
- services
- core
- models
- schemas
- system
- utils

Current product-specific engines already present include:

- conversion_probability_engine
- price_radar_engine
- external_lookup_engine

Current API surface already includes modules such as:

- agent
- ai_actions
- conversion_probability
- dashboard
- decision_engine
- events
- intent
- live_alerts
- live_opportunities
- live_visitors
- market_lookup
- opportunities
- price_intelligence
- revenue_actions
- revenue_radar
- top_pages
- track
- tracker
- visitor_scores
- weekly_trend


## Frontend architecture

Main frontend app:

dashboard

Current role of the frontend:

- merchant-facing dashboard
- live and semi-live analytics interface
- presentation layer for opportunities, alerts, pricing signals, and insights

The frontend must progressively become the main operating UI for Lite and Pro plans.


## Architectural target

The target architecture is larger than the current live stack.

WishSpark should evolve toward this system:

Merchant / User
↓
Dashboard UI / Shopify Embedded App
↓
API Layer
↓
Business Logic Layer
↓
Intelligence Layer
↓
Worker / Batch Layer
↓
Data Layer
↓
Internal Agent Layer

Target major blocks:

- dashboard
- shopify embedded app
- backend API
- intelligence services
- batch workers
- scheduler / cron jobs
- AI routing layer
- internal agent layer
- database / cache
- logs / observability


## Planned architectural expansion

### 1. Shopify product layer

This is the merchant-facing layer.

It will include:

- embedded app experience
- merchant configuration
- alerts
- pricing recommendations
- opportunity summaries
- plan-based feature access

This layer must remain simple, fast, and low-friction.


### 2. Intelligence layer

This is the analytical layer.

It will process:

- pricing data
- visitor behavior
- product signals
- market lookup
- opportunities
- trends
- decision signals

This layer turns raw data into structured signals.


### 3. Worker and batch layer

This layer is not primarily user-facing.

It exists to run heavy and repeated jobs outside live requests.

It will be used for:

- background analysis
- signal recalculation
- periodic enrichment
- scheduled intelligence generation
- future competitor and market scans
- future automation preparation

This layer is essential for cost control and scalability.


### 4. Internal agent layer

This layer is for AI-driven project and system operations.

It is separate from merchant-facing AI.

Its future role includes:

- system reading
- codebase understanding
- architecture inspection
- backend analysis
- next-step planning
- safe implementation support
- debugging support

This layer must operate with rules, context, and guardrails.


## Core architectural principle

Not everything should happen live.

WishSpark must use a hybrid model:

- live responses for fast user-facing interactions
- offline or batch processing for heavy computation
- scheduled updates for intelligence refresh
- agent-assisted operations for development and system improvement

This principle is required both for scalability and future self-management.


## Live vs offline design rule

### Live layer

Use live execution only for:

- dashboard reads
- health checks
- user-triggered lightweight actions
- small summaries
- immediate API responses

### Offline / batch layer

Use offline or scheduled execution for:

- intelligence recomputation
- large scans
- trend generation
- pricing analysis refresh
- future competitor enrichment
- future heavy AI operations

This separation is a core system rule.


## Agent-readable architecture

The system must always remain readable by AI agents.

For that reason the project must maintain:

- AGENTS.md
- docs/CURRENT_STATE.md
- docs/NEXT_STEPS.md
- SERVER_CONTEXT.md
- docs/AUTO_CONTEXT.md

These files are part of the architecture, not just documentation.


## Immediate architectural priorities

1. stabilize API and dashboard integration
2. add worker layer
3. add scheduler / batch jobs
4. define AI routing layer
5. prepare Shopify embedded app
6. prepare internal agent orchestration
7. improve observability and safe automation


## Non-goals for now

The following are not immediate production goals:

- full autonomous code deployment
- uncontrolled self-modifying agents
- direct production changes without review
- large irreversible database changes by agents

Autonomy must grow gradually and safely.


## Summary

WishSpark current architecture is already a functioning SaaS base.

Its target architecture is a layered commerce intelligence system built for:

- merchant value
- scalable analytics
- batch intelligence
- future AI-assisted operations
- future semi-autonomous system management
