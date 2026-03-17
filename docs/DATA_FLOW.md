# WishSpark — Data Flow

This document describes how data moves through the WishSpark system.

The goal is to make the platform understandable for developers and AI agents.

---

# High-Level Flow

Visitor
│
▼
Shopify Store
│
▼
WishSpark Tracker
│
▼
Backend Event APIs
│
▼
PostgreSQL Database
│
▼
Intelligence Engines
│
▼
Dashboard APIs
│
▼
Merchant Dashboard

---

# Step 1 — Visitor Interaction

A visitor enters a Shopify store where WishSpark tracking is installed.

The visitor may generate signals such as:

page views  
scroll depth  
dwell time  
wishlist actions  
buy clicks  
product revisits  

These interactions represent the raw behavioral layer of the platform.

---

# Step 2 — Tracker Collection

The WishSpark tracker runs inside the Shopify storefront.

Its role is to:

collect visitor-side behavioral events  
attach visitor identifiers  
attach session identifiers  
send events to the backend  

Tracked data may include:

visitor_id  
session_id  
product_id  
event_type  
timestamp  
page context  

The tracker sends events to backend endpoints such as:

POST /track  
POST /events  

---

# Step 3 — Backend Ingestion

The backend receives incoming behavioral data through FastAPI routers.

At this stage the system:

validates payloads  
normalizes event structure  
stores event records  
links events to visitors and products  

The backend transforms raw tracker payloads into structured application data.

---

# Step 4 — Database Persistence

Once processed, data is stored in PostgreSQL.

Main persistence targets include:

visitors  
events  
wishlist_items  
products  
visitor_product_state  
price_watch  
product_opportunity  
price_intelligence  
market_lookup  

Purpose of persistence:

retain historical behavior  
enable aggregation  
support scoring engines  
support dashboard analytics  

---

# Step 5 — Behavioral Interpretation

After storage, backend logic and intelligence modules interpret the raw data.

Examples of derived interpretations:

visitor intent  
product momentum  
conversion readiness  
wishlist pressure  
recurring interest  

This is the layer where behavior becomes meaning.

---

# Step 6 — Intelligence Engines

WishSpark includes several intelligence engines.

## Intent Engine

Uses behavioral patterns to classify visitors.

Possible classifications:

hot  
warm  
cold  

## Conversion Probability Engine

Estimates the likelihood of conversion for a visitor or product.

## Price Intelligence Engine

Analyzes price positioning relative to market context.

## Market Intelligence Engine

Estimates whether a product is unique, comparable, or commoditized.

## Revenue Radar

Highlights product momentum and conversion pressure.

These engines may read from both raw event data and derived product/visitor state.

---

# Step 7 — Decision Layer

The decision layer combines multiple intelligence outputs.

Examples:

high intent + high wishlist + weak price position  
high product momentum + comparable market + urgency opportunity  

This layer is represented by modules such as:

decision_engine  
opportunities  
agent  

The goal is to turn analysis into suggested action.

---

# Step 8 — Dashboard Delivery

The dashboard consumes backend APIs.

Main dashboard surfaces:

Lite dashboard  
Pro dashboard (HedgeSpark Intelligence)  

The dashboard displays:

traffic metrics  
event counts  
wishlist activity  
hot visitors  
top products  
product opportunities  
price intelligence  
market intelligence  
copilot recommendations  

At this stage, data becomes merchant-facing operational insight.

---

# Step 9 — Merchant Action

The merchant reads the dashboard and may act on suggestions.

Typical actions:

change pricing  
launch promotion  
increase urgency  
improve product positioning  
watch specific products  

This is the first operational loop of WishSpark.

---

# Step 10 — Future Automated Action

In the future, AI agents may execute actions automatically.

Possible automation targets:

discount experiments  
urgency tests  
price tests  
segment-specific recommendations  
campaign triggers  

This will transform WishSpark from insight platform to operating system.

---

# Core Flow Summary

Raw behavior
→ tracked by storefront script
→ sent to backend
→ stored in database
→ interpreted by intelligence engines
→ aggregated by dashboard APIs
→ displayed to merchant
→ converted into action

---

# AI-Agent Readiness

This flow must remain:

predictable  
documented  
modular  

AI agents will depend on this determinism to:

debug ingestion issues  
improve scoring logic  
add new intelligence modules  
extend merchant automation safely  

---

# Key Principle

WishSpark is not only an analytics tool.

It is a behavioral intelligence pipeline whose final purpose is:

conversion action.
