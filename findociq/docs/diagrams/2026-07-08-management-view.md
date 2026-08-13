# Pipeline — management view (2026-07-08)

Visual grammar (consistent across all diagrams):

- **Rectangle** = task (verb phrase). **Diamond** = decision (condition only). **Stadium** = artifact/queue.
- **Color = who does it**: blue = deterministic code (zero tokens) · purple = Gemini (costs tokens) · teal = PaddleOCR (candidate) · red = human/manual.
- **Dashed border** = designed but not wired yet.
- Status: ✅ live · 🟡 partial · 🔴 missing/manual.

Rendered PNGs sit beside this file (`2026-07-08-mgmt-*.png`). Re-render via the mermaid MCP after edits.

## 0 — Executive overview

```mermaid
flowchart LR
  D0["0 · DISCOVER<br/>find sections and pages<br/>🟡 built, not wired"]
  S1["1 · ROUTE<br/>decide handling per page<br/>✅ live"]
  MB["M · MANIFEST<br/>group pages into jobs<br/>🔴 by hand today"]
  S2["2 · EXTRACT<br/>read each table<br/>✅ NSFR-proven"]
  S3["3 · LOAD<br/>HTML → database<br/>✅ live"]
  S4["4 · VERIFY<br/>numbers re-checked vs PDF<br/>✅ live"]
  S5["5 · STAMP<br/>rows → standard concepts<br/>✅ live"]
  C(["reports · chat"])
  PP["PaddleOCR structure challenger<br/>(spike running)"]

  D0 --> S1 --> MB --> S2 --> S3 --> S4 --> S5 --> C
  PP -.-> S2

  subgraph LEGEND["legend — color = who · dashed = designed, not wired"]
    L1["code · zero tokens"]
    L2["Gemini · costs tokens"]
    L3["PaddleOCR · candidate"]
    L4["human"]
    L5(["artifact / queue"])
  end

  class D0,S1,S3,S4,S5,L1 code
  class S2,L2 llm
  class L3 paddle
  class MB,L4 human
  class C,L5 art
  class PP paddlePlan

  classDef code fill:#dbeafe,stroke:#2a78d6,color:#0b3a6b
  classDef llm fill:#ede9fe,stroke:#7c5cbf,color:#3b2a63
  classDef paddle fill:#ccfbf1,stroke:#0d9488,color:#0f4c45
  classDef human fill:#ffe3e3,stroke:#d64545,color:#7a1f1f
  classDef art fill:#f7f7f5,stroke:#898781,color:#52514e
  classDef paddlePlan fill:#ccfbf1,stroke:#0d9488,color:#0f4c45,stroke-dasharray:6 4
```

## 1 — ROUTE (detail)

```mermaid
flowchart TD
  subgraph SIG["1a · READ SIGNALS — every page, ~0.35s, zero tokens"]
    T1["read running header → section id"]
    T2["detect ruled table fragments"]
    T3["score aligned number columns"]
    T4["measure share of numbers inside fragments"]
  end
  D1{"any numbers<br/>on page?"}
  D2{"≥ 80% of numbers<br/>inside fragments?"}
  D3{"< 50% inside AND<br/>strong column alignment?"}
  subgraph UB["1b · BUILD UNITS"]
    T5["one extraction unit per fragment"]
    T6["one main-table unit —<br/>decoy fragments dropped, logged"]
  end
  D4{"≥ 60% of a known template's<br/>column headers on page?"}
  subgraph TMPL["1c · TYPE THE UNIT"]
    T7["attach template + structure authority<br/>(ruled → geometry · borderless → registry)"]
    T8["leave unit untyped"]
  end
  SKIP(["skip — no call"])
  RQ(["review queue — don't guess"])
  RM(["route map + route JSON"])

  T1 --> T2 --> T3 --> T4 --> D1
  D1 -- no --> SKIP
  D1 -- yes --> D2
  D2 -- yes --> T5
  D2 -- no --> D3
  D3 -- yes --> T6
  D3 -- no --> RQ
  T5 --> D4
  T6 --> D4
  D4 -- yes --> T7
  D4 -- no --> T8
  T7 --> RM
  T8 --> RM

  class T1,T2,T3,T4,T5,T6,T7,T8,D1,D2,D3,D4 code
  class SKIP,RQ,RM art
  classDef code fill:#dbeafe,stroke:#2a78d6,color:#0b3a6b
  classDef art fill:#f7f7f5,stroke:#898781,color:#52514e
```

## M — MANIFEST BUILDER (today vs target)

```mermaid
flowchart TD
  RJ(["route JSONs — one per PDF"])
  TODAY["TODAY — analyst assembles by hand:<br/>banks, periods, unit pages, exclusions"]
  subgraph TARGET["TARGET — deterministic builder · ports located in legacy PASS2_v2"]
    P1["identify bank + reporting period<br/>from cover pages"]
    P2["group section pages into one unit<br/>per reporting period"]
    P3["mark spanning units and<br/>boundary-spill pages"]
    P4["carry no-table exclusions"]
  end
  FM(["fleet manifest"])

  RJ --> TODAY --> FM
  RJ -.-> P1
  P1 --> P2 --> P3 --> P4
  P4 -.-> FM

  class TODAY human
  class P1,P2,P3,P4 codePlan
  class RJ,FM art
  classDef human fill:#ffe3e3,stroke:#d64545,color:#7a1f1f
  classDef codePlan fill:#dbeafe,stroke:#2a78d6,color:#0b3a6b,stroke-dasharray:6 4
  classDef art fill:#f7f7f5,stroke:#898781,color:#52514e
```

