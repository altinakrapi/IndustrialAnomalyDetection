from typing import Literal

from sqlalchemy import and_, case

from app.shared.models import AnalysisRun

RunKind = Literal[
    "live_reading",
    "scheduled_cycle",
    "on_demand_analysis",
    "validation",
]


def classify_analysis_run(analysis: AnalysisRun) -> RunKind:
    configuration = (analysis.metrics or {}).get("configuration", {})
    if configuration.get("nasa_ims_validation_id") is not None:
        return "validation"
    if (
        analysis.source_type == "equipment"
        and analysis.execution_type == "measurement"
        and analysis.total_records == 1
    ):
        return "live_reading"
    if analysis.execution_type in {"scheduled", "simulated"}:
        return "scheduled_cycle"
    return "on_demand_analysis"


def analysis_run_kind_expression():
    validation = AnalysisRun.metrics["configuration"]["nasa_ims_validation_id"].as_string().is_not(None)
    live_reading = and_(
        AnalysisRun.source_type == "equipment",
        AnalysisRun.execution_type == "measurement",
        AnalysisRun.total_records == 1,
    )
    scheduled_cycle = AnalysisRun.execution_type.in_(("scheduled", "simulated"))
    return case(
        (validation, "validation"),
        (live_reading, "live_reading"),
        (scheduled_cycle, "scheduled_cycle"),
        else_="on_demand_analysis",
    )
