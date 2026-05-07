# Sahayak — Cognitive Prosthesis for Mild Cognitive Impairment

> **सहायक** (sahāyak) — *helper, companion* (Hindi)

An ambient, voice-first AI system for people with early-stage dementia or mild cognitive impairment (MCI). Sahayak runs on an Android phone + Bluetooth lapel microphone, continuously perceiving the user's environment (faces, conversations, location), maintaining an external episodic memory store, and proactively surfacing context through natural voice interaction.

**Resume story:** Integrates 8+ distinct ML/AI domains in a single deployed system — not a benchmark exercise, but a real-world pipeline that runs on consumer hardware.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Hardware Layer                                                      │
│  Android Phone + Boya M1 BT lapel mic + clip-on wide-angle lens     │
└──────────────────────────────────────────────────────────────────────┘
                           │  WebSocket (audio chunks)
                           │  REST (queries, memory, faces)
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI Backend  (localhost:8000)                                   │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Perception Layer                                              │ │
│  │  ┌────────────┐ ┌──────────────┐ ┌────────┐ ┌─────────────┐  │ │
│  │  │ ASR        │ │ Face Recog.  │ │  OCR   │ │    TTS      │  │ │
│  │  │ Whisper    │ │ InsightFace  │ │Tessera-│ │ XTTS v2     │  │ │
│  │  │ (fine-tune │ │ ArcFace emb  │ │  ct    │ │ voice clone │  │ │
│  │  │  on Indic) │ │ 512-dim vec  │ │        │ │             │  │ │
│  │  └─────┬──────┘ └──────┬───────┘ └────┬───┘ └─────────────┘  │ │
│  └────────┼───────────────┼──────────────┼──────────────────────┘ │
│           │               │              │                          │
│           ▼               ▼              ▼                          │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Memory Layer                                                  │ │
│  │  ┌──────────────────┐  ┌──────────────┐  ┌─────────────────┐ │ │
│  │  │ Episodic Memory  │  │  Semantic    │  │  Relationship   │ │ │
│  │  │ LanceDB vector   │  │  Profile     │  │  Graph          │ │ │
│  │  │ BGE-M3 1024-dim  │  │  (user prefs │  │  (JSON-backed)  │ │ │
│  │  │ ANN search       │  │  med sched.) │  │                 │ │ │
│  │  └──────────────────┘  └──────────────┘  └─────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│                              ▼                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Agent Graph (LangGraph StateGraph)                            │ │
│  │                                                                │ │
│  │  START → [Perceiver] → [Recaller] → [Planner] → [Speaker] → END│ │
│  │                                                                │ │
│  │  Perceiver : entity extraction, intent classification,        │ │
│  │              time-range resolution, face ID lookup            │ │
│  │  Recaller  : vector search episodic memory, semantic profile  │ │
│  │              query, medication log lookup                     │ │
│  │  Planner   : reasoning plan generation, routing decision      │ │
│  │  Speaker   : LLM response generation (Hindi/English mix)      │ │
│  └──────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│              ┌───────────────┴────────────────┐                     │
│              ▼                                ▼                     │
│  ┌───────────────────┐           ┌────────────────────────┐         │
│  │  Edge-Cloud Router│           │  Anomaly Detector      │         │
│  │  Complexity score │           │  meal_skip, med_skip,  │         │
│  │  → on_device      │           │  wandering, silence,   │         │
│  │  OR → cloud       │           │  routine_deviation     │         │
│  └─────┬─────────────┘           └────────────────────────┘         │
│        │                                                             │
│   ┌────┴────┐    ┌──────────┐                                       │
│   │Gemma-2B │    │  Claude  │                                       │
│   │INT4 GGUF│    │  Opus/   │                                       │
│   │llama.cpp│    │  Sonnet  │                                       │
│   └─────────┘    └──────────┘                                       │
└──────────────────────────────────────────────────────────────────────┘
          │                                │
          ▼                                ▼
