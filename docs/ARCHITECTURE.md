# Architecture

This document reflects the current hybrid implementation: fast-path tools for common note operations, a read-focused fallback agent for unusual note queries, and a 30-minute same-chat context window.

For raw Mermaid files, see `docs/diagrams/`.

## Current Runtime Architecture

```mermaid
flowchart LR
    User["User<br/>Telegram app"] --> Bot["Telegram Bot"]
    Bot -->|Webhook| Tunnel["ngrok tunnel"]
    Tunnel --> API["FastAPI `/webhook/telegram`"]

    API --> Router["UpdateRouter"]
    Router --> Ack["Immediate ack / result reply"]
    Router --> BG["BackgroundTasks"]
    Router --> Archive["ImageArchive"]
    Router --> Manager["NoteManager"]
    Router --> DB["SQLite"]

    BG --> NIM["NVIDIA NIM<br/>text + vision"]
    BG --> FastTools["Fast-path tools"]
    BG --> AgentFallback["Read-focused fallback agent"]

    Manager --> DB
    Archive --> TGFile["Telegram file API"]
    Archive --> DB
    NIM --> FastTools
    NIM --> AgentFallback

    DB --> Tables["MESSAGE / NOTE / AI_ANALYSIS / TAG / NOTE_TAG / IMAGE_FILE / MERGE_PROPOSAL"]
    Tables --> Context["Recent same-chat context<br/>last 30 min"]
    Context --> NIM
```

## Current Processing Sequence

```mermaid
sequenceDiagram
    actor U as User
    participant T as Telegram Bot
    participant N as ngrok
    participant A as FastAPI
    participant R as UpdateRouter
    participant D as SQLite
    participant M as NVIDIA NIM

    U->>T: Send text or photo
    T->>N: Webhook request
    N->>A: POST /webhook/telegram
    A->>R: handle_update(update, background_tasks)
    R->>D: dedupe check

    alt duplicate
        R-->>A: ignored
        A-->>T: 200 OK
    else new input
        R->>D: insert MESSAGE(status=received)
        R->>T: send "수신 완료."
        R-->>A: accepted
        A-->>T: 200 OK

        par background work
            alt text
                R->>D: load same-chat messages from last 30 minutes
                R->>M: analyze_text(current text + recent context + tags + candidate notes)
                M-->>R: strict JSON route

                alt fast-path tool
                    alt count/search/tag
                        R->>D: read note data
                        R->>D: update MESSAGE(status=processed)
                        R-->>T: send plain text answer
                    else merge proposal
                        R->>D: read all notes
                        R->>D: insert MERGE_PROPOSAL(status=proposed)
                        R->>D: update MESSAGE(status=processed)
                        R-->>T: send keep/merge suggestion
                    end
                else fallback agent
                    loop up to 4 read steps
                        R->>M: plan_agent_step(query + recent context + tool history)
                        M-->>R: next tool or final response
                        alt tool step
                            R->>D: read notes / tags / note detail
                        else final response
                            R->>D: update MESSAGE(status=processed)
                            R-->>T: flexible plain text answer
                        end
                    end
                else note route
                    alt append
                        R->>D: update NOTE + NOTE_TAG
                    else create
                        R->>D: insert AI_ANALYSIS + NOTE + NOTE_TAG
                    else ignore
                        R->>D: update MESSAGE(status=processed)
                    end
                    R-->>T: send summary-only completion
                end
            else photo
                R->>D: insert IMAGE_FILE
                R->>M: OCR + classify image
                alt note image
                    R->>D: insert NOTE
                    R-->>T: send photo summary
                else unclear
                    R->>D: update MESSAGE(status=needs_review)
                    R-->>T: ask clarification
                else general photo
                    R->>D: update MESSAGE(status=processed)
                end
            end
        end
    end
```

## Why This Hybrid Shape

```mermaid
flowchart TD
    A["Fully hardcoded router"] --> B["Fast but rigid"]
    B --> C["Need: more flexible note queries"]
    C --> D["Added fast-path tools for common requests"]
    D --> E["Added fallback agent for uncommon requests"]
    E --> F["Kept fallback read-only for safety"]
    F --> G["Added 30-minute context window for follow-up messages"]
```

## Near-Term Target

```mermaid
flowchart TD
    In["Telegram message / photo"] --> Store["Store MESSAGE immediately"]
    Store --> Ack["Immediate ack"]
    Store --> Context["Load same-chat context<br/>last 30 minutes only"]
    Context --> Decide["Hybrid routing"]

    Decide --> Fast{"Common request?"}
    Fast -->|Yes| FastTool["Fast-path tools"]
    Fast -->|No| Agent["Fallback agent"]

    FastTool --> Count["count_notes"]
    FastTool --> Search["search_notes"]
    FastTool --> Tags["tag tools"]
    FastTool --> MergeSuggest["merge suggestion"]

    Agent --> Read["search / tag / recent / read_note"]
    Read --> Respond["plain text answer"]

    Decide --> Save{"Need to save?"}
    Save -->|Create| Create["Create note"]
    Save -->|Append| Append["Append note"]
    Save -->|Ignore| Ignore["Ignore as note"]

    Decide --> Img{"Image path?"}
    Img -->|OCR note| OCR["OCR + save"]
    Img -->|Unclear| Clarify["Ask follow-up"]

    MergeSuggest --> Proposal["Create merge proposal"]
    Proposal --> Approval{"Approved?"}
    Approval -->|Yes| Merge["Merge + delete duplicate"]
    Approval -->|No| Close["Close proposal"]

    Count --> Done["Reply to user"]
    Search --> Done
    Tags --> Done
    Respond --> Done
    Create --> Done
    Append --> Done
    Ignore --> Done
    OCR --> Done
    Clarify --> Done
    Merge --> Done
    Close --> Done
```
