import hashlib
import logging
import os
from pathlib import Path
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.datasets.application.import_nasa_ims import (
    persist_nasa_ims_archive,
)
from app.modules.datasets.application.nasa_ims import archive_entries
from app.shared.database.session import SessionLocal, engine
from app.shared.models import Dataset, DatasetImportJob, MonitoringSchedule

logger = logging.getLogger(__name__)

NASA_IMS_SOURCE_KEY = "nasa-ims-bearings"
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_PROGRESS_UPDATE_BYTES = 16 * 1024 * 1024
_IMPORT_ADVISORY_LOCK_ID = 1_229_790_278


def _update_job(
    *,
    status: str,
    downloaded_bytes: int | None = None,
    total_bytes: int | None = None,
    dataset_ids: list[int] | None = None,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    with SessionLocal.begin() as db:
        job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
        if job is None:
            raise RuntimeError("NASA IMS import status record was not created.")
        job.status = status
        if downloaded_bytes is not None:
            job.downloaded_bytes = downloaded_bytes
        if total_bytes is not None:
            job.total_bytes = total_bytes
        if dataset_ids is not None:
            job.dataset_ids = dataset_ids
        job.error_message = error_message
        job.updated_at = datetime.now(timezone.utc)
        job.completed_at = datetime.now(timezone.utc) if completed else None
        if completed and status == "completed":
            schedule = db.get(MonitoringSchedule, 1)
            if schedule is not None and schedule.enabled:
                schedule.next_run_at = datetime.now(timezone.utc)


def _download_archive(destination: Path, max_bytes: int) -> tuple[int, int | None]:
    parsed_url = urlsplit(settings.NASA_IMS_SOURCE_URL)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "phm-datasets.s3.amazonaws.com"
    ):
        raise ValueError(
            "NASA_IMS_SOURCE_URL must use the verified HTTPS NASA Bearings archive host."
        )
    temporary_path = destination.with_suffix(destination.suffix + ".part")
    request = Request(
        settings.NASA_IMS_SOURCE_URL,
        headers={"User-Agent": "IndustrialAnomalyDetection/1.0", "Accept-Encoding": "identity"},
    )
    downloaded = 0
    last_reported = 0
    try:
        with urlopen(request, timeout=60) as response:
            length_header = response.headers.get("Content-Length")
            total_bytes = int(length_header) if length_header else None
            if total_bytes is not None and total_bytes > max_bytes:
                raise ValueError(
                    f"The NASA archive is {total_bytes:,} bytes, exceeding the configured "
                    f"{max_bytes // (1024 * 1024)} MB import limit."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("wb") as output:
                while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise ValueError(
                            f"The NASA archive exceeds the configured "
                            f"{max_bytes // (1024 * 1024)} MB import limit."
                        )
                    output.write(chunk)
                    if downloaded - last_reported >= _PROGRESS_UPDATE_BYTES:
                        _update_job(
                            status="downloading",
                            downloaded_bytes=downloaded,
                            total_bytes=total_bytes,
                        )
                        last_reported = downloaded
    except HTTPError as error:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(
            f"The NASA archive download failed with HTTP status {error.code}."
        ) from error
    except URLError as error:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"The NASA archive could not be reached: {error.reason}.") from error
    except TimeoutError as error:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("The NASA archive download timed out.") from error
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("The NASA archive could not be saved to local storage.") from error
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    if not downloaded:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("The NASA archive download was empty.")
    if total_bytes is not None and downloaded != total_bytes:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(
            f"The NASA archive download was incomplete ({downloaded:,} of "
            f"{total_bytes:,} bytes)."
        )
    os.replace(temporary_path, destination)
    return downloaded, total_bytes


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _find_duplicate_datasets(db: Session, source_hash: str) -> list[Dataset]:
    seen_paths: set[Path] = set()
    matching = []
    for dataset in db.scalars(
        select(Dataset).where(Dataset.file_format == "nasa_ims")
    ):
        path = Path(dataset.file_path)
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        if _archive_sha256(path) == source_hash:
            matching.append(dataset)
    return matching