┌──────────────────┐           ┌───────────────────────┐
│  Streamlit       │           │  Flower FL Server     │
│  Caregiver       │           │  :9090                │
│  Dashboard       │           │  FedAvg over MLP      │
│  :8501           │           │  (intervention timing)│
└──────────────────┘           └───────────────────────┘
```

---

## Technical Stack

### ML / AI Domains Covered

| Domain | Implementation | File |
|---|---|---|
| **Speech Recognition** | faster-whisper (IndicWhisper-small), Hindi/English code-mix, auto-detect language | `backend/perception/asr.py` |
| **Computer Vision** | InsightFace ArcFace (512-dim face embeddings), CLIP scene understanding, Tesseract OCR for medicine labels | `backend/perception/face.py`, `ocr.py` |
| **Text-to-Speech** | Coqui XTTS v2, multilingual, voice cloning (caregiver-consent) | `backend/perception/tts.py` |
| **Vector Search / RAG** | LanceDB ANN index, BGE-M3 1024-dim embeddings, time-range + person-filter search | `backend/memory/episodic.py` |
| **Knowledge Graph** | JSON-backed person relationship graph, natural language context generation | `backend/memory/graph_store.py` |
| **Agentic AI** | LangGraph StateGraph, 4-node DAG, tool-calling, conditional routing | `backend/agents/` |
| **LLM (Cloud)** | Anthropic Claude with prompt caching (`cache_control: ephemeral`), tool use, LLM-as-judge | `backend/llm/cloud.py` |
| **LLM (On-Device)** | llama.cpp Gemma-2-2B INT4 (Q4_K_M GGUF), ~8-12 tok/s on Snapdragon 8 Gen 2 | `backend/llm/on_device.py` |
| **Edge-Cloud Routing** | Heuristic complexity scorer (query length, medical terms, multi-hop keywords) → routes on-device or cloud | `backend/routing/router.py` |
| **Time-Series ML** | Routine baseline learning (rolling average), deviation detection with configurable grace windows | `backend/anomaly/routine.py`, `detector.py` |
| **Reinforcement Learning** | Contextual bandit (intervention timing), caregiver thumbs-up/down as reward signal | `backend/federation/client.py` |
| **Federated Learning** | Flower FedAvg, differential privacy on weight exports, min 2 clients | `backend/federation/` |
| **Evaluation** | 50-scenario synthetic benchmark, LLM-as-judge scoring, hallucination penalty, pass-rate reporting | `backend/eval/` |
| **Mobile** | Flutter (Android), WebSocket real-time audio streaming, camera face pipeline | `app/` |

### Key Libraries

```
# Backend
fastapi          uvicorn          langgraph        langchain-anthropic
anthropic        lancedb          sentence-transformers  (BGE-M3)
faster-whisper   insightface      onnxruntime      pytesseract
TTS              llama-cpp-python flwr             pydantic-settings
structlog        httpx            pyarrow          torch

# Dashboard
streamlit        plotly           pandas

# Flutter
flutter_riverpod  go_router       dio              record
audioplayers      web_socket_channel  camera       permission_handler
```

---

## Repository Structure

```
sahayak/
│
├── backend/                        # Python 3.11+ FastAPI application
│   ├── main.py                     # App entry point, lifespan, WebSocket /ws/{user_id}
│   ├── config.py                   # Pydantic BaseSettings (reads .env)
│   ├── schemas.py                  # Shared Pydantic models (MemoryChunk, Person, etc.)
│   ├── requirements.txt
│   ├── .env.example
│   │
│   ├── agents/                     # LangGraph multi-agent pipeline
│   │   ├── graph.py                # AgentGraph class + /agent/query endpoint
│   │   ├── perceiver.py            # Node 1: entity extraction + intent classification
│   │   ├── recaller.py             # Node 2: episodic + semantic memory retrieval
│   │   ├── planner.py              # Node 3: reasoning plan + routing decision
│   │   └── speaker.py              # Node 4: Hindi/English response generation
│   │
│   ├── memory/
│   │   ├── episodic.py             # LanceDB ANN store, BGE-M3 embeddings, CRUD
│   │   ├── semantic.py             # User profile, medication schedule, med logs
│   │   └── graph_store.py          # Person relationship graph (JSON-persisted)
│   │
│   ├── perception/
│   │   ├── asr.py                  # faster-whisper transcription, multilingual
│   │   ├── face.py                 # InsightFace registration + recognition
│   │   ├── ocr.py                  # Tesseract OCR + medicine label parser
│   │   └── tts.py                  # Coqui XTTS v2 synthesis + voice cloning
│   │
│   ├── llm/
│   │   ├── cloud.py                # Anthropic Claude wrapper (with prompt caching)
│   │   └── on_device.py            # llama.cpp Gemma-2-2B wrapper
│   │
│   ├── routing/
│   │   └── router.py               # Edge-cloud complexity scorer + router
│   │
│   ├── anomaly/
│   │   ├── routine.py              # Rolling-average routine learner
│   │   └── detector.py             # 5 anomaly types + background monitoring + webhooks
│   │
│   ├── federation/
│   │   ├── client.py               # Flower NumPyClient, contextual bandit, feedback CSV
│   │   └── server.py               # Flower FedAvg server + /federation-server API
│   │
│   └── eval/
│       ├── scenarios.py            # 50 synthetic Hindi/English dementia scenarios
│       └── judge.py                # EvalHarness, LLM-as-judge, async job runner
│
├── dashboard/
│   └── app.py                      # Streamlit caregiver dashboard (6 tabs)
│
├── app/                            # Flutter Android application
│   ├── pubspec.yaml
│   ├── lib/
│   │   ├── main.dart               # Entry: Hive init + ProviderScope
│   │   ├── app.dart                # GoRouter + warm orange Material 3 theme
│   │   ├── models/                 # MemoryChunk, Person, AnomalyEvent, ConversationMessage
│   │   ├── services/
│   │   │   ├── api_service.dart    # Dio HTTP client (15 endpoints), Riverpod provider
│   │   │   ├── audio_service.dart  # record pkg (16kHz WAV), audioplayers playback
│   │   │   └── websocket_service.dart  # WebSocket + exponential backoff reconnect
│   │   ├── providers/              # Riverpod state (user, conversation, settings)
│   │   └── screens/
│   │       ├── home_screen.dart    # Giant pulsing mic button, 4-state FSM
│   │       ├── conversation_screen.dart  # Chat bubble history
│   │       ├── memory_screen.dart  # Searchable chronological memory list
│   │       ├── faces_screen.dart   # Family registry + face enrollment
│   │       ├── caregiver_screen.dart   # Anomaly alerts + resolve
│   │       └── settings_screen.dart    # User ID, base URL, language, eval trigger
│   └── android/
│       └── app/src/main/AndroidManifest.xml
│
├── Makefile                        # Dev convenience targets
├── howtorun.txt                    # Step-by-step setup guide
└── README.md
```

---

## Data Models

### MemoryChunk
```python
class MemoryChunk(BaseModel):
    id: str                       # UUID
    user_id: str                  # patient identifier
    timestamp: datetime
    text: str                     # transcribed or observed text
    embedding: list[float]        # BGE-M3 1024-dim, stored in LanceDB
    people: list[str]             # person IDs from face recognition
    location: dict | None         # {"lat": float, "lon": float}
    tags: list[str]               # auto-tagged: ["meal", "medication", ...]
    session_id: str
    memory_type: str              # "episodic" | "semantic" | "procedural"
