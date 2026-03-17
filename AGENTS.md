Project: WishSpark

Root directory:
/opt/wishspark

Stack

frontend
Next.js dashboard

backend
FastAPI server

database
Postgres (docker)

cache
Redis (docker)

proxy
Traefik


Main backend entrypoint
backend/app/main.py


Agent workflow

1 Read docs in /opt/wishspark/docs
2 Read CURRENT_STATE.md
3 Read NEXT_STEPS.md
4 Analyze backend/app/api
5 Propose minimal safe changes


Rules

Never break database models
Never change environment variables automatically
Never deploy automatically
Always explain modifications


Safe areas for modification

backend/app/services
backend/app/core
backend/app/api
dashboard/src


High risk areas

database models
infra
deployment configs

Context sources for agents

Primary rules
AGENTS.md

Stable project state
docs/CURRENT_STATE.md

Development roadmap
docs/NEXT_STEPS.md

Auto-generated technical context
SERVER_CONTEXT.md
docs/AUTO_CONTEXT.md
