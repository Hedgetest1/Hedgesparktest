# WishSpark Current State

Root
/opt/wishspark


## Stack

Frontend
Next.js dashboard
PM2 process: wishspark-dashboard

Backend
FastAPI
PM2 process: wishspark-backend
Port: 8000

Database
Postgres (docker)

Cache
Redis (docker)

Proxy
Traefik


## System Architecture

Next.js dashboard
    ↓
FastAPI backend
    ↓
Services / Engines
    ↓
Postgres + Redis


## Backend API modules

agent
ai_actions
conversion_probability
dashboard
decision_engine
events
intent
live_alerts
live_opportunities
live_visitors
market_lookup
opportunities
price_intelligence
revenue_actions
revenue_radar
top_pages
track
tracker
visitor_scores
weekly_trend


## Notes

Technical context is auto-generated in:

SERVER_CONTEXT.md
docs/AUTO_CONTEXT.md

Project rules for agents are defined in:

AGENTS.md
