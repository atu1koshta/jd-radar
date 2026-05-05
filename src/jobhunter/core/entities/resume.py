"""Resume domain entity.

Slim wrapper around the raw YAML body plus refresh metadata. The expensive,
typed view of the resume lives on `interpreted: InterpretedResume`, which is
populated by `ports.resume_interpreter.ResumeInterpreter` and cached as long
as `body_hash` is unchanged.

`body` always holds the verbatim YAML dict so any future top-level section
the user adds (certifications, publications, ...) is preserved with zero
schema churn.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jobhunter.core.entities.interpreted_resume import InterpretedResume


def canonical_body_hash(body: dict[str, Any]) -> str:
    """Stable sha256 hash of a resume body dict.

    Whitespace-only YAML edits (re-indent, key reorder) collapse to the same
    hash, so the interpreter cache survives cosmetic changes.
    """
    payload = json.dumps(body, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Resume(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = "resume:current"
    body: dict[str, Any] = Field(default_factory=dict)
    body_hash: str = ""

    # Refresh metadata: TTL gate compares `last_updated_at` against now().
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    source_url: str | None = None
    etag: str | None = None

    # Cached LLM interpretation. Cleared whenever body_hash changes.
    interpreted: InterpretedResume | None = None