def _run_nasa_ims_initialization() -> None:
    archive_path = Path(settings.UPLOAD_DIR).resolve() / "nasa-ims-bearings.zip"
    try:
        with SessionLocal() as db:
            job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
            if job is None or job.requested_by is None:
                raise RuntimeError("NASA IMS initialization is missing its import settings.")
            user_id = job.requested_by
            max_bytes = settings.NASA_IMS_MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
            window_size = 256
            existing_path = archive_path.is_file()

        if existing_path:
            downloaded = archive_path.stat().st_size
            if downloaded > max_bytes:
                archive_path.unlink(missing_ok=True)
                existing_path = False
        if existing_path:
            archive_entries(str(archive_path))
            _update_job(
                status="extracting",
                downloaded_bytes=downloaded,
                total_bytes=downloaded,
            )
        else:
            _update_job(status="downloading", downloaded_bytes=0, total_bytes=0)
            downloaded, total_bytes = _download_archive(archive_path, max_bytes)
            _update_job(
                status="extracting",
                downloaded_bytes=downloaded,
                total_bytes=total_bytes or downloaded,
            )

        archive_entries(str(archive_path))
        source_hash = _archive_sha256(archive_path)
        with SessionLocal() as db:
            duplicates = _find_duplicate_datasets(db, source_hash)
            if duplicates:
                dataset_ids = sorted({dataset.id for dataset in duplicates})
                _update_job(
                    status="completed",
                    downloaded_bytes=downloaded,
                    total_bytes=downloaded,
                    dataset_ids=dataset_ids,
                    completed=True,
                )
                return
            job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
            if job is None or job.requested_by is None:
                raise RuntimeError("NASA IMS initialization settings are unavailable.")
            user_id = job.requested_by

        _update_job(status="extracting", downloaded_bytes=downloaded)
        with SessionLocal.begin() as db:
            datasets = persist_nasa_ims_archive(
                db=db,
                archive_path=archive_path,
                source_filename="NASA IMS Bearings.zip",
                user_id=user_id,
                window_size=window_size,
                description=(
                    "NASA IMS Bearings dataset downloaded from the NASA Prognostics "
                    "Data Repository and preprocessed into vibration windows."
                ),
                on_progress=lambda: _update_job(
                    status="processing",
                    downloaded_bytes=downloaded,
                ),
            )
            dataset_ids = [dataset.id for dataset in datasets]
        _update_job(
            status="completed",
            downloaded_bytes=downloaded,
            total_bytes=downloaded,
            dataset_ids=dataset_ids,
            completed=True,
        )
    except Exception as error:
        logger.exception("NASA IMS automatic import failed")
        try:
            _update_job(
                status="failed",
                error_message=str(error)[:2000],
            )
        except Exception:
            logger.exception("Could not persist NASA IMS import failure status")
        raise


def run_nasa_ims_initialization() -> None:
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _IMPORT_ADVISORY_LOCK_ID},
        )
        lock_connection.commit()
        if not acquired:
            logger.info("NASA IMS import is already running in another worker")
            return
        stop_heartbeat = threading.Event()

        def refresh_heartbeat() -> None:
            while not stop_heartbeat.wait(30):
                try:
                    with SessionLocal.begin() as db:
                        job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
                        if job is None or job.status not in {
                            "queued",
                            "downloading",
                            "extracting",
                            "processing",
                        }:
                            return
                        job.updated_at = datetime.now(timezone.utc)
                except Exception:
                    logger.exception("Could not refresh NASA IMS import heartbeat")

        heartbeat = threading.Thread(
            target=refresh_heartbeat,
            name="nasa-ims-import-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            _run_nasa_ims_initialization()
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _IMPORT_ADVISORY_LOCK_ID},
            )
            lock_connection.commit()


async def nasa_ims_import_recovery_loop() -> None:
    import asyncio

    while True:
        try:
            with SessionLocal() as db:
                job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
                should_resume = job is not None and job.status in {
                    "queued",
                    "downloading",
                    "extracting",
                    "processing",
                }
            if should_resume:
                await asyncio.to_thread(run_nasa_ims_initialization)
        except Exception:
            logger.exception("NASA IMS import recovery poll failed")
        await asyncio.sleep(30)
