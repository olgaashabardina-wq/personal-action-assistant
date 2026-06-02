from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TextCreate(BaseModel):
    source_text: str = Field(..., min_length=1, description="Исходный рабочий текст")


class TextResponse(BaseModel):
    id: int
    source_text: str
    status: str
    needs_review: bool
    review_reason: str | None
    created_at: datetime

    model_config = {
        "from_attributes": True
    }


class AuditResponse(BaseModel):
    id: int
    action: str
    input: str
    output: str | None
    status: str
    error: str | None
    duration_ms: int
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

class ExtractedAction(BaseModel):
    title: str
    description: str
    assignee: str | None
    department: str | None = None
    deadline: str | None
    priority: Literal["low", "medium", "high", "critical"]
    status: Literal["new", "needs_clarification"]
    source_fragment: str


class UnclearItem(BaseModel):
    issue: str
    reason: str
    related_fragment: str


class AIAnalysisResult(BaseModel):
    summary: str
    actions: list[ExtractedAction]
    unclear_items: list[UnclearItem]
    overall_priority: Literal["low", "medium", "high", "critical"]
    needs_review: bool
    review_reason: str | None
    confidence: float = Field(..., ge=0, le=1)    
