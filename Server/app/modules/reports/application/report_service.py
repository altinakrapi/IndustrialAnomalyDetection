import csv
import io
import json
import textwrap

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.api.dependencies import require_roles
from app.modules.anomaly_detection.infrastructure.repository import (
    registered_device_anomaly_filter,
)
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    GeneratedReport,
    AuditLog,
    Dataset,
    Device,
    MaintenanceOrder,
    User,
)

router = APIRouter()
ANALYSTS = require_roles("administrator", "data_analyst")
REPORT_USERS = require_roles(
    "administrator", "data_analyst", "maintenance_technician"
)

REPORT_FOREST = "#20372a"
REPORT_GREEN = "#466b4c"
REPORT_INK = "#294431"
REPORT_MUTED = "#667466"
REPORT_BORDER = "#dce5dc"
REPORT_PALE = "#f1f5f0"
REPORT_CARD_COLORS = ["#466b4c", "#5d805f", "#7fa180", "#a5c2a6"]


def _report_page(title: str, subtitle: str, page_number: int):
    figure = Figure(figsize=(8.27, 11.69), facecolor="#f4f6ef")
    axis = figure.add_axes([0, 0, 1, 1])
    axis.set_axis_off()
    axis.add_patch(
        Rectangle((0, 0.855), 1, 0.145, transform=axis.transAxes, color=REPORT_FOREST)
    )
    axis.add_patch(
        Rectangle((0, 0.855), 0.018, 0.145, transform=axis.transAxes, color="#7fa180")
    )
    axis.add_patch(
        Circle((0.91, 0.93), 0.058, transform=axis.transAxes, color="#294431")
    )
    axis.add_patch(
        Circle((0.91, 0.93), 0.043, transform=axis.transAxes, color=REPORT_FOREST,
               fill=False, linewidth=1.4)
    )
    axis.add_patch(
        Circle((0.91, 0.93), 0.025, transform=axis.transAxes, color="#7fa180")
    )
    axis.text(
        0.07, 0.965, "INDUSTRIAL ANOMALY DETECTION", transform=axis.transAxes,
        color="#a5c2a6", fontsize=7, fontweight="bold", va="center"
    )
    axis.text(
        0.07, 0.92, title, transform=axis.transAxes, color="white",
        fontsize=16, fontweight="bold", va="center"
    )
    axis.text(
        0.07, 0.885, subtitle, transform=axis.transAxes, color="#e3ece3",
        fontsize=8.5, va="center"
    )
    axis.add_line(
        Line2D(
            [0.06, 0.94], [0.073, 0.073], transform=axis.transAxes,
            color=REPORT_BORDER, linewidth=0.9
        )
    )
    axis.text(
        0.06, 0.045, "EQUIPMENT INTELLIGENCE  /  GENERATED REPORT",
        transform=axis.transAxes, color=REPORT_MUTED, fontsize=6.5, fontweight="bold"
    )
    axis.text(
        0.94, 0.045, f"{page_number:02d}",
        transform=axis.transAxes, color=REPORT_GREEN, fontsize=8,
        ha="right", fontweight="bold"
    )
    return figure, axis


def _summary_page(
    pdf: PdfPages,
    title: str,
    subtitle: str,
    metrics: list[tuple[str, str]],
    details: list[tuple[str, str]],
) -> None:
    figure, axis = _report_page(title, subtitle, 1)
    axis.text(
        0.06, 0.82, "AT A GLANCE", transform=axis.transAxes,
        color=REPORT_GREEN, fontsize=7, fontweight="bold"
    )
    card_gap = 0.018
    card_width = (0.88 - card_gap * (len(metrics) - 1)) / len(metrics)
    for index, (label, value) in enumerate(metrics):
        x = 0.06 + index * (card_width + card_gap)
        axis.add_patch(
            FancyBboxPatch(
                (x, 0.655), card_width, 0.135, transform=axis.transAxes,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor="white", edgecolor=REPORT_BORDER, linewidth=0.7
            )
        )
        axis.add_patch(
            FancyBboxPatch(
                (x, 0.655), 0.009, 0.135, transform=axis.transAxes,
                boxstyle="round,pad=0.001,rounding_size=0.006",
                facecolor=REPORT_CARD_COLORS[index % len(REPORT_CARD_COLORS)],
                edgecolor="none"
            )
        )
        axis.text(
            x + 0.022, 0.748, label.upper(), transform=axis.transAxes,
            color=REPORT_MUTED, fontsize=6.2, fontweight="bold", va="center"
        )
        axis.text(
            x + 0.022, 0.695, value, transform=axis.transAxes,
            color=REPORT_INK, fontsize=14, fontweight="bold", va="center"
        )
    axis.text(
        0.06, 0.605, "REPORT DETAILS", transform=axis.transAxes,
        color=REPORT_FOREST, fontsize=10, fontweight="bold"
    )
    axis.add_line(
        Line2D([0.06, 0.94], [0.59, 0.59], transform=axis.transAxes,
               color=REPORT_BORDER, linewidth=0.8)
    )
    for index, (label, value) in enumerate(details):
        column = index % 2
        row = index // 2
        x = 0.06 + column * 0.46
        y = 0.55 - row * 0.105
        axis.add_patch(
            FancyBboxPatch(
                (x, y - 0.085), 0.42, 0.09, transform=axis.transAxes,
                boxstyle="round,pad=0.004,rounding_size=0.008",
                facecolor="white", edgecolor=REPORT_BORDER, linewidth=0.55
            )
        )
        axis.text(
            x + 0.012, y - 0.012, label.upper(), transform=axis.transAxes,
            color=REPORT_GREEN, fontsize=5.8, fontweight="bold", va="top"
        )
        axis.text(
            x + 0.012, y - 0.035,
            textwrap.fill(_report_value(value, 72), width=42),
            transform=axis.transAxes,
            color=REPORT_INK, fontsize=7.3, va="top", wrap=True
        )
    pdf.savefig(figure)


