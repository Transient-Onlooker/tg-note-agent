# Roadmap

Base date: `2026-07-03`

This is a short working roadmap for the current Telegram-first note agent.

Current stabilization status: webhook outbound messaging is best-effort, command gate runs before AI save routing, explicit save prefixes are stripped before note persistence, numbered references are backed by conversation state, text/image corrections update the stored note fields, and regression tests cover those paths.

## Delivery Phases

1. `v1` core webhook + note routing
   Target window: `2026-07-03` to `2026-07-04`
   Scope: immediate ack, duplicate handling, create/append/ignore

2. `v1.5` note query tools
   Target window: `2026-07-03` to `2026-07-04`
   Scope: count/search/tag listing, Telegram plain-text answers

3. `v2` image intake
   Target window: `2026-07-03` to `2026-07-05`
   Scope: photo archive, OCR, note-vs-photo classification, OCR correction, clarification loop

4. `v2.1` merge workflow
   Target window: `2026-07-03` to `2026-07-06`
   Scope: scan all notes, propose merge, approve/cancel, delete merged note

5. `v2.5` agent expansion
   Target window: `2026-07-05` to `2026-07-08`
   Scope: more AI-callable tools, richer multi-step routing, Notion-first sync strategy

## Mermaid

```mermaid
gantt
    title tg-note-agent short roadmap
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section v1 Core
    Webhook reliability             :done, v1a, 2026-07-03, 1d
    Background processing           :done, v1b, 2026-07-03, 1d
    Duplicate update handling       :done, v1c, 2026-07-03, 1d
    Create / append / ignore route  :done, v1d, 2026-07-03, 1d
    Best-effort sendMessage policy  :done, v1e, 2026-07-04, 1d
    Explicit save prefix cleanup    :done, v1f, 2026-07-08, 1d

    section v1.5 Query Tools
    Count / search / tag tools      :done, v15a, 2026-07-03, 1d
    Telegram output cleanup         :done, v15b, 2026-07-03, 1d
    Command gate hardening          :done, v15c, 2026-07-04, 1d
    Numbered reference regression   :done, v15d, 2026-07-04, 1d
    Delete-phrase correction route  :done, v15e, 2026-07-08, 1d

    section v2 Images
    Telegram image ingest           :done, v2a, 2026-07-03, 1d
    Local archive                   :done, v2b, 2026-07-03, 1d
    OCR and image classification    :done, v2c, 2026-07-03, 2d
    OCR correction sync             :done, v2d, 2026-07-04, 1d
    Clarification flow              :active, v2e, 2026-07-04, 2d

    section v2.1 Merge Flow
    Merge proposal tool             :done, v21a, 2026-07-03, 2d
    Approve / cancel / soft delete  :done, v21b, 2026-07-04, 1d
    Merge summary refresh           :active, v21c, 2026-07-05, 1d

    section v2.5 Agent Expansion
    More dynamic tools              :v25a, 2026-07-05, 2d
    Multi-step agent loop           :v25b, after v25a, 2d
    Notion sync strategy            :v25c, after v25b, 1d
```
