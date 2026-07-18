# Architecture

This document reflects the current hybrid implementation:

- Telegram outbound `sendMessage` is best-effort; timeout or HTTP failure must not turn the webhook into HTTP 500.
- Immediate ACK failure is logged only, while MESSAGE storage and background processing continue.
- Result reply failure is logged and recorded as `MESSAGE.status=reply_failed`.
- Deterministic command gate runs before AI routing for read, search, recent, count, correction, delete, and numbered references.
- Target slash-command layer pins command intent while allowing flexible AI/DB-assisted argument resolution. `/new` creates a note; `/add` appends to an existing note.
- Existing-note mutations require approval: `/add`, `/fix`, `/delete`, and `/dedupe` must show a preview or target list before execution.
- Broad list/show results should paginate at 10 items per page and keep page state in `CONVERSATION_STATE`.
- AI routing is used only after command-gate miss, with a safety net preventing meta commands from becoming NOTE create/append.
- Explicit save prefixes are removed before analysis; an explicit save forces NOTE persistence even if AI routing says ignore.
- Planned batch-list capture preserves a multi-line message as one raw NOTE and extracts sections/items; it only splits into separate notes after an explicit user request.
- Bare numeric slash arguments such as `/delete 5` resolve item 5 from `last_list_results` before any previously selected note.
- AI-generated titles, summaries, tags, search answers, and agent answers remove Han ideographs; raw NOTE bodies and OCR text remain unchanged.
- Temporal metadata is promoted only for clear plans, appointments, tasks, or deadlines, not past narrative context such as `3시까지 기다렸다`.
- Correction records `NOTE_REVISION` and can update `NOTE.title`, `NOTE.summary`, `NOTE.body`, `IMAGE_FILE.ocr_text`, and `IMAGE_FILE.summary`.
- Same-chat context remains bounded to 30 minutes for follow-up interpretation.

For raw Mermaid files, see `docs/diagrams/`.

## Current Runtime Architecture

```mermaid
flowchart LR
    User["User in Telegram"] --> Telegram["Telegram Bot API"]
    Telegram -->|webhook update| Tunnel["ngrok tunnel"]
    Tunnel --> API["FastAPI /webhook/telegram"]
    API --> Router["UpdateRouter"]

    Terminal["testterminal.bat"] -->|simulated text, image, PDF| Router

    Router --> Store["Store MESSAGE and dedupe"]
    Router --> Ack["Best-effort immediate ACK"]
    Ack -->|failure| AckLog["warning only; webhook still returns 200"]
    Router --> Gate["Deterministic command gate"]
    Gate --> Slash["Slash commands<br/>/new /add /list /show /raw<br/>/fix /delete /dedupe /help"]
    Gate --> Natural["Natural commands<br/>read, search, list, count<br/>correction, delete, numbered reference"]
    Gate --> State["CONVERSATION_STATE<br/>result ids, page state, selection<br/>bare number resolves current list first<br/>pending action, pending delete"]
    Gate --> Revision["NOTE_REVISION<br/>correction audit"]

    Router --> Prep["Prepare note text<br/>strip prefix; explicit save forces create"]
    Prep --> Capture["Batch-list capture - planned<br/>preserve one raw NOTE; detect sections and items"]
    Capture --> Split{"Explicitly request<br/>separate notes?"}
    Split -->|No or ambiguous| OneNote["Keep one NOTE<br/>with structured list metadata"]
    Split -->|Yes| ManyNotes["Create or propose<br/>separate notes per section"]
    Prep --> AI["NVIDIA NIM<br/>route, analyze, vision"]
    Router --> Archive["ImageArchive"]
    Archive --> FileAPI["Telegram file API"]

    Router --> Manager["NoteManager"]
    Manager --> DB[("SQLite")]
    Archive --> DB
    AI --> MetadataGuard["Generated metadata guard<br/>remove Han ideographs<br/>preserve only actionable schedule times"]
    MetadataGuard --> Manager

    DB --> Message["MESSAGE<br/>received, processed, reply_failed"]
    DB --> Note["NOTE<br/>soft delete fields"]
    DB --> Image["IMAGE_FILE<br/>OCR text and classification"]
    DB --> Tags["TAG and NOTE_TAG"]
    DB --> Items["NOTE_LIST_ITEM - planned<br/>section, text, completion state"]
    DB --> Merge["MERGE_PROPOSAL"]

    Router --> Reply["Best-effort result reply"]
    Reply -->|success| Telegram
    Reply -->|failure| ReplyFailed["Log warning and set<br/>MESSAGE.status = reply_failed"]
```

## Current Processing Sequence

