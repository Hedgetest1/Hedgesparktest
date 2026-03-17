# WishSpark — Project Architecture

## Overview

WishSpark is an AI-powered conversion intelligence platform for Shopify stores.

The system collects behavioral signals from store visitors and transforms them
into actionable insights for merchants.

Core objective:

Turn visitor behavior into conversion decisions.

---

# System Architecture

The platform is composed of five main layers.

Visitor Browser
      │
      ▼
Shopify Store + WishSpark Tracker
      │
      ▼
WishSpark Backend (FastAPI)
      │
      ▼
PostgreSQL Database
      │
      ▼
WishSpark Dashboard (Next.js)

---

# Server Structure

Root path:

/opt/wishspark

Current project structure:

wishspark
│
├── backend
│
├── dashboard
│
├── tracker
│
├── ops
│
├── docs
│
└── SERVER_CONTEXT.txt

---

# Backend

Framework:

FastAPI

Responsibilities:

• receive visitor events  
• process intent signals  
• run conversion intelligence  
• provide API endpoints to dashboard  

Main entry point:

backend/app/main.py

Active API modules:

events  
intent  
track  
dashboard  
opportunities  
price_intelligence  
market_lookup  
conversion_probability  
revenue_radar  
agent  
decision_engine  

---

# Database

Database engine:

PostgreSQL

Main tables:

visitors  
events  
wishlist_items  
products  
product_opportunity  
price_intelligence  
market_lookup  
visitor_product_state  
price_watch  

These tables store:

• visitor behavior  
• product interactions  
• market intelligence  
• pricing signals  

---

# Shopify Tracker

Location:

tracker/

The tracker is a JavaScript script embedded inside Shopify stores.

Collected signals:

page view  
scroll depth  
dwell time  
wishlist actions  
buy click  
session id  
visitor id  

The tracker sends events to backend APIs.

Endpoints used:

/track  
/events  

---

# Dashboard

Framework:

Next.js

UI Stack:

React  
TailwindCSS

Location:

dashboard/src/app

The dashboard provides merchant-facing analytics.

---

# Dashboard Plans

Two product tiers exist.

Lite Plan

Features:

Visitor analytics  
Radar signals  
Hot visitor detection  
Basic conversion insights

Pro Plan (HedgeSpark Intelligence)

Features:

Product opportunities  
Price intelligence  
Market intelligence  
AI copilot insights

---

# Intelligence Engines

WishSpark uses internal intelligence modules.

Main engines:

Price Intelligence  
Market Intelligence  
Conversion Probability  
Revenue Radar

These engines transform behavioral signals into actionable merchant insights.

---

# AI Copilot

The dashboard integrates an AI Copilot that assists merchants in understanding
conversion opportunities.

The copilot analyzes:

visitor intent  
product performance  
market signals  

and produces recommendations.

---

# Server Context System

WishSpark includes a system command:

wishspark-context

This command generates the file:

/opt/wishspark/SERVER_CONTEXT.txt

Purpose:

Allow AI agents to understand the structure of the system without breaking it.

The context file contains:

• server structure  
• backend routes  
• dashboard structure  
• database tables  
• git state  

---

# Long Term Vision

WishSpark is designed to evolve into:

AI Conversion Operating System for Shopify.

The system will eventually:

predict conversions  
suggest merchant actions  
run automatic experiments  
optimize pricing  
improve product positioning  

---

# Future Architecture

Future modules may include:

AI agents

ai_dev_agent  
ai_ops_agent  
ai_analytics_agent  
ai_growth_agent  

These agents will autonomously maintain and improve the platform.

---

# Design Principles

To allow AI-managed development, the system must remain:

deterministic  
modular  
self-documenting  
API-driven  

This guarantees that both human developers and AI agents can safely evolve
the platform.