def _table_pages(
    pdf: PdfPages,
    title: str,
    section: str,
    columns: list[str],
    rows: list[list[str]],
    page_number: int,
    column_widths: list[float] | None = None,
) -> int:
    page_size = 15
    pages = [rows[index:index + page_size] for index in range(0, len(rows), page_size)]
    if not pages:
        pages = [[]]
    for page_index, page_rows in enumerate(pages):
        page = page_number + page_index
        figure, axis = _report_page(
            title, f"{section}  -  {datetime.now().strftime('%B %d, %Y')}", page
        )
        axis.text(
            0.06, 0.82, section.upper(), transform=axis.transAxes,
            color=REPORT_FOREST, fontsize=10, fontweight="bold"
        )
        if page_rows:
            table_height = min(0.64, 0.04 * (len(page_rows) + 1))
            table = axis.table(
                cellText=page_rows,
                colLabels=columns,
                colWidths=column_widths,
                cellLoc="left",
                bbox=(0.06, 0.78 - table_height, 0.88, table_height),
            )
            table.auto_set_font_size(False)
            table.set_fontsize(7.3)
            for (row_index, _), cell in table.get_celld().items():
                cell.set_edgecolor(REPORT_BORDER)
                cell.set_linewidth(0.6)
                cell.get_text().set_color(REPORT_INK)
                cell.PAD = 0.018
                if row_index == 0:
                    cell.set_facecolor(REPORT_FOREST)
                    cell.get_text().set_color("white")
                    cell.get_text().set_fontweight("bold")
                else:
                    cell.set_facecolor("white" if row_index % 2 else REPORT_PALE)
        else:
            axis.text(
                0.06, 0.75, "No records to display.", transform=axis.transAxes,
                color=REPORT_MUTED, fontsize=9
            )
        pdf.savefig(figure)
    return page_number + len(pages)


def _report_value(value: object, max_length: int = 100) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=True, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= max_length else f"{text[:max_length - 3]}..."


