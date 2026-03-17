# WishSpark AI Coding Rules

Project: WishSpark
Type: AI Commerce Intelligence System for Shopify

## General behavior

Claude must:
- Always analyze the entire repository before modifying code
- Propose a plan before implementing changes
- Wait for user approval before executing structural changes
- Never modify production-critical files without confirmation

## Code modification rules

Claude must:
- Prefer rewriting full files rather than partial patches
- Avoid suggesting manual copy/paste changes
- Ensure code remains compatible with the existing architecture

## Architecture overview

WishSpark consists of:

backend/
FastAPI API server

dashboard/
frontend merchant dashboard

workers/
background analytics workers

ai_engines/
AI analysis engines

market_engine/
competitor and pricing analysis

agents/
AI autonomous agents

sandbox/
temporary analysis environments

## AI architecture

WishSpark uses:

Claude Code
for coding, debugging, architecture

OpenAI API
for merchant AI features

## Safety constraints

Claude must NEVER:

- modify server credentials
- change billing logic
- delete database content
- run destructive shell commands

without explicit user approval.

## Development philosophy

WishSpark is designed to evolve into:

AI-managed infrastructure

Claude must prioritize:

- modular code
- autonomous diagnosability
- clear logs
- testability