```mermaid
sequenceDiagram
    actor U as User
    participant T as Telegram
    participant N as ngrok
    participant A as FastAPI
    participant R as UpdateRouter
    participant D as SQLite
    participant M as NVIDIA NIM

    U->>T: Send text or photo
    T->>N: Webhook request
    N->>A: POST /webhook/telegram
    A->>R: handle_update(update)
    R->>D: Dedupe check

    alt Duplicate update
        R-->>A: ignored
        A-->>T: 200 OK
    else New update
        R->>D: Insert MESSAGE(status=received)
        R->>T: Best-effort sendMessage("Received.")
        alt ACK delivery fails
            R->>R: Log warning only
        end
        R-->>A: accepted
        A-->>T: 200 OK

        par Background processing
            alt Text command gate hit
                R->>D: Load CONVERSATION_STATE
                alt Slash command
                    R->>R: Pin command intent
                    R->>D: Resolve note target and page state
                    Note over R,D: Bare number such as /delete 5 resolves current list item 5 before prior selection
                    alt Existing-note mutation
                        R->>D: Save pending_action preview
                    else List or show
                        R->>D: Save page_state and result ids
                    else New note
                        R->>M: Analyze note text
                        M-->>R: Title, summary, tags
                        R->>R: Remove Han; preserve only actionable schedule times
                        R->>D: Create NOTE
                    end
                else Read, search, recent, count
                    R->>D: Read active NOTE rows
                    R->>D: Save list/search result ids
                else Numbered reference
                    R->>D: Resolve last_list_results, then last_search_results
                    R->>D: Save last_selected_note_id
                else Correction
                    R->>D: Insert NOTE_REVISION
                    R->>D: Update NOTE body and linked IMAGE_FILE OCR text
                else Delete
                    R->>D: Save pending_delete_note_id
                    Note over R,D: Confirmation soft-deletes the NOTE
                end
                R->>D: Mark MESSAGE processed
                R->>T: Best-effort result reply
            else Command gate miss
                R->>R: Reject meta commands from note persistence
                R->>R: Strip explicit save prefix; set force-create flag
                R->>D: Load 30-minute chat context and candidates
                R->>M: Route and analyze text
                M-->>R: create, append, ignore, or tool
                R->>R: Explicit save stays create even if AI route is ignore
                R->>R: Sanitize generated title, summary, tags, and AI answer
                Note over R: Raw NOTE body and OCR text remain unchanged
                R->>D: Persist NOTE or execute read-only tool
                R->>T: Best-effort result reply
            else Photo flow
                R->>D: Insert IMAGE_FILE
                R->>M: OCR and image classification
                M-->>R: OCR text, summary, type, confidence
                R->>R: Preserve OCR text; sanitize generated metadata
                R->>D: Update IMAGE_FILE
                alt NOTE already linked to this image
                    R->>D: Reuse existing NOTE and update reference state
                else Image is a note
                    R->>D: Create NOTE and save last_image_note_id
                else Unclear image
                    R->>D: Mark MESSAGE needs_review
                end
                R->>T: Best-effort image result reply
            end
        end

        alt Result reply delivery fails
            R->>D: Set MESSAGE.status=reply_failed
            R->>R: Log warning; do not roll back action
        end
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
        string status "received, processed, ai_failed, needs_review, action_failed, reply_failed"
        datetime created_at
    }

    TELEGRAM_MESSAGE_DEDUPE {
        string chat_id PK
        string telegram_message_id PK
        string message_id
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
        string notion_page_id
        string notion_status
        datetime deleted_at
        string deleted_reason
        datetime created_at
    }

    AI_ANALYSIS {
        string id PK
        string message_id FK
        string provider
        string model
        string category
        text raw_response
        float confidence
        datetime created_at
    }

    IMAGE_FILE {
        string id PK
        string message_id FK
        string telegram_file_id
        string telegram_file_unique_id
        string local_path
        string mime_type
        int file_size
        int width
        int height
        text ocr_text
        text summary
        string image_type
        float confidence
        datetime created_at
    }

    TAG {
        string id PK
        string name
        string normalized_name
        datetime created_at
    }

    NOTE_TAG {
        string note_id PK, FK
        string tag_id PK, FK
        datetime created_at
    }

    MERGE_PROPOSAL {
        string id PK
        string chat_id
        string keep_note_id FK
        string merge_note_id FK
        string reason
        string status
        datetime created_at
    }

    CONVERSATION_STATE {
        string chat_id PK
        string sender_id PK
        string key PK
        text value_json "result ids, page state, selection, pending actions"
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

    NOTE_LIST_ITEM {
        string id PK
        string note_id FK
        string section_label
        text body
        int position
        boolean is_completed
        datetime created_at
    }

    MESSAGE ||--o| NOTE : creates_or_updates
    MESSAGE ||--o{ AI_ANALYSIS : analyzed_by
    MESSAGE ||--o{ IMAGE_FILE : archives
    NOTE ||--o{ NOTE_REVISION : revises
    NOTE ||--o{ NOTE_LIST_ITEM : contains_planned
    NOTE ||--o{ NOTE_TAG : labels
    TAG ||--o{ NOTE_TAG : belongs_to
    NOTE ||--o{ MERGE_PROPOSAL : keep_target
    NOTE ||--o{ MERGE_PROPOSAL : merge_target
```

