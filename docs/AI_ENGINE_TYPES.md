# WishSpark AI Engine Types

Purpose

WishSpark uses multiple AI capability levels rather than a single generic AI.

This design allows the system to control cost, latency, and reasoning depth.


## AI Engine Categories

The system defines three primary AI capability levels.


### Micro AI Engine

Role

Very fast and inexpensive AI tasks.

Typical usage

- classification
- intent detection
- event labeling
- product tagging
- extracting structured fields

Examples in WishSpark

- intent engine
- signal extraction
- event classification

Characteristics

- lowest cost
- very fast
- minimal reasoning


### Insight Engine

Role

Generate structured insights and recommendations.

Typical usage

- pricing suggestions
- opportunity summaries
- conversion insights
- competitor analysis
- store reports

Characteristics

- medium cost
- deeper reasoning
- structured output


### Agent Engine

Role

Handle complex multi-step reasoning and internal system tasks.

Typical usage

- strategy simulations
- debugging
- architecture analysis
- code generation
- automation planning

Characteristics

- highest cost
- slower execution
- restricted access


## Engine selection

AI tasks should be routed by the AI Router.

Example routing

visitor event classification  
→ Micro AI

store performance report  
→ Insight Engine

pricing strategy simulation  
→ Agent Engine inside sandbox


## Safety rules

Agent engines must never:

- modify production code directly
- modify production databases
- deploy automatically


## Sandbox rule

All agent-level tasks must run inside the sandbox layer before any real change.


## Cost management

The system must track:

- AI cost per task
- AI cost per store
- AI cost per day


## Design principle

AI should be applied progressively:

Micro AI first  
Insight AI second  
Agent AI only when required
