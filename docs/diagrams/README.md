# Diagram Index

These Mermaid files were selected from the original handoff direction and adapted to match the current codebase.

- `01_current_system_architecture.mmd`
  Current runtime structure with Telegram, ngrok, FastAPI, SQLite, NIM, and optional Notion.
- `02_current_webhook_sequence.mmd`
  Current webhook flow with duplicate check, immediate acknowledgement, and background AI processing.
- `03_current_db_erd.mmd`
  Current database schema that exists in code today.
- `04_target_agent_pipeline.mmd`
  Next-step agent-oriented architecture that keeps raw message capture but moves routing into a worker.
- `05_delivery_timeline.mmd`
  Short delivery timeline from `v1` stabilization through `v2.5` agent routing.