def analysis_report(
    analysis_id: int,
    include_dataset: bool = Query(default=False),
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> Response:
    analysis = db.get(AnalysisRun, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis.status != "completed":
        raise HTTPException(status_code=409, detail="Only completed analyses can be reported")
    dataset = analysis.dataset
    device = analysis.device or (dataset.device if dataset else None)
    measurement = analysis.equipment_measurement
    configuration = (analysis.metrics or {}).get("configuration", {})
    algorithm = (
        analysis.model_config.algorithm
        if analysis.model_config
        else configuration.get("algorithm", "Not recorded")
    )
    anomaly_filter = registered_device_anomaly_filter(
        include_dataset=include_dataset
    )
    anomalies = list(
        db.scalars(
            select(Anomaly)
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(Anomaly.analysis_run_id == analysis.id)
            .where(anomaly_filter)
            .order_by(Anomaly.anomaly_score.desc())
            .limit(20)
        )
    )
    flagged_count = db.scalar(
        select(func.count(Anomaly.id))
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .where(
            Anomaly.analysis_run_id == analysis.id,
            anomaly_filter,
        )
    ) or 0
    output = io.BytesIO()
    with PdfPages(output) as pdf:
        metrics = [
            ("Measurements", f"{analysis.total_records:,}"),
            ("Flagged for review", f"{flagged_count:,}"),
            (
                "Source rows",
                f"{dataset.row_count:,}" if dataset else "Equipment measurement",
            ),
            ("Status", analysis.status.title()),
            (
                "Data provenance",
                measurement.source_type if measurement else "dataset",
            ),
        ]
        details = [
            (
                "Equipment",
                f"{device.name} ({device.device_code})"
                if device
                else "Not linked",
            ),
            ("Dataset", dataset.name if dataset else "Registered equipment"),
            (
                "Model",
                analysis.model_config.name
                if analysis.model_config
                else configuration.get("pretrained_model", {}).get(
                    "name", algorithm
                ),
            ),
            ("Algorithm", algorithm),
            ("Analysis date", _report_value(analysis.created_at)),
            (
                "Execution duration",
                f"{analysis.metrics.get('execution_seconds', 'Not recorded')} seconds",
            ),
            (
                "Features",
                ", ".join(analysis.metrics.get("features", []))
                or ", ".join(configuration.get("features", []))
                or "Not recorded",
            ),
            ("Evaluation", _report_value(analysis.metrics.get("evaluation", "Not available"))),
        ]
        _summary_page(
            pdf,
            "ANOMALY ANALYSIS REPORT",
            f"{device.name if device else 'Registered equipment'}  -  "
            f"{dataset.name if dataset else 'Equipment measurement'}",
            metrics,
            details,
        )
        next_page = _table_pages(
            pdf,
            "ANOMALY ANALYSIS REPORT",
            "Flagged measurements for human review",
            ["Record", "Score", "Severity", "Review status", "Measurement values"],
            [
                [
                    str(anomaly.id),
                    f"{anomaly.anomaly_score:.4f}",
                    anomaly.severity.title(),
                    anomaly.status.replace("_", " ").title(),
                    _report_value(anomaly.feature_values),
                ]
                for anomaly in anomalies
            ],
            2,
            [0.08, 0.12, 0.15, 0.17, 0.48],
        )
        notice_figure, notice_axis = _report_page(
            "REPORT NOTES", "How to interpret this report", next_page
        )
        notice_axis.text(
            0.06, 0.82, "IMPORTANT", transform=notice_axis.transAxes,
            color=REPORT_GREEN, fontsize=10, fontweight="bold"
        )
        notice_axis.text(
            0.06, 0.77,
            "MODEL OUTPUTS SUPPORT REVIEW - NOT A FAULT DIAGNOSIS.",
            transform=notice_axis.transAxes, color=REPORT_INK, fontsize=10,
            wrap=True, va="top"
        )
        pdf.savefig(notice_figure)
    output.seek(0)
    db.add(
        GeneratedReport(
            title=f"Analysis report #{analysis.id}",
            report_type="analysis",
            file_format="pdf",
            parameters={"analysis_id": analysis.id},
            analysis_run_id=analysis.id,
            created_by=user.id,
        )
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="generate",
            entity_type="analysis_report",
            entity_id=analysis.id,
        )
    )
    db.commit()
    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{analysis.id}-report.pdf"'
        },
    )


def maintenance_export(
    device_id: int | None = None,
    assigned_to: int | None = None,
    status_filter: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    user: User = Depends(REPORT_USERS),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "title", "device", "device_source_type", "anomaly_id", "assignee_id", "priority", "status", "due_date", "resolution"]
    )
    query = select(MaintenanceOrder).order_by(MaintenanceOrder.created_at.desc())
    if device_id:
        query = query.where(MaintenanceOrder.device_id == device_id)
    if assigned_to:
        query = query.where(MaintenanceOrder.assigned_to == assigned_to)
    if status_filter:
        query = query.where(MaintenanceOrder.status == status_filter)
    if start_date:
        query = query.where(MaintenanceOrder.created_at >= start_date)
    if end_date:
        query = query.where(MaintenanceOrder.created_at <= end_date)
    orders = list(db.scalars(query))
    for order in orders:
        writer.writerow(
            [
                order.id,
                order.title,
                order.device.name if order.device else "Historical experiment",
                order.device.source_type if order.device else "dataset",
                order.anomaly_id,
                order.assigned_to,
                order.priority,
                order.status,
                order.due_date,
                order.resolution_notes,
            ]
        )
    db.add(
        GeneratedReport(
            title="Maintenance orders export",
            report_type="maintenance",
            file_format="csv",
            parameters={
                "device_id": device_id,
                "assigned_to": assigned_to,
                "status": status_filter,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            },
            created_by=user.id,
        )
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="maintenance_report",
            details={"rows": len(orders)},
        )
    )
    db.commit()
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="maintenance-orders.csv"'},
    )


