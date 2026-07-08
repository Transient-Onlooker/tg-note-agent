# Architecture

This document reflects the current hybrid implementation:

- Telegram outbound `sendMessage` is best-effort; timeout or HTTP failure must not turn the webhook into HTTP 500.
- Immediate ACK failure is logged only, while MESSAGE storage and background processing continue.
- Result reply failure is logged and recorded as `MESSAGE.status=reply_failed`.
- Deterministic command gate runs before AI routing for read, search, recent, count, correction, delete, and numbered references.
- AI routing is used only after command-gate miss, with a safety net preventing meta commands from becoming NOTE create/append.
- Explicit save prefixes such as `메모로 저장해줘:` are stripped before AI routing and NOTE persistence.
- Correction records `NOTE_REVISION` and can update `NOTE.title`, `NOTE.summary`, `NOTE.body`, `IMAGE_FILE.ocr_text`, and `IMAGE_FILE.summary`.
- Same-chat context remains bounded to 30 minutes for follow-up interpretation.

For raw Mermaid files, see `docs/diagrams/`.

## Current Runtime Architecture

```mermaid
flowchart LR
    User["User<br/>Telegram app"] --> Bot["Telegram Bot"]
    Bot -->|Webhook| Tunnel["ngrok tunnel"]
    Tunnel --> API["FastAPI `/webhook/telegram`"]

    API --> Router["UpdateRouter"]
    Router --> Outbound["Best-effort outbound<br/>ACK + result replies"]
    Outbound --> ReplyFailed["send failure logs warning<br/>result failure sets reply_failed"]
    Router --> Gate["Command gate first<br/>read / search / recent / count<br/>correction / delete / numbered reference"]
    Router --> Prep["Save text preparation<br/>strip explicit save prefixes"]
    Router --> BG["BackgroundTasks"]
    Router --> DB["SQLite"]
    Router --> Archive["ImageArchive"]
    Prep --> BG
    BG --> NIM["NVIDIA NIM<br/>router + text + vision"]

    Gate --> State["CONVERSATION_STATE<br/>last_list_results / last_search_results<br/>last_selected_note_id / last_image_note_id / pending_delete"]
    Gate --> Revision["NOTE_REVISION"]
    Archive --> DB
    Archive --> TGFile["Telegram file API"]
    NIM --> DB

    DB --> Msg["MESSAGE<br/>status includes reply_failed"]
    DB --> Note["NOTE<br/>deleted_at / deleted_reason"]
    DB --> Image["IMAGE_FILE<br/>ocr_text / summary / image_type / confidence"]
    DB --> Tags["TAG / NOTE_TAG"]
    DB --> Merge["MERGE_PROPOSAL"]
```

## Current Processing Sequence

```mermaid
sequenceDiagram
    actor U as User
    participant T as Telegram
    participant A as FastAPI
    participant R as UpdateRouter
    participant D as SQLite
    participant M as NVIDIA NIM

    U->>T: Send update
    T->>A: POST /webhook/telegram
    A->>R: handle_update
    R->>D: dedupe check + insert MESSAGE
    R-->>T: best-effort "수신 완료."
    alt ACK failed
        R->>R: logger.warning only
    end
    R-->>A: accepted
    A-->>T: 200 OK

    alt command gate hit
        R->>D: read CONVERSATION_STATE
        alt numbered reference
            R->>D: resolve last_list_results then last_search_results
            R->>D: set last_selected_note_id
        else read / search / recent / count
            R->>D: read active NOTE rows
            R->>D: set last_list_results or last_search_results
        else correction
            R->>D: insert NOTE_REVISION
            R->>D: update NOTE title/summary/body + IMAGE_FILE ocr/summary
        else delete
            R->>D: set pending_delete_note_id or soft delete NOTE
        end
        R->>D: update MESSAGE(status=processed)
        R-->>T: best-effort result reply
    else command gate miss
        R->>R: meta-command safety net
        R->>R: strip explicit save prefixes
        R->>M: route_text(prepared text)
        M-->>R: create / append / ignore / tool
        R->>D: write NOTE, read tool data, or mark ignored
        R-->>T: best-effort result reply
    end

    alt result send failed
        R->>D: update MESSAGE(status=reply_failed)
        R->>R: logger.warning
    end
```

## Current Data Shape

```mermaid
erDiagram
    MESSAGE {
        string id PK
        string telegram_message_id
        string chat_id
        string sender_id
        text raw_text
        string content_type
        string status "received|processed|ai_failed|needs_review|action_failed|reply_failed"
        datetime created_at
    }

    NOTE {
        string id PK
        string message_id FK
        string title
        text summary
        text body
        text tags
        float confidence
        datetime deleted_at
        string deleted_reason
        datetime created_at
    }

    IMAGE_FILE {
        string id PK
        string message_id FK
        string local_path
        text ocr_text
        text summary
        string image_type
        float confidence
        datetime created_at
    }

    CONVERSATION_STATE {
        string chat_id PK
        string sender_id PK
        string key PK
        text value_json "last_list_results|last_search_results|last_selected_note_id|last_image_note_id|pending_delete_note_id"
        datetime updated_at
    }

    NOTE_REVISION {
        string id PK
        string note_id FK
        text previous_body
        text new_body
        string reason
        datetime created_at
    }

    MESSAGE ||--o| NOTE : creates
    MESSAGE ||--o{ IMAGE_FILE : archives
    NOTE ||--o{ NOTE_REVISION : revises
```

## Near-Term Target

```mermaid
flowchart TD
    In["Telegram input"] --> Store["Store MESSAGE"]
    Store --> Gate{"Command gate first"}
    Gate -->|Read/Search/Recent/Count| Read["DB-only answer"]
    Gate -->|Numbered reference| Select["Resolve list/search state<br/>set selected note"]
    Gate -->|Correction| Correct["NOTE_REVISION<br/>NOTE title/summary/body<br/>IMAGE_FILE ocr/summary"]
    Gate -->|Delete| Delete["pending delete<br/>soft delete on confirm"]
    Gate -->|Miss| Prep["Strip explicit save prefixes"]
    Prep --> AI["AI route_text"]
    AI --> Save["Create/append/ignore/tool"]
    In -->|Photo| OCR["IMAGE_FILE<br/>OCR/classify/update"]
    OCR --> Duplicate{"Existing NOTE?"}
    Duplicate -->|Yes| Reuse["Reuse note"]
    Duplicate -->|No| NewImageNote["Create image NOTE<br/>set last_image_note_id"]
    Read --> Reply["Best-effort reply"]
    Select --> Reply
    Correct --> Reply
    Delete --> Reply
    Save --> Reply
    Reuse --> Reply
    NewImageNote --> Reply
    Reply --> Fail{"sendMessage failed?"}
    Fail -->|Yes| ReplyFailed["MESSAGE.status=reply_failed"]
    Fail -->|No| Done["Done"]
```
