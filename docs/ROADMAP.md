# Roadmap

Base date: `2026-07-03`

This is a short working roadmap for the current Telegram-first note agent.

Current stabilization status: webhook outbound messaging is best-effort, command gate runs before AI save routing, explicit save prefixes are stripped before note persistence, numbered references are backed by conversation state, text/image corrections update the stored note fields, and regression tests cover those paths.

Command UX direction: slash commands should pin intent while leaving arguments flexible. `/new` creates a new note, `/add` appends to an existing note, and existing-note mutations such as `/add`, `/fix`, `/delete`, and `/dedupe` require an approval step. After the slash-command prototype is stable, a natural-language router should map ordinary Korean requests into the same internal command actions without requiring `/`. See `docs/COMMANDS.md`.

## Delivery Phases

1. `v1` core webhook + note routing
   Target window: `2026-07-03` to `2026-07-04`
   Scope: immediate ack, duplicate handling, create/append/ignore

2. `v1.5` note query tools
   Target window: `2026-07-03` to `2026-07-04`
   Scope: count/search/tag listing, Telegram plain-text answers

2.5. `v1.6` slash command UX
   Target window: `2026-07-08` to `2026-07-09`
   Scope: `/new`, `/add`, `/list`, `/show`, `/raw`, `/delete`, `/fix`, `/dedupe`, `/help`; Telegram command menu registration; pagination over 10 results; approval state for mutating existing notes

2.6. `v1.7` natural-language command router
   Target window: after `v1.6`
   Scope: route ordinary Korean requests into internal command actions (`new`, `list`, `show`, `add`, `fix`, `delete`, `dedupe`) while preserving preview/approval for mutations

3. `v2` image intake
   Target window: `2026-07-03` to `2026-07-05`
   Scope: photo archive, OCR, note-vs-photo classification, OCR correction, clarification loop

4. `v2.1` merge workflow
   Target window: `2026-07-03` to `2026-07-06`
   Scope: scan all notes, propose merge, approve/cancel, delete merged note

4.5. `v2.2` document/PDF intake
   Target window: after `v2.1`
   Scope: Telegram document attachments, original file metadata storage, per-page embedded text extraction, scanned-page detection, page image rendering with vision OCR fallback, page-boundary-preserving merge, long-document summarization, page-range processing, and follow-up commands against saved document notes

5. `v2.5` agent expansion
   Target window: `2026-07-05` to `2026-07-08`
   Scope: more AI-callable tools, richer multi-step routing, Notion-first sync strategy

6. `v2.6` Google Workspace integrations
   Target window: after `v2.5`
   Scope: Google Calendar event create/search/update tools, Google Chat notification or command surface, Workspace OAuth/token storage, and approval rules for mutating calendar/workspace data

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

    section v1.6 Slash Commands
    Command grammar + docs          :active, v16a, 2026-07-08, 1d
    Telegram setMyCommands          :v16b, after v16a, 1d
    /new and /add separation        :v16c, after v16a, 1d
    Pagination for list/show        :v16d, after v16b, 1d
    Approval for add/fix/delete     :v16e, after v16c, 1d

    section v1.7 Natural Router
    Natural language command routing:v17a, after v16e, 2d
    Keep approval safety boundary   :v17b, after v17a, 1d

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

    section v2.2 Documents
    Telegram document intake         :v22a, after v21c, 1d
    Per-page embedded text extraction:v22b, after v22a, 1d
    Scanned-page detect + image render:v22c, after v22b, 1d
    Vision OCR fallback              :v22d, after v22c, 2d
    Page merge + document summary    :v22e, after v22d, 1d
    Page-range + follow-up commands  :v22f, after v22e, 1d

    section v2.5 Agent Expansion
    More dynamic tools              :v25a, after v22f, 2d
    Multi-step agent loop           :v25b, after v25a, 2d
    Notion sync strategy            :v25c, after v25b, 1d

    section v2.6 Google Workspace
    Workspace OAuth/token storage   :v26a, after v25c, 1d
    Google Calendar tools           :v26b, after v26a, 2d
    Google Chat integration         :v26c, after v26b, 1d
    Approval for external mutations :v26d, after v26b, 1d
```
