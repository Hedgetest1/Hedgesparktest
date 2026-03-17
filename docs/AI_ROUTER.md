# WishSpark AI Router

Purpose

The AI Router decides which AI capability should be used for each task.

Not every task requires the same type of model, reasoning level, or cost.

The router protects the system from unnecessary AI usage and controls cost and latency.


## Position in architecture

Market Intelligence
↓
AI Router
↓
AI Engines


## Router responsibilities

The router decides:

- which AI engine to use
- whether AI is needed at all
- whether the task should run live or offline
- whether the task should run inside the sandbox
- whether human approval is required


## AI engine categories

WishSpark uses three conceptual AI levels.


### Micro AI Engine

Purpose:

cheap, fast classification and extraction.

Examples:

- intent classification
- event labeling
- signal extraction
- product tagging
- simple summarization

Characteristics:

- very low cost
- high speed
- no deep reasoning


### Insight Engine

Purpose:

structured reasoning and analysis.

Examples:

- store reports
- opportunity summaries
- pricing suggestions
- competitor analysis
- conversion insights

Characteristics:

- medium cost
- deeper reasoning
- structured outputs


### Agent Engine

Purpose:

complex tasks and system operations.

Examples:

- strategy simulations
- multi-step analysis
- debugging code
- architecture inspection
- long reasoning chains

Characteristics:

- higher cost
- slower execution
- restricted usage


## Routing rules

Example routing logic.

visitor intent classification
→ Micro AI

store weekly report
→ Insight Engine

pricing strategy simulation
→ Agent Engine inside sandbox


## Cost protection

The router must enforce:

- model budgets
- daily AI usage limits
- cost alerts


## Safety rules

The router must prevent:

- uncontrolled agent execution
- direct production modification
- AI loops


## Sandbox integration

Tasks that modify or simulate system behavior must run inside:

sandbox layer


## Human approval

Certain tasks require human approval:

- code modifications
- pricing automation
- major strategy changes


## Future expansion

The router may later include:

- dynamic model benchmarking
- automatic model selection
- provider switching
- fallback models


## Core principle

AI should be used only when it creates real value.

The router exists to ensure intelligence without uncontrolled cost or complexity.
