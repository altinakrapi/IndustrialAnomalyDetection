from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.analyses.application.simulator_tuning import (
    development_evaluation_metrics,
    prepare_reserved_tuning_comparison,
    run_simulator_tuning,
)
from app.shared.models import (
    AuditLog,
    ModelConfig,
    SimulatorTunedConfig,
    SimulatorTuningRun,
    User,
)

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")


class TuningInput(BaseModel):
    ground_truth_run_ids: list[int] = Field(min_length=1, max_length=20)
    false_alarm_cap: float = Field(default=0.05, gt=0, le=1)


class SaveTuningInput(BaseModel):
    tuning_run_id: int = Field(gt=0)
    algorithm: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=150)


class ReservedComparisonInput(BaseModel):
    ground_truth_run_ids: list[int] = Field(min_length=2, max_length=2)
    evaluation_runs: list[dict] = Field(min_length=2, max_length=2)


DETECTOR_DEFAULTS = {
    "isolation_forest": {"algorithm": "isolation_forest", "contamination": 0.1, "n_estimators": 100},
    "z_score": {"algorithm": "z_score", "threshold": 3.0},
    "ewma": {"algorithm": "ewma", "lambda": 0.2, "L": 3.0, "baseline_window": 30},
    "cusum": {"algorithm": "cusum", "k": 0.5, "h": 5.0, "baseline_window": 30},
    "local_outlier_factor": {"algorithm": "local_outlier_factor", "n_neighbors": 20, "contamination": 0.05},
    "one_class_svm": {"algorithm": "one_class_svm", "nu": 0.05, "gamma": "scale"},
    "autoencoder": {"algorithm": "autoencoder", "hidden_sizes": [8, 4], "alpha": 0.0001, "threshold_percentile": 95},
}

RUN_7_SAVED_ALGORITHMS = frozenset(("ewma", "cusum", "z_score"))


def run_7_save_decision(row: dict) -> tuple[bool, str]:
    if row["algorithm"] not in RUN_7_SAVED_ALGORITHMS:
        if row["algorithm"] == "autoencoder":
            return False, "Autoencoder was tuned in run 7, but its seed-101 false-alarm rate exceeded the 5% cap; default kept."
        return False, "No selected tuning candidate in run 7 or no reliable improvement over the default on both development runs."

    reliable = all(
        item["tuned_f1"] > item["default_f1"]
        and item["tuned_false_alarm_rate"] <= 0.05
        for item in row["runs"]
    )
    if not reliable:
        return False, "No reliable improvement over the default on both development runs and within the false-alarm cap."
    return True, "Reliable improvement over the default on both development runs."


def tuning_payload(run: SimulatorTuningRun, db: Session | None = None) -> dict:
    chosen = (run.result or {}).get("chosen", {})
    scored_positions = sum(
        int(chosen.get(key, 0) or 0)
        for key in ("true_positive", "false_positive", "false_negative", "true_negative")
    )
    superseded_positions = {1: 476, 2: 476, 4: 1384, 5: 1384, 6: 1384}
    payload = {
        "id": run.id,
        "created_by": run.created_by,
        "status": run.status,
        "objective": run.objective,
        "false_alarm_cap": run.false_alarm_cap,
        "ground_truth_run_ids": run.ground_truth_run_ids,
        "data_seeds": run.data_seeds,
        "configuration": run.configuration,
        "candidates": run.candidates,
        "result": run.result,
        "runtime_seconds": run.runtime_seconds,
        "limitations": run.limitations,
        "created_at": run.created_at,
    }
    if run.id in superseded_positions and run.id != 7:
        payload["presentation"] = {
            "superseded_by_run_7": True,
            "metric_version": "earlier metric version",
            "scored_positions": scored_positions,
            "supersession_reason": (
                f"Stored confusion counts cover {scored_positions} scored positions "
                "and use an earlier metric version; run 7 covers 824 scored positions."
            ),
        }
    if db is not None and run.id == 7:
        payload["detector_comparison"] = tuning_comparison(db, run)
    return payload


