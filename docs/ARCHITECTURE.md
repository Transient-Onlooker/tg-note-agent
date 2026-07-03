# Architecture

This document reflects both the current implementation and the next target shape of the project.

For raw Mermaid files, see `docs/diagrams/`.

## Current Runtime Architecture

```mermaid
flowchart LR
    User["User<br/>Telegram app"] --> Bot["Telegram Bot"]
    Bot -->|Webhook| Tunnel["ngrok tunnel"]
    Tunnel --> API["FastAPI `/webhook/telegram`"]

    API --> Router["UpdateRouter"]
    Router --> DB["SQLite<br/>MESSAGE / NOTE / AI_ANALYSIS"]
    Router --> TG["Telegram sendMessage"]
    Router --> BG["FastAPI BackgroundTasks"]
    BG --> NIM["NVIDIA NIM<br/>Text model"]
    NIM --> DB
    DB --> TG
```

## Current Text Processing Sequence

```mermaid
sequenceDiagram
    actor U as User
    participant T as Telegram Bot
    participant N as ngrok
    participant A as FastAPI
    participant R as UpdateRouter
    participant D as SQLite
    participant M as NVIDIA NIM

    U->>T: Send text
    T->>N: Webhook request
    N->>A: POST /webhook/telegram
    A->>R: handle_update(update, background_tasks)
    R->>D: Check duplicate by chat_id + message_id

    alt Duplicate update
        R-->>A: ignored
        A-->>T: 200 OK
    else New update
        R->>D: Insert MESSAGE(status=received)
        R->>T: sendMessage("수신 완료.")
        R-->>A: accepted
        A-->>T: 200 OK

        par Background processing
            R->>M: Analyze text
            alt NIM success
                M-->>R: title / summary / tags / confidence
                R->>D: Insert AI_ANALYSIS
                R->>D: Insert NOTE
                R->>D: Update MESSAGE(status=processed)
                R->>T: sendMessage("저장했어...")
            else NIM failure or timeout
                R->>D: Update MESSAGE(status=ai_failed)
                R->>T: sendMessage("메시지는 저장했지만 AI 분석에는 실패했어...")
            end
        end
    end
```

## What Changed During Implementation

```mermaid
flowchart TD
    A["Initial idea<br/>Webhook waits for AI result"] --> B["Problem observed<br/>NIM timeout causes Telegram retry"]
    B --> C["Applied change<br/>Immediate ack + background processing"]
    C --> D["Applied change<br/>Duplicate message check"]
    D --> E["Applied change<br/>NIM timeout 30s -> 120s"]
    E --> F["Applied change<br/>Console logging for each step"]
    F --> G["Applied change<br/>run.bat starts FastAPI + ngrok together"]
```

## Next Target: Agent-Oriented Pipeline

```mermaid
flowchart LR
    User["User<br/>Telegram"] --> Bot["Telegram Bot"]
    Bot --> API["FastAPI webhook"]
    API --> Raw["Store raw MESSAGE immediately"]
    Raw --> Ack["Send '수신 완료.'"]
    Raw --> Queue["JOB / TASK_QUEUE"]

    Queue --> Agent["Background Agent Worker"]
    Agent --> Search["Search SQLite / Notion / prior notes / rules"]
    Search --> Decide{"Need more context?"}

    Decide -->|No| Route{"Destination"}
    Decide -->|Yes| Clarify["Ask user on Telegram"]
    Clarify --> Agent

    Route -->|Note| LocalNote["SQLite NOTE"]
    Route -->|Readable archive| Notion["Notion page"]
    Route -->|Schedule| Calendar["Google Calendar"]
    Route -->|Task| Todo["Task store"]
    Route -->|Mixed result| Multi["Multiple destinations"]

    LocalNote --> Done["Send completion summary"]
    Notion --> Done
    Calendar --> Done
    Todo --> Done
    Multi --> Done
```

## Recommended Next Schema Expansion

```mermaid
erDiagram
    MESSAGE {
        string id PK
        string telegram_message_id
        string chat_id
        string sender_id
        text raw_text
        string status
        datetime created_at
    }

    JOB {
        string id PK
        string message_id FK
        string job_type
        string status
        int attempt_count
        text routing_context
        datetime created_at
        datetime updated_at
    }

    NOTE {
        string id PK
        string message_id FK
        string title
        text summary
        text body
        text tags
        float confidence
    }

    NOTION_EXPORT {
        string id PK
        string note_id FK
        string notion_page_id
        string status
        datetime exported_at
    }

    CLARIFICATION {
        string id PK
        string job_id FK
        text question
        text answer
        string status
    }

    MESSAGE ||--o{ JOB : spawns
    MESSAGE ||--o| NOTE : creates
    NOTE ||--o| NOTION_EXPORT : exports_to
    JOB ||--o{ CLARIFICATION : asks
```
