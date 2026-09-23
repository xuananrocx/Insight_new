"""Per-request deep AI limits; null rounds and disabled timer are explicit."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class DeepAiOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    constraint_strategy: Literal['evidence', 'balanced', 'exploratory'] | None = None
    max_rounds: int | None = Field(10, ge=1, strict=True)
    time_limit_enabled: bool = False
    total_timeout_seconds: int = Field(360, ge=30, strict=True)