## Near-Term Target

```mermaid
flowchart TD
    Input["Telegram text, photo, or document"] --> Store["Store MESSAGE immediately"]
    Store --> Ack["Best-effort immediate ACK"]
    Ack --> AckResult{"ACK delivered?"}
    AckResult -->|No| AckLog["Log warning<br/>return webhook 200"]
    AckResult -->|Yes| Accepted["Webhook accepted"]
    AckLog --> Accepted

    Store --> State["Load CONVERSATION_STATE"]
    State --> Gate{"Deterministic command gate"}

    Gate -->|Slash command| Slash["Pin command intent"]
    Gate -->|Natural command| Command["read, search, recent, count<br/>correction, delete, numbered reference"]
    Slash --> Resolve["Resolve target and arguments<br/>with DB search or bounded AI helper"]
    Command --> Resolve

    Resolve -->|List or search| List["Read active notes<br/>save result ids and page state"]
    Resolve -->|Numbered reference| Select["Resolve current result set<br/>bare slash number uses list first<br/>save selected note"]
    Resolve -->|Read original| Read["Return NOTE body or IMAGE_FILE OCR text"]
    Resolve -->|Correction| Correct["Insert NOTE_REVISION<br/>sync NOTE body and IMAGE_FILE OCR text"]
    Resolve -->|Delete request| DeleteAsk["Save pending delete target"]
    DeleteAsk --> DeleteConfirm{"User confirms?"}
    DeleteConfirm -->|Yes| SoftDelete["Soft delete NOTE<br/>set deleted_at and reason"]
    DeleteConfirm -->|No| Cancel["Clear pending state"]
    Resolve -->|Existing-note mutation| Preview["Save pending_action preview"]
    Resolve -->|New note| Create["Create NOTE"]

    Gate -->|No command| Meta{"Meta command?"}
    Meta -->|Yes| NoSave["Do not create or append"]
    Meta -->|No| Prep["Strip explicit save prefix<br/>explicit save forces create<br/>load context and candidates"]
    Prep --> Batch{"Multi-line list capture? - planned"}
    Batch -->|No| Agent["AI router and tools"]
    Batch -->|Yes| Structure["Preserve raw body in one NOTE<br/>detect sections and checklist items"]
    Structure --> Split{"Explicitly asks to split<br/>into separate notes?"}
    Split -->|No or ambiguous| ListNote["Create one list NOTE<br/>save item metadata for future tools"]
    Split -->|Yes| SplitNotes["Create or propose notes<br/>per section"]
    Agent --> AgentAction{"AI action"}
    AgentAction -->|Create, append, or explicit save| Metadata["Validate generated metadata<br/>remove Han ideographs<br/>keep actionable schedule times only"]
    Metadata --> Save["Persist NOTE and AI_ANALYSIS<br/>raw body unchanged"]
    AgentAction -->|Read-only tool| Tool["Query active notes, tags, merge candidates"]
    AgentAction -->|Ignore without explicit save| Ignore["Mark MESSAGE processed"]

    Input -->|Photo| Image["Archive IMAGE_FILE"]
    Image --> Vision["Vision OCR and classification"]
    Vision --> ImageMetadata["Preserve OCR original<br/>sanitize generated metadata"]
    ImageMetadata --> ImageData["Update OCR text, summary<br/>image type, confidence"]
    ImageData --> Existing{"Existing NOTE for image?"}
    Existing -->|Yes| Reuse["Reuse NOTE and save image reference"]
    Existing -->|No, note image| ImageNote["Create NOTE and last_image_note_id"]
    Existing -->|Unclear| Review["Mark needs_review and ask user"]
    Existing -->|General photo| ArchiveOnly["Keep archive without NOTE"]

    Input -->|Document - planned| Document["Store file and page metadata"]
    Document --> Pages["Embedded text first<br/>vision OCR fallback per page"]
    Pages --> DocumentNote["Merge page text with boundaries<br/>create document NOTE"]

    List --> Reply["Best-effort result reply"]
    Select --> Reply
    Read --> Reply
    Correct --> Reply
    SoftDelete --> Reply
    Cancel --> Reply
    Preview --> Reply
    Create --> Reply
    NoSave --> Reply
    Save --> Reply
    Tool --> Reply
    ListNote --> Reply
    SplitNotes --> Reply
    Ignore --> Reply
    Reuse --> Reply
    ImageNote --> Reply
    Review --> Reply
    ArchiveOnly --> Reply
    DocumentNote --> Reply

    Reply --> ReplyResult{"Reply delivered?"}
    ReplyResult -->|No| ReplyFailed["Set MESSAGE.status to reply_failed<br/>keep completed action"]
    ReplyResult -->|Yes| Done["Done"]
```
