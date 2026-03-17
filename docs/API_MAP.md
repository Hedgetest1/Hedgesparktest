# WishSpark — API Map

This document describes the backend API structure of WishSpark.

The backend is implemented using FastAPI.

Main entry point:

backend/app/main.py

All API endpoints are organized through routers.

---

# Event Tracking APIs

Purpose:

Receive behavioral signals from Shopify visitors.

Main endpoints:

POST /track
POST /events

Responsibilities:

• record visitor actions  
• store behavioral signals  
• attach visitor/session identifiers  

These APIs feed the behavioral dataset used by intelligence engines.

---

# Intent Detection APIs

Purpose:

Analyze behavioral signals to estimate visitor intent.

Endpoints:

POST /intent

Function:

Transform raw behavior into intent scores.

Example outputs:

hot visitor  
warm visitor  
cold visitor  

Intent scoring is used by the dashboard radar.

---

# Dashboard APIs

Purpose:

Provide aggregated analytics for the merchant dashboard.

Endpoints:

GET /dashboard

Typical data returned:

visitor metrics  
session counts  
event totals  
wishlist interactions  
conversion readiness indicators  

Used by:

Dashboard Lite.

---

# Opportunities APIs

Purpose:

Detect conversion opportunities for products.

Endpoints:

GET /opportunities

Examples:

discount opportunity  
urgency signal  
scarcity opportunity  

Used by:

HedgeSpark Intelligence dashboard.

---

# Price Intelligence APIs

Purpose:

Analyze the pricing position of a product compared to the market.

Endpoints:

GET /price_intelligence

Possible insights:

price below market  
price above market  
price competitive  

Recommended actions may include:

price reduction  
promotion  
price anchoring strategy.

---

# Market Lookup APIs

Purpose:

Understand how unique or common a product is in the market.

Endpoints:

GET /market_lookup

Possible outputs:

unique product  
comparable product  
commodity product  

This helps merchants decide positioning strategies.

---

# Conversion Probability APIs

Purpose:

Estimate the probability of a product converting based on behavioral signals.

Endpoints:

GET /conversion_probability

Data used:

visitor signals  
wishlist activity  
product interactions  

Outputs:

conversion probability score.

---

# Revenue Radar APIs

Purpose:

Identify products gaining momentum.

Endpoints:

GET /revenue_radar

Signals analyzed:

visitor traffic  
wishlist growth  
interaction velocity  

The radar highlights products with increasing conversion pressure.

---

# Decision Engine APIs

Purpose:

Combine all intelligence modules to produce strategic insights.

Endpoints:

GET /decision_engine

The engine aggregates:

price intelligence  
market intelligence  
conversion probability  
visitor signals  

Output:

actionable merchant recommendations.

---

# Agent APIs

Purpose:

Support future AI agents interacting with the system.

Endpoints:

/agent

These APIs will allow automated agents to:

inspect system data  
suggest optimizations  
execute automated experiments.

---

# API Design Principles

WishSpark APIs follow these principles:

stateless requests  
clear endpoint naming  
predictable responses  
JSON payloads  

These rules ensure compatibility with:

dashboard UI  
external integrations  
AI agents.

---

# Future APIs

Planned future endpoints include:

/export_data  
/sales_trends  
/ai_autopilot  

These APIs will support:

data exports  
trend visualization  
automated optimization.