def tuning_comparison(db: Session, run: SimulatorTuningRun) -> list[dict]:
    from app.modules.analyses.application.simulator_evaluation import (
        load_development_ground_truth_runs,
    )

    runs = load_development_ground_truth_runs(db, run.ground_truth_run_ids)
    selected = (run.result or {}).get("selected", {})
    rows = []
    for algorithm, default_parameters in DETECTOR_DEFAULTS.items():
        default_runs = []
        tuned_runs = []
        for ground_truth_run in runs:
            default_metrics, _ = development_evaluation_metrics(
                db, [ground_truth_run], default_parameters, tolerance_readings=5
            )
            default_runs.append(default_metrics)
            tuned_parameters = (
                selected.get(algorithm, {}).get("parameters", default_parameters)
            )
            tuned_metrics, _ = development_evaluation_metrics(
                db, [ground_truth_run], tuned_parameters, tolerance_readings=5
            )
            tuned_runs.append(tuned_metrics)
        rows.append({
            "algorithm": algorithm,
            "runs": [
                {
                    "run_id": ground_truth_run.id,
                    "seed": ground_truth_run.seed,
                    "default_f1": default_runs[index]["f1"],
                    "default_false_alarm_rate": default_runs[index]["false_alarm_rate"],
                    "tuned_f1": tuned_runs[index]["f1"],
                    "tuned_false_alarm_rate": tuned_runs[index]["false_alarm_rate"],
                }
                for index, ground_truth_run in enumerate(runs)
            ],
            "default_parameters": default_parameters,
            "tuned_parameters": selected.get(algorithm, {}).get(
                "parameters", default_parameters
            ),
            "decision": (
                "saved"
                if run.id == 7 and algorithm in ("ewma", "cusum", "z_score")
                else "default kept"
            ),
            "reason": (
                "Reliable improvement over the default on both development runs"
                if run.id == 7 and algorithm in ("ewma", "cusum", "z_score")
                else (
                    "Autoencoder was tuned in run 7, but its seed-101 false-alarm rate exceeded the 5% cap; default kept."
                    if algorithm == "autoencoder"
                    else
                    "No selected candidate in run 7"
                    if algorithm not in selected
                    else "Improvement was not reliable on both development runs"
                )
            ),
        })
    return rows


