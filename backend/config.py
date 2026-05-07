from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM / API ---
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")
    MODEL_CLOUD: str = Field(
        default="claude-opus-4-7",
        description="Cloud model identifier (Anthropic)",
    )
    MODEL_ON_DEVICE: str = Field(
        default="models/gemma-2-2b-it/gemma-2-2b-it.Q4_K_M.gguf",
        description="Path to the on-device GGUF model file",
    )

    # --- Storage ---
    LANCEDB_PATH: str = Field(default="./data/lancedb", description="LanceDB directory")
    FACE_DB_PATH: str = Field(default="./data/faces", description="Face embeddings directory")

    # --- Routing ---
    ON_DEVICE_THRESHOLD: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Query complexity score below this threshold routes to on-device model",
    )

    # --- Memory ---
    MAX_MEMORY_CHUNKS: int = Field(default=10_000, description="Maximum stored episodic chunks")
    EMBEDDING_MODEL: str = Field(
        default="BAAI/bge-m3",
        description="Sentence-transformers model for dense embeddings",
    )

    # --- Perception ---
    WHISPER_MODEL: str = Field(
        default="small",
        description="Whisper model size (tiny | base | small | medium | large)",
    )
    FACE_MODEL: str = Field(
        default="buffalo_l",
        description="InsightFace recognition model pack name",
    )
    TTS_MODEL: str = Field(
        default="tts_models/multilingual/multi-dataset/xtts_v2",
        description="Coqui TTS model identifier",
    )

    # --- Federated Learning ---
    FL_SERVER_ADDRESS: str = Field(
        default="localhost:9090",
        description="Flower federated learning server address (host:port)",
    )
    FL_NUM_ROUNDS: int = Field(default=3, description="Number of federated learning rounds")

    # --- Server ---
    SERVER_HOST: str = Field(default="0.0.0.0", description="Uvicorn bind host")
    SERVER_PORT: int = Field(default=8000, description="Uvicorn bind port")
    CORS_ORIGINS: List[str] = Field(
        default=["*"],
        description="Allowed CORS origins (use ['*'] for dev)",
    )
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # --- Computed ---
    @computed_field  # type: ignore[misc]
    @property
    def data_dir(self) -> Path:
        """Parent directory of LANCEDB_PATH — all persisted data lives here."""
        return Path(self.LANCEDB_PATH).parent

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()
