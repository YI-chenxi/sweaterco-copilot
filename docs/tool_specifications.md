# SweaterCo Tool Specifications

This document records the shared tool specifications for the SweaterCo
General Manager's Co-Pilot.

Each deterministic tool should define its purpose, inputs, outputs,
evidence, non-goals, and expected failure behaviour before integration
with the agent.

---

## Retrieval Tools

### 1. `lookup_order`

**Owner:** A — Data & Intelligence Tools  
**Type:** Retrieval + Tracing  
**Status:** Existing sample tool

**Purpose**

Retrieve one exact factory order when a valid order ID is known.

**Inputs**

- `order_id`: factory order identifier, for example `ORD-057`

**Outputs**

A `ToolResult` containing:

- the matching order record in `data`
- row-level evidence identifying the source order

**Evidence**

- Source: `orders.csv`
- Reference: matching `order_id`

**Non-goals**

- Does not assess order risk
- Does not determine whether an order is stalled or urgent
- Does not search for ambiguous or partial order descriptions
- Does not call an LLM

**Failure behaviour**

- Empty order IDs raise a `ValueError`
- Unknown order IDs return an empty result with a warning

---

### 2. `search_factory_orders`

**Owner:** A1 — Retrieval & Tracing  
**Type:** Retrieval + Tracing  
**Status:** Implemented

**Purpose**

Search factory orders using one or more objective filters when an exact
order ID is not known.

**Inputs**

Optional filters:

- `customer`
- `product`
- `category`
- `status`
- `current_stage`
- `due_before`
- `due_after`

Multiple filters may be combined.

**Outputs**

A `ToolResult` containing:

- `orders`: all matching order records
- `count`: number of matching orders
- row-level evidence for each matching order

**Evidence**

- Source: `orders.csv`
- Reference: matching `order_id`

**Non-goals**

- Does not classify orders as high-risk, stalled, urgent, or overdue
- Does not rank orders
- Does not infer which order a user meant when multiple orders match
- Does not call an LLM

**Failure behaviour**

- No search filters return an empty result with a warning
- No matching orders return an empty result with a warning
- Invalid due-date ranges raise a `ValueError`

---

### 3. `retrieve_factory_production`

**Owner:** A1 — Retrieval & Tracing  
**Type:** Retrieval + Tracing  
**Status:** Implemented

**Purpose**

Retrieve historical factory production records and calculate basic
deterministic summary statistics.

**Inputs**

Optional filters:

- `stage`
- `production_date`
- `start_date`
- `end_date`

**Outputs**

A `ToolResult` containing:

- `records`: matching production records
- `count`: number of matching records
- `summary`:
  - `total_pieces`
  - `average_pieces`
  - `min_pieces`
  - `max_pieces`

**Evidence**

- Source: `production_log.csv`
- Reference: matching `date` and `stage`

**Non-goals**

- Does not decide whether production is normal or abnormal
- Does not detect production anomalies
- Does not forecast future production
- Does not call an LLM

**Failure behaviour**

- No filters return an empty result with a warning
- No matching records return an empty result with a warning
- Conflicting exact-date and date-range inputs raise a `ValueError`
- Invalid date ranges raise a `ValueError`

**Open design note**

Factory Sundays have zero recorded output because the factory is closed.
The retrieval tool preserves these source records. Treatment of closed
days in production baselines or capacity estimates should be defined by
the Judgement / Feasibility logic rather than silently removed here.

---

### 4. `retrieve_factory_workshops`

**Owner:** A1 — Retrieval & Tracing  
**Type:** Retrieval + Tracing  
**Status:** Implemented

**Purpose**

Retrieve workshop profiles using objective workshop attributes.

**Inputs**

Optional filters:

- `workshop_id`
- `status`
- `category`

Category filtering supports workshops that make multiple categories,
such as `TOPS+ACCESSORIES`.

**Outputs**

A `ToolResult` containing:

- `workshops`: matching workshop profiles
- `count`: number of matching workshops

Returned workshop records retain source attributes including capacity,
queue, pickup lead time, defect rate, cost, status, and maximum batch
size where available.

**Evidence**

- Source: `workshops.csv`
- Reference: matching `workshop_id`

**Non-goals**

- Does not recommend the "best" workshop
- Does not automatically rank workshops by cost, quality, or speed
- Does not determine final order feasibility
- Does not automatically remove workshops based on business judgement
- Does not call an LLM

**Failure behaviour**

- No filters return an empty result with a warning
- No matching workshops return an empty result with a warning

---

## Shared Retrieval and Tracing Contract

A1 retrieval tools follow the shared interfaces defined in
`app/agent/contracts.py`.

Each tool:

1. Has a unique `name` and clear `description`.
2. Performs deterministic retrieval or calculation only.
3. Returns a `ToolResult`.
4. Places user-relevant results in `data`.
5. Provides source-level `Evidence` for traceability.
6. Uses `warnings` for valid requests that cannot return useful data.
7. Does not call an LLM.
8. Leaves business judgement to the relevant Judgement or Feasibility
   tool.

---

## Planned Tool Areas

The following areas are owned or co-owned by other parts of the project
and will be specified as development progresses:

- Judgement — production baseline, normal/abnormal, order risk
- Discovery — factory-wide anomaly and risk discovery
- Feasibility — current load, historical capacity, workshop capacity,
  assumptions and feasibility estimation
- Action — reminders, watches, confirmed actions and audit trail