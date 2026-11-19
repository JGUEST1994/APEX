# 02. System component diagram

```mermaid
flowchart TB
  classDef done fill:#d4f4dd,stroke:#2b8a3e
  classDef planned fill:#f1f3f5,stroke:#868e96,stroke-dasharray: 4 3

  subgraph Clients
    UI[Dashboard - Next.js]:::planned
    CLI[apex CLI]:::done
  end

  API[FastAPI backend]:::done

  subgraph CoreA[Component A: core trading software]
    CFG[Config and secrets]:::done
    LOG[Logging with redaction]:::done
    CAL[Trading calendars]:::done
    MDI[Market data interface]:::done
    PRV[Providers: synthetic, CSV, Binance]:::done
    VAL[Validation and staleness]:::done
    ING[Ingestion and data versioning]:::done
    STR[Strategy interface and lifecycle]:::done
    BT[Backtester and metrics]:::planned
    TOUR[Tournament and leaderboard]:::planned
    RISK[Risk service and kill switch]:::planned
    EXE[Execution service and paper broker]:::planned
    PF[Portfolio and P&L]:::planned
  end

  subgraph CompB[Component B: internal intelligence]
    FEAT[Feature pipelines]:::planned
    TRAIN[Training jobs]:::planned
    MREG[Model registry, ONNX]:::planned
    REGIME[Regime and anomaly detection]:::planned
  end

  subgraph Research
    LIB[Research library]:::done
    DOCS[PDF, HTML, transcript extraction]:::planned
    VEC[Vector search, pgvector]:::planned
    JOUR[Research journal]:::done
  end

  subgraph CompC[Component C: optional external AI]
    AIIF[LanguageModel interface]:::done
    CLA[Claude adapter]:::planned
    OAI[OpenAI adapter]:::planned
    LOC[Local LLM adapter]:::planned
  end

  DB[(PostgreSQL / SQLite)]:::done

  UI --> API
  CLI --> ING
  CLI --> LIB
  API --> DB
  PRV --> MDI --> ING --> VAL
  ING --> DB
  CAL --> VAL
  STR --> BT --> DB
  BT --> TOUR
  TOUR --> EXE
  EXE --> RISK
  RISK --> EXE
  EXE --> PF --> DB
  FEAT --> TRAIN --> MREG --> REGIME --> STR
  DOCS --> LIB --> DB
  VEC --> DB
  AIIF -.optional.-> API
  CLA & OAI & LOC -.-> AIIF
```

Green = implemented and tested in Phase 1. Dashed grey = planned.

## Data flow for a backtest (Phase 5 target)

1. Operator or tournament scheduler selects strategy version + params + instrument + range.
2. Backtester loads bars from the database and computes their content hash (`dataset_sha256`).
3. Strategy produces target positions; backtester fills at next bar open with the instrument's `CostModel` plus configured latency.
4. Metrics and the full manifest (data hash, code commit, params, seed) are written to `experiments`.
5. Results appear on the leaderboard; nothing is promoted without passing the validation gates (see 06).