```

### AgentState (LangGraph TypedDict)
```python
class AgentState(TypedDict):
    query: str
    user_id: str
    retrieved_memories: list[dict]    # top-k episodic chunks
    identified_people: list[dict]     # face-recognized persons
    plan: list[str]                   # reasoning steps from planner
    response: str                     # final output from speaker
    routing_decision: str             # "on_device" | "cloud"
    confidence: float                 # 0-1 query complexity score
    error: str | None
    image_b64: str | None
    context: dict
```

---

## Agent Pipeline Detail

```
Query: "Kal jo aaye the, wo kaun the?" (Who came yesterday?)

┌─────────────────────────────────────────────────────────┐
│  Perceiver                                              │
│  ─ Intent: "person_recall"                             │
│  ─ Time range: yesterday 00:00 → 23:59 UTC             │
│  ─ Entities: {} (no name given)                        │
│  ─ Face lookup: None (no image)                        │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│  Recaller                                               │
│  ─ EpisodicMemory.query("who visited", filters={        │
│      user_id, start_time: yesterday_start,             │
│      end_time: yesterday_end}, k=5)                    │
│  ─ Returns: [{text:"Rahul aaya tha...", people:[p_id_1]}]│
│  ─ GraphStore.get_context_for_person(p_id_1)           │
│    → "Rahul — your son, last seen 2 days ago"          │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│  Planner                                                │
│  ─ Plan: ["person_identified", "compose_answer"]       │
│  ─ Routing: complexity=0.3 < threshold=0.7             │
│    → routing_decision: "on_device" (if Gemma loaded)  │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│  Speaker                                                │
│  ─ Uses Gemma-2-2B on-device                           │
│  ─ System: "You are Sahayak, warm memory assistant..." │
│  ─ Response: "Kal aapke bete Rahul aaye the. Aap donon │
│    ne chai pi aur baatein ki thi."                     │
└─────────────────────────────────────────────────────────┘

Latency breakdown (Snapdragon 8 Gen 2):
  ASR:        ~0.3s
  Perceiver:  ~0.5s (cloud entity extraction)
  Recaller:   ~0.1s (LanceDB ANN search)
  Planner:    ~0.4s (routing decision)
  Speaker:    ~0.8s (Gemma on-device @ 10 tok/s)
  Total p50:  ~2.1s  ← target <2.5s
