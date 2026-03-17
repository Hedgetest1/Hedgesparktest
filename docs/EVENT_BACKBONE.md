# WishSpark Data and Event Backbone

Purpose

The event backbone connects all parts of the system through structured data flow.

It allows WishSpark to process store activity, market signals, AI analysis, and intelligence generation without relying only on synchronous API requests.


## Core components

The backbone consists of:

- PostgreSQL (primary database)
- Redis (cache and fast state)
- event logs
- task queues
- metrics and telemetry


## Data sources

Events originate from several sources:

- Shopify webhooks
- store tracker events
- visitor actions
- pricing updates
- competitor signals
- internal AI outputs


## Event ingestion

Events enter the system through:

- API endpoints
- tracker.js
- Shopify webhooks
- internal services

Examples:

product_view  
add_to_cart  
wishlist_add  
checkout_start  
purchase  
price_update  
competitor_detected


## Event storage

Events are stored in:

PostgreSQL
for long-term analysis

Redis
for temporary fast access


## Event processing

Events trigger different types of processing:

live processing

- alerts
- live dashboard metrics
- visitor signals

batch processing

- price intelligence recalculation
- opportunity detection
- trend generation
- AI insights


## Event flow example

visitor event
↓
tracker.js
↓
FastAPI event endpoint
↓
event stored in PostgreSQL
↓
Redis update
↓
business engines triggered
↓
AI insight generation
↓
dashboard update


## Event bus concept

In future versions an event bus may manage:

- distributed workers
- asynchronous processing
- external integrations


## Observability

The backbone must provide observability for:

- event volume
- processing latency
- failed jobs
- AI costs
- worker performance


## Design rule

The event backbone must remain simple, observable, and scalable.

Heavy analysis should never run directly inside user-facing requests.