## 2 — EXTRACT (detail)

```mermaid
flowchart TD
  FM(["fleet manifest"])
  D1{"unit's route<br/>class?"}
  T1["frame prompt: drawn grid is the anchor<br/>payload: PDF page"]
  T2["frame prompt: infer columns from alignment<br/>payload: PDF page + rendered image"]
  T3["extract table — one call per unit page"]
  D2{"call<br/>succeeded?"}
  T5["retry — same model, up to 3×"]
  D3{"still<br/>failing?"}
  FL(["FLAG unit — never swap model"])
  D4{"unit has<br/>more pages?"}
  T4["continuation call carrying the<br/>open table's column labels"]
  A1(["unit HTML — replayable at zero cost"])
  PP["PaddleOCR reads structure<br/>from geometry (spike)"]

  FM --> D1
  D1 -- bordered --> T1
  D1 -- borderless --> T2
  T1 --> T3
  T2 --> T3
  T3 --> D2
  D2 -- no --> T5 --> D3
  D3 -- yes --> FL
  D3 -- no --> T3
  D2 -- yes --> D4
  D4 -- yes --> T4 --> D4
  D4 -- no --> A1
  PP -.-> T3

  class T1,T2,T3,T4 llm
  class T5,D1,D2,D3,D4 code
  class FM,FL,A1 art
  class PP paddlePlan
  classDef code fill:#dbeafe,stroke:#2a78d6,color:#0b3a6b
  classDef llm fill:#ede9fe,stroke:#7c5cbf,color:#3b2a63
  classDef art fill:#f7f7f5,stroke:#898781,color:#52514e
  classDef paddlePlan fill:#ccfbf1,stroke:#0d9488,color:#0f4c45,stroke-dasharray:6 4
```

## 3/4/5 — LOAD · VERIFY · STAMP (detail)

```mermaid
flowchart TD
  A0(["unit HTML"])
  subgraph S3["3 · LOAD"]
    T1["parse HTML into cells"]
    T2["load into database — idempotent per doc"]
  end
  DB(["final.db"])
  subgraph S4["4 · VERIFY — cannot be switched off"]
    T3["re-find every loaded number,<br/>verbatim, in the source PDF text"]
    D1{"every number<br/>found?"}
  end
  FL(["FLAG — run fails"])
  subgraph S5["5 · STAMP"]
    T4["match rows to standard concepts"]
    D2{"row known to<br/>the template?"}
  end
  DR(["drift review queue"])
  TS(["cross-bank time series"])
  C1["slide reports"]
  C2["chat app — LLM writes only a query spec,<br/>code runs the SQL"]

  A0 --> T1 --> T2 --> DB --> T3 --> D1
  D1 -- no --> FL
  D1 -- yes --> T4 --> D2
  D2 -- no --> DR
  D2 -- yes --> TS
  TS --> C1
  TS --> C2

  class T1,T2,T3,T4,D1,D2,C1 code
  class C2 llm
  class A0,DB,FL,DR,TS art
  classDef code fill:#dbeafe,stroke:#2a78d6,color:#0b3a6b
  classDef llm fill:#ede9fe,stroke:#7c5cbf,color:#3b2a63
  classDef art fill:#f7f7f5,stroke:#898781,color:#52514e
```

## G — Open gaps panel

```mermaid
flowchart TD
  G1["MANIFEST · built by hand<br/>fix: port legacy unit-grouping + bank/period detection"]
  G2["EXTRACT · truncated Gemini response can load as complete<br/>fix: port legacy finish-reason guard → FLAG"]
  G3["EXTRACT · zero-cost replay accepts stale artifacts<br/>fix: prompt-hash sidecar check (legacy had one)"]
  G4["VERIFY · structure check designed, unwired —<br/>merge/shade authority computed but never read"]
  G5["FLEET · no duplicate-table detection across units<br/>(legacy fingerprint check is portable)"]
  G6["ROUTE · coverage dead zone — 36 UOB pages unresolved"]
  G7["QUEUES · review / FLAG / drift have no consumer"]
  G8["DISCOVER · TOC discovery validated, not feeding routing"]
  G9["TEMPLATES · KM1/LCR seeded, no instances loaded"]

  G1 ~~~ G2 ~~~ G3 ~~~ G4 ~~~ G5 ~~~ G6 ~~~ G7 ~~~ G8 ~~~ G9

  class G1 human
  class G2,G3,G4,G5,G6,G7,G8,G9 gap
  classDef human fill:#ffe3e3,stroke:#d64545,color:#7a1f1f
  classDef gap fill:#fff3d1,stroke:#c98500,color:#6b4a00
```