```

---

## Anomaly Detection

5 anomaly types with configurable thresholds:

| Type | Trigger | Severity |
|---|---|---|
| `meal_skip` | Expected meal time + 1h, no meal memory | medium |
| `med_skip` | Medication time + 2h, no medication log | high |
| `wandering` | GPS >2km from home during 22:00–06:00 | high |
| `routine_deviation` | Any routine event >2h outside learned window | low–medium |
| `silence` | No memory entries for >4h during waking hours | medium |

Alerts delivered via:
- Caregiver dashboard (Streamlit polling)
- Webhook POST to caregiver's URL (configurable per user)
- In-app push notification (Flutter)

---

## Federated Learning

Personalization of intervention timing without sharing raw episodic data.

**Local model per device:**
- 2-layer MLP (6 input features → 32 → 1 output)
- Features: `[hour_of_day, day_of_week, recent_anomalies_count, last_interaction_gap_hours, routine_deviation_score, caregiver_feedback_rate]`
- Label: `caregiver_thumbs_up` (0/1)
- Optimizer: SGD, lr=0.01, 5 local epochs per round

**Aggregation:** FedAvg (Flower), minimum 2 clients, 3 rounds default

**Privacy:** Only model weight deltas leave the device. Raw episodic memories never transmitted.

---

## Evaluation Benchmark

50 synthetic dementia scenarios covering:

| Category | Count | Example query |
|---|---|---|
| `person_recall` | 8 | "Kal jo aaye the, wo kaun the?" |
| `event_recall` | 8 | "Aaj subah maine kya kiya?" |
| `medication_check` | 7 | "Meri dawai li kya maine?" |
| `multi_hop` | 7 | "Doctor ke baad Rahul ne kya kaha tha?" |
| `routine_check` | 5 | "Main generally kitne baje nashta karta hoon?" |
| `emotional_support` | 5 | "Mujhe kuch yaad nahi aa raha, kya hua aaj?" |
| `anomaly_context` | 5 | "Main kahaan gaya tha raat ko?" |
| `hallucination_trap` | 5 | (asks about events that never happened — system must not confabulate) |

**Scoring formula:**
```
score = min(1.0, max(0.0,
    judge_score * 0.6          # LLM-as-judge on judge_criteria
  + contains_score * 0.4       # expected_answer_contains string match
  - hallucination_penalty      # 0.3 per forbidden_claim found in response
))
pass_threshold = 0.6
```

---

## Quick Start

```bash
# Clone
git clone https://github.com/US30/sahayak.git
cd sahayak

# Backend
cp backend/.env.example backend/.env
# → add ANTHROPIC_API_KEY to backend/.env
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload

# Dashboard (new terminal)
streamlit run dashboard/app.py

# Flutter app (new terminal)
cd app && flutter pub get && flutter run
```

See `howtorun.txt` for full setup including on-device LLM, federated learning, and troubleshooting.

Interactive API docs: http://localhost:8000/docs

---

## Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| Phone | Android 10, 4GB RAM | Pixel 8a / Snapdragon 8 Gen 2+ (for on-device LLM) |
| Mic | Any BT or wired mic | Boya M1 (TRRS) / BOYA BY-WM3 wireless (~₹1,500–2,500) |
| Passive camera | Phone propped on charger | + ₹300 wide-angle clip-on lens |
| Backend server | 8GB RAM, Python 3.11 | 16GB RAM for all models loaded simultaneously |

---

## Development Roadmap

| Phase | Months | Status |
|---|---|---|
| 1 — Voice memory companion (ASR → embed → retrieve → answer) | M1–M2 | Complete (code) |
| 2 — Face recognition + proactive cueing + multi-agent | M3–M4 | Complete (code) |
| 3 — Anomaly detection + caregiver dashboard + eval harness | M5–M6 | Complete (code) |
| 4 — Federated personalization + pilot deployment + thesis | M7–M9 | In progress |

---

## Citing

If you use Sahayak code or ideas in academic work:

```
@misc{sahayak2026,
  author = {Sinha, Utkarsh},
  title  = {Sahayak: A Multi-Agent Cognitive Prosthesis for Mild Cognitive Impairment},
  year   = {2026},
  url    = {https://github.com/US30/sahayak}
}
```

---

## Acknowledgements

- [AI4Bharat](https://ai4bharat.iitm.ac.in/) — IndicWhisper multilingual ASR
- [InsightFace](https://github.com/deepinsight/insightface) — ArcFace face recognition
- [LanceDB](https://lancedb.com/) — embedded vector database
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) — multilingual embeddings
- [Coqui TTS](https://github.com/coqui-ai/TTS) — XTTS v2 voice synthesis
- [Flower](https://flower.ai/) — federated learning framework
- [LangGraph](https://langchain-ai.github.io/langgraph/) — agent orchestration
- [Anthropic Claude](https://docs.anthropic.com/) — cloud LLM + eval judge