@router.post("/tuning-runs", status_code=status.HTTP_201_CREATED)
def create_tuning_run(
    payload: TuningInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = run_simulator_tuning(
            db,
            ground_truth_run_ids=payload.ground_truth_run_ids,
            false_alarm_cap=payload.false_alarm_cap,
            created_by=user.id,
        )
        db.add(AuditLog(
            user_id=user.id,
            action="tune",
            entity_type="simulator_tuning_run",
            entity_id=run.id,
            details={
                "ground_truth_run_ids": payload.ground_truth_run_ids,
                "false_alarm_cap": payload.false_alarm_cap,
                "objective": "maximize_f1_with_false_alarm_cap",
            },
        ))
        db.commit()
        db.refresh(run)
        return tuning_payload(run, db)
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/tuning-runs")
def list_tuning_runs(
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> list[dict]:
    runs = db.scalars(select(SimulatorTuningRun).order_by(SimulatorTuningRun.created_at.desc()))
    return [tuning_payload(run, db) for run in runs]


@router.get("/tuned-configs")
def list_tuned_configs(
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> list[dict]:
    configs = db.scalars(select(SimulatorTunedConfig).order_by(SimulatorTunedConfig.created_at.desc()))
    return [{
        "id": config.id,
        "tuning_run_id": config.tuning_run_id,
        "model_config_id": config.model_config_id,
        "name": config.name,
        "algorithm": config.algorithm,
        "parameters": config.parameters,
        "objective": config.objective,
        "false_alarm_cap": config.false_alarm_cap,
        "development_run_ids": config.development_run_ids,
        "development_seeds": config.development_seeds,
        "limitations": config.limitations,
        "evidence": config.evidence,
        "is_superseded": config.is_superseded,
        "superseded_reason": config.superseded_reason,
        "created_at": config.created_at,
    } for config in configs]


@router.post("/tuned-configs", status_code=status.HTTP_201_CREATED)
def save_tuned_config(
    payload: SaveTuningInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    tuning_run = db.get(SimulatorTuningRun, payload.tuning_run_id)
    if tuning_run is None:
        raise HTTPException(status_code=404, detail="Tuning run not found")
    selected = (tuning_run.result or {}).get("selected", {}).get(payload.algorithm)
    if not selected:
        raise HTTPException(status_code=422, detail="Algorithm has no selected tuning candidate")
    parameters = dict(selected["parameters"])
    if payload.algorithm not in DETECTOR_DEFAULTS:
        raise HTTPException(status_code=422, detail="Algorithm has no documented default parameters")
    if tuning_run.id != 7 or payload.algorithm not in RUN_7_SAVED_ALGORITHMS:
        raise HTTPException(
            status_code=422,
            detail="Only reliably improved EWMA, CUSUM, and Z-score configurations from run 7 may be saved",
        )
    comparison_row = next(
        (
            row for row in tuning_comparison(db, tuning_run)
            if row["algorithm"] == payload.algorithm
        ),
        None,
    )
    if comparison_row is None:
        raise HTTPException(status_code=422, detail="No development evidence for algorithm")
    should_save, save_reason = run_7_save_decision(comparison_row)
    if not should_save:
        raise HTTPException(status_code=422, detail=save_reason)
    algorithm_candidates = [
        candidate for candidate in (tuning_run.candidates or {}).get("items", [])
        if candidate.get("algorithm") == payload.algorithm
    ]
    try:
        model = ModelConfig(
            name=payload.name.strip(),
            algorithm=payload.algorithm,
            description="Saved simulator tuning candidate for simulated equipment analysis.",
            parameters=parameters,
            contamination=parameters.get("contamination"),
            created_by=user.id,
            is_active=True,
        )
        db.add(model)
        db.flush()
        tuned = SimulatorTunedConfig(
            tuning_run_id=tuning_run.id,
            model_config_id=model.id,
            name=payload.name.strip(),
            algorithm=payload.algorithm,
            parameters=parameters,
            objective=tuning_run.objective,
            false_alarm_cap=tuning_run.false_alarm_cap,
            development_run_ids=tuning_run.ground_truth_run_ids,
            development_seeds=tuning_run.data_seeds,
            limitations=tuning_run.limitations,
            evidence={
                "tuning_run_id": tuning_run.id,
                "selected_parameters": parameters,
                "default_parameters": DETECTOR_DEFAULTS[payload.algorithm],
                "objective": tuning_run.objective,
                "false_alarm_cap": tuning_run.false_alarm_cap,
                "data_used": {
                    "ground_truth_run_ids": tuning_run.ground_truth_run_ids,
                    "seeds": tuning_run.data_seeds,
                },
                "runtime_seconds": tuning_run.runtime_seconds,
                "candidate_count": len(algorithm_candidates),
                "default_vs_tuned_by_run": comparison_row["runs"],
                "mean_f1": {
                    "default": sum(row["default_f1"] for row in comparison_row["runs"])
                    / len(comparison_row["runs"]),
                    "tuned": sum(row["tuned_f1"] for row in comparison_row["runs"])
                    / len(comparison_row["runs"]),
                },
                "seed_to_seed_f1_spread": max(
                    row["tuned_f1"] - row["default_f1"]
                    for row in comparison_row["runs"]
                ) - min(
                    row["tuned_f1"] - row["default_f1"]
                    for row in comparison_row["runs"]
                ),
                "limitations": (
                    "Two seeds (101 and 202), about 500 readings each, development data only; "
                    "not an independent evaluation."
                ),
            },
            created_by=user.id,
        )
        db.add(tuned)
        db.flush()
        db.add(AuditLog(
            user_id=user.id,
            action="save",
            entity_type="simulator_tuned_config",
            entity_id=tuned.id,
            details={"name": tuned.name, "algorithm": tuned.algorithm, "parameters": parameters},
        ))
        db.commit()
        db.refresh(tuned)
        return {
            "id": tuned.id,
            "name": tuned.name,
            "algorithm": tuned.algorithm,
            "parameters": tuned.parameters,
            "model_config_id": tuned.model_config_id,
        }
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="A configuration with this name already exists") from error


@router.post("/tuning-runs/{tuning_run_id}/reserved-comparison")
def create_reserved_comparison(
    tuning_run_id: int,
    payload: ReservedComparisonInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    tuning_run = db.get(SimulatorTuningRun, tuning_run_id)
    if tuning_run is None:
        raise HTTPException(status_code=404, detail="Tuning run not found")
    try:
        comparison = prepare_reserved_tuning_comparison(
            db,
            tuning_run=tuning_run,
            ground_truth_run_ids=payload.ground_truth_run_ids,
            evaluation_runs=payload.evaluation_runs,
            created_by=user.id,
        )
        db.add(AuditLog(
            user_id=user.id,
            action="compare_reserved",
            entity_type="simulator_tuning_run",
            entity_id=tuning_run.id,
            details={
                "fingerprint": comparison["fingerprint"],
                "seeds": comparison["seeds"],
            },
        ))
        db.commit()
        db.refresh(tuning_run)
        return comparison
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409 if "already been generated" in str(error) else 422, detail=str(error)) from error
