import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.datasets.application.nasa_ims_validation import (
    build_nasa_ims_validation_result,
)
from app.shared.database.session import SessionLocal
from app.shared.models import AnalysisRun, NasaIMSValidation
from app.workers.analysis_worker import run_analysis

logger = logging.getLogger(__name__)


def run_nasa_ims_validation(validation_id: int) -> None:
    db: Session = SessionLocal()
    try:
        validation = db.get(NasaIMSValidation, validation_id)
        if validation is None:
            logger.error(
                "Cannot run NASA IMS validation %s because it does not exist",
                validation_id,
            )
            return
        if validation.status != "queued":
            logger.warning(
                "NASA IMS validation %s is %s and will not be started",
                validation_id,
                validation.status,
            )
            return
        validation.status = "running"
        validation.started_at = datetime.now(timezone.utc)
        db.commit()
        analysis_run_ids = list(validation.analysis_run_ids)
    finally:
        db.close()

    try:
        for analysis_run_id in analysis_run_ids:
            with SessionLocal() as status_db:
                analysis = status_db.get(AnalysisRun, analysis_run_id)
                if analysis is None:
                    raise LookupError(
                        f"Analysis run {analysis_run_id} disappeared during validation"
                    )
                if analysis.status == "completed":
                    continue
            run_analysis(analysis_run_id)
            with SessionLocal() as status_db:
                analysis = status_db.get(AnalysisRun, analysis_run_id)
                if analysis is None:
                    raise LookupError(
                        f"Analysis run {analysis_run_id} disappeared during validation"
                    )
                if analysis.status != "completed":
                    raise RuntimeError(
                        f"Analysis run {analysis_run_id} failed: "
                        f"{analysis.error_message or analysis.status}"
                    )
        with SessionLocal() as result_db:
            validation = result_db.get(NasaIMSValidation, validation_id)
            if validation is None:
                raise LookupError(
                    f"NASA IMS validation {validation_id} disappeared during processing"
                )
            result = build_nasa_ims_validation_result(result_db, validation)
            validation.result = result
            validation.limitations = result["limitations"]
            validation.status = "completed"
            validation.error_message = None
            validation.completed_at = datetime.now(timezone.utc)
            result_db.commit()
    except Exception as error:
        logger.exception("NASA IMS validation %s failed", validation_id)
        with SessionLocal() as failure_db:
            validation = failure_db.get(NasaIMSValidation, validation_id)
            if validation is not None:
                validation.status = "failed"
                validation.error_message = str(error)
                validation.completed_at = datetime.now(timezone.utc)
                failure_db.commit()
