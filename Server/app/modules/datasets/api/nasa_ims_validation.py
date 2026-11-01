from __future__ import annotations

from typing import Literal
from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.datasets.application.nasa_ims_validation import (
    LIMITATIONS,
    NasaIMSValidationInProgress,
    create_nasa_ims_validation,
    nasa_ims_validation_to_csv,
)
from app.shared.models import (
    AuditLog,
    NasaIMSBearingFact,
    NasaIMSValidation,
    User,
)
from app.workers.nasa_ims_validation import run_nasa_ims_validation

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")


class NasaIMSValidationInput(BaseModel):
    dataset_id: int = Field(gt=0)
    healthy_period_fraction: float = Field(default=0.2, gt=0, le=0.5)
    sustained_alert_windows: int = Field(default=5, ge=1, le=100)
    time_bin_count: int = Field(default=100, ge=10, le=500)
    snapshot_flag_fraction: float = Field(default=0.2, gt=0, le=1)
    sustained_alert_snapshots: int = Field(default=3, ge=1, le=100)
    snapshot_burn_in_snapshots: int = Field(default=63, ge=0, le=983)


def _payload(validation: NasaIMSValidation, *, include_result: bool) -> dict:
    data = {
        "id": validation.id,
        "dataset_id": validation.dataset_id,
        "created_by": validation.created_by,
        "status": validation.status,
        "source_type": "dataset",
        "configuration": validation.configuration,
        "analysis_run_ids": validation.analysis_run_ids,
        "limitations": validation.limitations,
        "error_message": validation.error_message,
        "created_at": validation.created_at,
        "started_at": validation.started_at,
        "completed_at": validation.completed_at,
    }
    if include_result:
        data["result"] = validation.result
    return data


@router.get("/facts")
def list_verified_facts(
    test_set: Literal["1st_test", "2nd_test", "3rd_test"] | None = Query(
        default=None
    ),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(NasaIMSBearingFact).order_by(
        NasaIMSBearingFact.test_set, NasaIMSBearingFact.bearing_number
    )
    if test_set is not None:
        statement = statement.where(NasaIMSBearingFact.test_set == test_set)
    return [
        {
            "test_set": item.test_set,
            "bearing_number": item.bearing_number,
            "channel_numbers": item.channel_numbers,
            "is_failed": item.is_failed,
            "failure_type": item.failure_type,
            "healthy_bearing_count": item.healthy_bearing_count,
            "failed_bearing_count": item.failed_bearing_count,
            "source_document": item.source_document,
            "source_section": item.source_section,
            "source_excerpt": item.source_excerpt,
            "verified_at": item.verified_at,
        }
        for item in db.scalars(statement)
    ]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_validation(
    payload: NasaIMSValidationInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    try:
        validation = create_nasa_ims_validation(
            db,
            dataset_id=payload.dataset_id,
            healthy_period_fraction=payload.healthy_period_fraction,
            sustained_alert_windows=payload.sustained_alert_windows,
            time_bin_count=payload.time_bin_count,
            created_by=user.id,
            snapshot_flag_fraction=payload.snapshot_flag_fraction,
            sustained_alert_snapshots=payload.sustained_alert_snapshots,
            snapshot_burn_in_snapshots=payload.snapshot_burn_in_snapshots,
        )
        db.add(
            AuditLog(
                user_id=user.id,
                action="create",
                entity_type="nasa_ims_validation",
                entity_id=validation.id,
                details={
                    "dataset_id": validation.dataset_id,
                    "test_set": validation.configuration["test_set"],
                    "analysis_count": len(validation.analysis_run_ids),
                    "limitations": LIMITATIONS,
                },
            )
        )
        db.commit()
        db.refresh(validation)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NasaIMSValidationInProgress as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_nasa_ims_validation, validation.id)
    return _payload(validation, include_result=False)


@router.get("")
def list_validations(
    dataset_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(NasaIMSValidation).order_by(
        NasaIMSValidation.created_at.desc()
    )
    if dataset_id is not None:
        statement = statement.where(NasaIMSValidation.dataset_id == dataset_id)
    validations = db.scalars(statement.offset(offset).limit(limit))
    return [_payload(item, include_result=False) for item in validations]


@router.get("/{validation_id}")
def get_validation(
    validation_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    validation = db.get(NasaIMSValidation, validation_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="NASA IMS validation not found")
    return _payload(validation, include_result=True)


@router.get("/{validation_id}/csv")
def export_validation_csv(
    validation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    validation = db.get(NasaIMSValidation, validation_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="NASA IMS validation not found")
    if validation.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"NASA IMS validation is {validation.status}; CSV is available when completed",
        )
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="nasa_ims_validation",
            entity_id=validation.id,
            details={"format": "csv", "source_type": "dataset"},
        )
    )
    db.commit()
    return StreamingResponse(
        iter([nasa_ims_validation_to_csv(validation)]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nasa-ims-validation-{validation.id}.csv"'
            )
        },
    )


def _validation_pdf(validation: NasaIMSValidation) -> bytes:
    output = BytesIO()
    with PdfPages(output) as pdf:
        figure, axis = plt.subplots(figsize=(11, 8.5))
        axis.axis("off")
        lines = [
            f"NASA IMS validation #{validation.id}",
            f"Test set: {validation.configuration.get('test_set', '-')}",
            "Source: dataset",
            "Statistical evidence for human review, not fault diagnosis.",
            "2nd_test was used to choose the snapshot rule and burn-in.",
            "",
            validation.limitations or "",
        ]
        axis.text(0.03, 0.97, "\n".join(lines), va="top", fontsize=10, wrap=True)
        pdf.savefig(figure, bbox_inches="tight")
        plt.close(figure)
    return output.getvalue()


@router.get("/{validation_id}/pdf")
def export_validation_pdf(
    validation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    validation = db.get(NasaIMSValidation, validation_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="NASA IMS validation not found")
    if validation.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"NASA IMS validation is {validation.status}; PDF is available when completed",
        )
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="nasa_ims_validation",
            entity_id=validation.id,
            details={"format": "pdf", "source_type": "dataset"},
        )
    )
    db.commit()
    return Response(
        content=_validation_pdf(validation),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nasa-ims-validation-{validation.id}.pdf"'
            )
        },
    )
