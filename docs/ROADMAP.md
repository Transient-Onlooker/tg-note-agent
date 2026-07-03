# Roadmap

Base date: `2026-07-03`

This timeline is intentionally short and pragmatic. It is meant to make the next implementation steps easier to resume, not to lock the project into a rigid long-term plan.

## Delivery Phases

1. `v1` stabilize the Telegram text note flow
   Target window: `2026-07-03` to `2026-07-05`
   Scope: webhook stability, duplicate handling, background processing, model tuning, logging

2. `v1.5` add readable note output through Notion
   Target window: `2026-07-06` to `2026-07-08`
   Scope: optional Notion export, `notion_page_id` persistence, Telegram completion message includes Notion status

3. `v2` image note ingestion
   Target window: `2026-07-09` to `2026-07-14`
   Scope: Telegram image receive, local archive, hash dedupe, vision model analysis, OCR/search metadata

4. `v2.5` agent-style routing
   Target window: `2026-07-15` to `2026-07-18`
   Scope: `JOB` queue, worker routing, clarification loop, multi-destination save logic

## Immediate Priority

The next engineering target should be `v1.5`, not `v2`.

Reason:

- It directly solves the current product gap: "stored, but visible in a readable note app"
- It preserves the current SQLite-first architecture
- It gives a cleaner handoff point before image ingestion and OCR complexity

## Mermaid

```mermaid
gantt
    title tg-note-agent short roadmap
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section v1 Stabilization
    Webhook reliability             :done, v1a, 2026-07-03, 1d
    Background processing           :done, v1b, 2026-07-03, 1d
    Duplicate update handling       :done, v1c, 2026-07-03, 1d
    Model tuning and timeout policy :active, v1d, 2026-07-03, 3d

    section v1.5 Notion
    Notion integration scaffold     :v15a, 2026-07-06, 1d
    Notion page export              :v15b, after v15a, 1d
    DB link and response polish     :v15c, after v15b, 1d

    section v2 Images
    Telegram image ingest           :v2a, 2026-07-09, 2d
    Local archive and dedupe        :v2b, after v2a, 2d
    Vision analysis and OCR         :v2c, after v2b, 2d
    Search and return flow          :v2d, after v2c, 1d

    section v2.5 Agent Routing
    Job queue schema                :v25a, 2026-07-15, 1d
    Worker routing engine           :v25b, after v25a, 2d
    Clarification loop              :v25c, after v25b, 1d
```