def device_report(
    device_id: int,
    user: User = Depends(REPORT_USERS),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    datasets = list(
        db.scalars(select(Dataset).where(Dataset.device_id == device.id))
    )
    analyses = list(
        db.scalars(
            select(AnalysisRun)
            .where(
                or_(
                    AnalysisRun.device_id == device.id,
                    AnalysisRun.dataset.has(Dataset.device_id == device.id),
                )
            )
            .order_by(AnalysisRun.created_at.desc())
        )
    )
    registered_anomaly_counts = (
        dict(
            db.execute(
                select(Anomaly.analysis_run_id, func.count(Anomaly.id))
                .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
                .where(
                    AnalysisRun.id.in_([run.id for run in analyses]),
                    registered_device_anomaly_filter(),
                )
                .group_by(Anomaly.analysis_run_id)
            ).all()
        )
        if analyses
        else {}
    )
    orders = list(
        db.scalars(
            select(MaintenanceOrder)
            .where(MaintenanceOrder.device_id == device.id)
            .order_by(MaintenanceOrder.created_at.desc())
        )
    )
    output = io.BytesIO()
    with PdfPages(output) as pdf:
        analysis_rows = [
            [
                str(run.id),
                _report_value(run.created_at, 24),
                run.status.title(),
                f"{registered_anomaly_counts.get(run.id, 0):,}",
                _report_value(
                    run.model_config.name
                    if run.model_config
                    else (run.metrics or {}).get("configuration", {}).get(
                        "algorithm", "Not recorded"
                    ),
                    28,
                ),
            ]
            for run in analyses[:30]
        ]
        order_rows = [
            [
                str(order.id),
                _report_value(order.created_at, 24),
                order.status.replace("_", " ").title(),
                order.priority.title(),
                _report_value(order.title, 36),
            ]
            for order in orders[:30]
        ]
        summary_metrics = [
            ("Datasets", f"{len(datasets):,}"),
            ("Analyses", f"{len(analyses):,}"),
            (
                "Flagged measurements",
                f"{sum(registered_anomaly_counts.values()):,}",
            ),
            ("Maintenance orders", f"{len(orders):,}"),
        ]
        _summary_page(
            pdf,
            "EQUIPMENT HISTORY REPORT",
            f"{device.name}  -  {device.device_code}",
            summary_metrics,
            [
                ("Equipment type", device.device_type),
                ("Category", device.category or "Not specified"),
                ("Manufacturer / model", f"{device.manufacturer or 'Not specified'} / {device.model or 'Not specified'}"),
                ("Location", device.location or "Not specified"),
                ("Current status", device.status.title()),
                ("Measurement provenance", device.source_type),
            ],
        )
        next_page = _table_pages(
            pdf,
            "EQUIPMENT HISTORY REPORT",
            "Recent analysis runs (up to 30)",
            ["Run", "Date", "Status", "Flagged", "Model"],
            analysis_rows,
            2,
            [0.08, 0.22, 0.17, 0.13, 0.40],
        )
        _table_pages(
            pdf,
            "EQUIPMENT HISTORY REPORT",
            "Maintenance history (up to 30)",
            ["Order", "Date", "Status", "Priority", "Title"],
            order_rows,
            next_page,
            [0.08, 0.22, 0.20, 0.14, 0.36],
        )
    output.seek(0)
    db.add(
        GeneratedReport(
            title=f"Device report: {device.name}",
            report_type="device",
            file_format="pdf",
            parameters={"device_id": device.id},
            created_by=user.id,
        )
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="generate",
            entity_type="device_report",
            entity_id=device.id,
        )
    )
    db.commit()
    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="device-{device.id}-report.pdf"'
        },
    )


def report_history(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination values")
    query = select(GeneratedReport).order_by(GeneratedReport.created_at.desc())
    if user.role == "maintenance_technician":
        query = query.where(GeneratedReport.created_by == user.id)
        query = query.where(
            GeneratedReport.report_type.in_(["maintenance", "device", "anomalies"])
        )
    return [
        {
            "id": report.id,
            "title": report.title,
            "report_type": report.report_type,
            "file_format": report.file_format,
            "parameters": report.parameters,
            "created_at": report.created_at,
            "created_by": report.created_by,
        }
        for report in db.scalars(query.limit(limit).offset(offset))
    ]