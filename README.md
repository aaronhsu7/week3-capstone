# Capstone Brief and Setup

**Session Time:** Remainder of Day 4 instructional time

## Purpose

Closing segment of Day 4 — transitioning from the week's final lesson (Deployment Patterns on AWS) into the capstone itself. By this point, every learner has built and validated a SQL agent (Day 2), extended it to query MongoDB alongside RDS (Day 2/3), built and validated a retrieval agent (Day 3), standardized all three to a shared tool registry and combined them into an orchestrator (this morning), and deployed that orchestrator to Lambda (this afternoon). The capstone is not new work — it's finishing, polishing, and demoing what's already built.

## What the Capstone Requires

Refer to the full Unit 3 Capstone Instructor Guide and learner-facing README for complete details, requirements, and the Bronze/Silver/Gold rubric. In brief, the capstone requires:

- A deployed multi-agent system on AWS Lambda, combining a SQL Query Agent, a MongoDB Tool, and a Retrieval Agent via a LangGraph Orchestrator
- SQL validation as a required, demonstrated deliverable — no query executes without passing through the validator built on Day 2
- A live demo covering all three tools individually plus at least one question requiring two or more sources combined
- Learner explanation of the routing decision, the validation layer, and the token cost of a query

## Pre-Capstone Check

Before releasing learners to work independently, confirm every learner has the following working, not just theoretically complete:

- [ ] RDS instance running with schema populated (Day 1)
- [ ] MongoDB connection working, `case_studies` collection populated (Day 1) — this is a required data store for the capstone, not optional
- [ ] OpenSearch index populated from the Day 3 ingestion pipeline
- [ ] AWS Bedrock access confirmed for Claude invocation via inference profile, for their account and region
- [ ] SQL agent passes all validation test cases (Day 2)
- [ ] MongoDB tool's `validate_mongo_operation` correctly blocks write/delete operations (Day 2/3)
- [ ] Retrieval agent returns cited, grounded answers (Day 3)
- [ ] Orchestrator successfully routes all single-tool and multi-tool combinations across all three tools (this morning)
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
- **Demo protocol:** live Lambda invocations covering all three tools individually (SQL-routed, MongoDB-routed, retrieval-routed) plus at least one multi-tool question, alongside verbal explanation of routing logic, validation layer, and token cost

This is the last instructor-led content of the week. Everything from here is capstone work time and the Friday demo.
