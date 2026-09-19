"""Shared API and persistence contract; existing sessions still default to AI."""
from typing import Literal

RetrievalMode = Literal["basic", "deep", "ai", "deep_ai"]
VALID_MODES = ("basic", "deep", "ai", "deep_ai")
