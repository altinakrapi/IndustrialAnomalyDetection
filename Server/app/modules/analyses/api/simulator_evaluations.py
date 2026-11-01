from __future__ import annotations

from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.analyses.application.simulator_evaluation import (
    GroundTruthValidationError,
    create_simulator_evaluation,
    evaluation_to_csv,
    import_simulator_ground_truth,
    MAX_GROUND_TRUTH_BYTES,
)
from app.shared.models import (
    AuditLog,
    SimulatorEvaluation,
    SimulatorGroundTruthRun,
    User,
)

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")


class EvaluationRunInput(BaseModel):
    ground_truth_run_id: int = Field(gt=0)
    analysis_run_ids: list[int] = Field(min_length=1, max_length=32)


class SimulatorEvaluationInput(BaseModel):
    runs: list[EvaluationRunInput] = Field(min_length=1, max_length=10)
    tolerance_readings: int = Field(default=5, ge=0, le=100)


def _ground_truth_payload(run: SimulatorGroundTruthRun) -> dict:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "seed": run.seed,
        "purpose": run.purpose,
        "source_type": "simulated",
        "configuration": run.configuration,
        "device_ids": run.device_ids,
        "received_after": run.received_after,
        "received_before": run.received_before,
        "accepted_reading_count": run.accepted_reading_count,
        "imported_by": run.imported_by,
        "imported_at": run.imported_at,
    }


def _evaluation_payload(evaluation: SimulatorEvaluation) -> dict:
    return {
        "id": evaluation.id,
        "created_by": evaluation.created_by,
        "purpose": evaluation.purpose,
        "source_type": "simulated",
        "configuration": evaluation.configuration,
        "ground_truth_run_ids": evaluation.ground_truth_run_ids,
        "analysis_run_ids": evaluation.analysis_run_ids,
        "limitations": evaluation.limitations,
        "result": evaluation.result,
        "created_at": evaluation.created_at,
    }


@router.post("/ground-truth", status_code=status.HTTP_201_CREATED)
def import_ground_truth(
    file: UploadFile = File(...),
    purpose: Literal["evaluation", "development"] = Query(...),
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    if not (file.filename or "").lower().endswith(".jsonl"):
        raise HTTPException(
            status_code=415, detail="Upload a simulator ground-truth .jsonl file"
        )
    contents = file.file.read(MAX_GROUND_TRUTH_BYTES + 1)
    try:
        run = import_simulator_ground_truth(
            db, contents, purpose=purpose, imported_by=user.id
        )
        db.add(
            AuditLog(
                user_id=user.id,
                action="import",
                entity_type="simulator_ground_truth_run",
                entity_id=run.id,
                details={
                    "run_id": run.run_id,
                    "seed": run.seed,
                    "purpose": run.purpose,
                    "accepted_reading_count": run.accepted_reading_count,
                    "source_type": "simulated",
                },
            )
        )
        db.commit()
        db.refresh(run)
    except GroundTruthValidationError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This simulator run has already been imported",
        ) from error
    return _ground_truth_payload(run)


@router.get("/ground-truth")
def list_ground_truth(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    runs = db.scalars(
        select(SimulatorGroundTruthRun)
        .order_by(SimulatorGroundTruthRun.imported_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_ground_truth_payload(run) for run in runs]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_evaluation(
    payload: SimulatorEvaluationInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    try:
        evaluation = create_simulator_evaluation(
            db,
            runs=[
                {
                    "ground_truth_run_id": run.ground_truth_run_id,
                    "analysis_run_ids": run.analysis_run_ids,
                }
                for run in payload.runs
            ],
            tolerance_readings=payload.tolerance_readings,
            created_by=user.id,
        )
        db.add(
            AuditLog(
                user_id=user.id,
                action="create",
                entity_type="simulator_evaluation",
                entity_id=evaluation.id,
                details={
                    "ground_truth_run_ids": evaluation.ground_truth_run_ids,
                    "analysis_run_ids": evaluation.analysis_run_ids,
                    "tolerance_readings": payload.tolerance_readings,
                    "purpose": evaluation.purpose,
                },
            )
        )
        db.commit()
        db.refresh(evaluation)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _evaluation_payload(evaluation)


@router.get("")
def list_evaluations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    evaluations = db.scalars(
        select(SimulatorEvaluation)
        .order_by(SimulatorEvaluation.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_evaluation_payload(evaluation) for evaluation in evaluations]


@router.get("/{evaluation_id}")
def get_evaluation(
    evaluation_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    evaluation = db.get(SimulatorEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return _evaluation_payload(evaluation)


@router.get("/{evaluation_id}/csv")
def export_evaluation_csv(
    evaluation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    evaluation = db.get(SimulatorEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="simulator_evaluation",
            entity_id=evaluation.id,
            details={"format": "csv", "source_type": "simulated"},
        )
    )
    db.commit()
    return StreamingResponse(
        iter([evaluation_to_csv(evaluation)]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="simulator-evaluation-{evaluation.id}.csv"'
            )
        },
    )


def _evaluation_pdf(evaluation: SimulatorEvaluation) -> bytes:
    output = BytesIO()
    with PdfPages(output) as pdf:
        figure, axis = plt.subplots(figsize=(11, 8.5))
        axis.axis("off")
        lines = [
            f"Simulator evaluation #{evaluation.id}",
            "Source: simulated",
            f"Purpose: {evaluation.purpose}",
            f"Tolerance readings: {evaluation.configuration.get('tolerance_readings', '-')}",
            "",
            evaluation.limitations,
        ]
        axis.text(0.03, 0.97, "\n".join(lines), va="top", fontsize=10, wrap=True)
        pdf.savefig(figure, bbox_inches="tight")
        plt.close(figure)
    return output.getvalue()


@router.get("/{evaluation_id}/pdf")
def export_evaluation_pdf(
    evaluation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    evaluation = db.get(SimulatorEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="simulator_evaluation",
            entity_id=evaluation.id,
            details={"format": "pdf", "source_type": "simulated"},
        )
    )
    db.commit()
    return Response(
        content=_evaluation_pdf(evaluation),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="simulator-evaluation-{evaluation.id}.pdf"'
            )
        },
    )
