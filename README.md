# Capstone Brief and Setup

## Purpose

Closing segment of Day 4 — transitioning from the week's final lesson (Deployment Patterns on AWS) into the capstone itself. By this point, every learner has built and validated a SQL agent (Day 2), built and validated a retrieval agent (Day 3), standardized both to a shared tool registry and combined them into an orchestrator (this morning), and deployed that orchestrator to Lambda (this afternoon). The capstone is not new work — it's finishing, polishing, and demoing what's already built.

> **Scope note:** per Module 3's HLD ("Databases, SQL and Modeling"), this capstone's required architecture is
> a SQL Query Agent, a Retrieval Agent, and a LangGraph Orchestrator that routes between them and synthesizes a
> cited response — two workers, not three. Earlier drafts of this README referenced a third MongoDB Tool that
> is not part of the HLD and was never actually built out in any lesson this week; those references have been
> removed below to match the HLD exactly.

## What the Capstone Requires

Refer to the full Unit 3 Capstone Instructor Guide and learner-facing README for complete details, requirements, and the Bronze/Silver/Gold rubric. In brief, the capstone requires:

- A deployed multi-agent system on AWS Lambda, combining a SQL Query Agent and a Retrieval Agent via a LangGraph Orchestrator
- SQL validation as a required, demonstrated deliverable — no query executes without passing through the validator built on Day 2
- A live demo covering both tools individually plus at least one question requiring both sources combined
- Learner explanation of the routing decision, the validation layer, and the token cost of a query

## Pre-Capstone Check

Before releasing learners to work independently, confirm every learner has the following working, not just theoretically complete:

- [ ] RDS instance running with schema populated (Day 1)
- [ ] OpenSearch index populated from the Day 3 ingestion pipeline
- [ ] AWS Bedrock access confirmed for Claude invocation via inference profile, for their account and region
- [ ] SQL agent passes all validation test cases (Day 2)
- [ ] Retrieval agent returns cited, grounded answers (Day 3)
- [ ] Orchestrator successfully routes all single-tool and multi-tool combinations across both tools (this morning)
- [ ] Lambda deployment succeeds and returns a valid response (this afternoon)

If any learner is missing one of these, address it now — the capstone work period is for polishing and demoing, not building these components from scratch under time pressure.

## What Changes Between Now and the Demo

The core system should already work by the end of this afternoon's Deployment Patterns lesson. Capstone time should go toward:

- Polishing error handling and edge cases
- Improving tool descriptions if routing has been unreliable
- Adding stretch goals — conversation memory across turns, or human-in-the-loop confirmation before unusual SQL queries execute
- Preparing the demo questions and practicing the explanation of routing, validation, and token cost

## Where to Go From Here

- **Full capstone requirements, Bronze/Silver/Gold rubric:** Unit 3 Capstone README
- **Step-by-step build reference for any component:** Unit 3 Capstone Instructor Guide
- **Demo protocol:** live Lambda invocations covering both tools individually (SQL-routed, retrieval-routed) plus at least one multi-tool question, alongside verbal explanation of routing logic, validation layer, and token cost

This is the last instructor-led content of the week. Everything from here is capstone work time and the Friday demo.
