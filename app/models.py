from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SheetResult(BaseModel):
    file_name: str
    status: str
    row_count: int
    column_count: int
    issues: List[str]
    column_names: List[str]


class ValidationResult(BaseModel):
    sheet_results: Dict[str, str]
    overall_decision: str
    reasons: List[str]


class ValidationBundle(BaseModel):
    per_file: List[SheetResult]
    summary: ValidationResult


class EngineResult(BaseModel):
    engine: str
    status: str
    payload: Dict[str, Any]


class PipelineResult(BaseModel):
    run_id: str
    validation: ValidationBundle
    normalized: Dict[str, Any]
    engines: List[EngineResult]
    planner: Dict[str, Any]
    export: Dict[str, Any]
    errors: Optional[List[str]] = None
