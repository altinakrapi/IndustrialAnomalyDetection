import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

import numpy as np

from app.core.config import settings

WINDOW_SIZE = 256
SAMPLE_RATE_HZ = 20_000
CHANNEL_FEATURES = (
    "rms",
    "std",
    "peak_to_peak",
    "kurtosis",
    "crest_factor",
    "fft_peak_hz",
    "spectral_centroid_hz",
    "spectral_bandwidth_hz",
    "spectral_energy",
)
FEATURE_NAMES = [
    f"bearing_{bearing}_{feature}"
    for bearing in range(1, 5)
    for feature in CHANNEL_FEATURES
]
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 10 * 1024**3
MAX_MEMBER_UNCOMPRESSED_BYTES = 128 * 1024**2
MAX_ARCHIVE_FILES = 100_000
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{2}$")
_TAR_DETAIL_PATTERN = re.compile(
    r"^(\S)\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+"
)


def feature_names_for_channels(channel_count: int) -> list[str]:
    if channel_count not in {4, 8}:
        raise ValueError(
            f"NASA IMS files must contain four or eight vibration channels; found {channel_count}."
        )
    prefix = "bearing" if channel_count == 4 else "sensor"
    return [
        f"{prefix}_{channel}_{feature}"
        for channel in range(1, channel_count + 1)
        for feature in CHANNEL_FEATURES
    ]


def _safe_member_path(filename: str) -> PurePosixPath:
    if "\\" in filename:
        raise ValueError("The archive contains an unsafe file path.")
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts or ":" in filename:
        raise ValueError("The archive contains an unsafe file path.")
    return path


def _is_timestamp_file(filename: str) -> bool:
    path = _safe_member_path(filename.rstrip("/"))
    name = path.name
    stem = name[:-4] if name.lower().endswith(".txt") else name
    return bool(_TIMESTAMP_PATTERN.fullmatch(stem))


def archive_entries(archive_path: str) -> list[zipfile.ZipInfo]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            if len(files) > MAX_ARCHIVE_FILES:
                raise ValueError("The archive contains too many files to import.")
            if sum(info.file_size for info in files) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("The expanded dataset exceeds the 10 GB import limit.")
            for info in files:
                _safe_member_path(info.filename)
                if info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("The archive contains a symbolic link.")
            candidates = [
                info for info in files if _is_timestamp_file(info.filename)
            ]
            if candidates:
                if any(
                    info.file_size > MAX_MEMBER_UNCOMPRESSED_BYTES
                    for info in candidates
                ):
                    raise ValueError("A vibration file exceeds the 128 MB per-file limit.")
                return sorted(candidates, key=lambda info: info.filename.casefold())
            containers = [
                info for info in files if PurePosixPath(info.filename).name == "IMS.7z"
            ]
            if len(containers) == 1:
                return containers
            raise ValueError(
                "The archive contains no NASA IMS vibration files or supported IMS.7z archive."
            )
    except zipfile.BadZipFile as error:
        raise ValueError("The uploaded file is not a valid ZIP archive.") from error


def _parse_timestamp(filename: str) -> datetime | None:
    name = PurePosixPath(filename).name
    stem = name[:-4] if name.lower().endswith(".txt") else name
    if not _TIMESTAMP_PATTERN.fullmatch(stem):
        return None
    try:
        return datetime.strptime(stem, "%Y.%m.%d.%H.%M.%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _load_measurements(contents: bytes, filename: str) -> np.ndarray | None:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    first_values = first_line.split()
    if len(first_values) not in {4, 8}:
        return None
    try:
        [float(value) for value in first_values]
    except ValueError:
        return None
    try:
        matrix = np.loadtxt(io.StringIO(text), dtype=np.float64)
    except (ValueError, TypeError) as error:
        raise ValueError(f"NASA IMS file {filename!r} contains invalid readings.") from error
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or matrix.shape[1] not in {4, 8}:
        raise ValueError(
            f"NASA IMS file {filename!r} must contain four or eight vibration channels."
        )
    if len(matrix) < 2 or not np.isfinite(matrix).all():
        raise ValueError(f"NASA IMS file {filename!r} has too few or non-finite readings.")
    return matrix


def _preprocess_matrix(
    matrix: np.ndarray,
    filename: str,
    first_row_number: int,
    window_size: int,
) -> Iterator[dict]:
    timestamp = _parse_timestamp(filename)
    channel_count = matrix.shape[1]
    for window_index, start in enumerate(
        range(0, len(matrix) - window_size + 1, window_size)
    ):
        window = matrix[start : start + window_size]
        mean = window.mean(axis=0)
        centered = window - mean
        variance = np.mean(centered * centered, axis=0)
        rms = np.sqrt(np.mean(window * window, axis=0))
        deviation = np.sqrt(variance)
        fourth_moment = np.mean(centered**4, axis=0)
        kurtosis = np.divide(
            fourth_moment,
            variance**2,
            out=np.zeros_like(fourth_moment),
            where=variance > 0,
        )
        peak = np.max(np.abs(window), axis=0)
        crest_factor = np.divide(
            peak, rms, out=np.zeros_like(peak), where=rms > 0
        )
        values: dict[str, float | str | int] = {
            "source_file": PurePosixPath(filename).name,
            "window_index": window_index + 1,
            "sample_start": start + 1,
            "sample_end": start + len(window),
        }
        prefix = "bearing" if channel_count == 4 else "sensor"
        for index in range(channel_count):
            channel = index + 1
            spectrum = np.abs(np.fft.rfft(centered[:, index]))
            frequencies = np.fft.rfftfreq(len(window), d=1 / SAMPLE_RATE_HZ)
            power = spectrum**2
            total_power = float(np.sum(power))
            peak_bin = int(np.argmax(spectrum[1:]) + 1)
            spectral_centroid = (
                float(np.sum(frequencies * power) / total_power)
                if total_power > 0
                else 0.0
            )
            spectral_bandwidth = (
                float(
                    np.sqrt(
                        np.sum(((frequencies - spectral_centroid) ** 2) * power)
                        / total_power
                    )
                )
                if total_power > 0
                else 0.0
            )
            values[f"{prefix}_{channel}_rms"] = float(rms[index])
            values[f"{prefix}_{channel}_std"] = float(deviation[index])
            values[f"{prefix}_{channel}_peak_to_peak"] = float(
                np.ptp(window[:, index])
            )
            values[f"{prefix}_{channel}_kurtosis"] = float(kurtosis[index])
            values[f"{prefix}_{channel}_crest_factor"] = float(crest_factor[index])
            values[f"{prefix}_{channel}_fft_peak_hz"] = float(
                frequencies[peak_bin]
            )
            values[f"{prefix}_{channel}_spectral_centroid_hz"] = spectral_centroid
            values[f"{prefix}_{channel}_spectral_bandwidth_hz"] = spectral_bandwidth
            values[f"{prefix}_{channel}_spectral_energy"] = (
                total_power / len(window)
            )
        window_timestamp = (
            timestamp
            + timedelta(seconds=(start + len(window) / 2) / SAMPLE_RATE_HZ)
            if timestamp
            else None
        )
        yield {
            "source_row_number": first_row_number + window_index,
            "timestamp": window_timestamp,
            "values": values,
        }


def _iter_file_rows(
    filenames: list[str],
    load_file: Callable[[str], bytes],
    window_size: int,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, Iterator[dict]]:
    first_matrix = None
    first_index = -1
    for index, filename in enumerate(filenames):
        matrix = _load_measurements(load_file(filename), filename)
        if matrix is not None:
            first_matrix = matrix
            first_index = index
            break
    if first_matrix is None:
        raise ValueError("No numeric NASA IMS vibration files were found in this test set.")
    channel_count = first_matrix.shape[1]
    feature_names_for_channels(channel_count)

    def rows() -> Iterator[dict]:
        row_number = 0
        processed_files = 0
        for index in range(first_index, len(filenames)):
            filename = filenames[index]
            matrix = (
                first_matrix
                if index == first_index
                else _load_measurements(load_file(filename), filename)
            )
            if matrix is None:
                processed_files += 1
                if on_progress and (
                    processed_files % 100 == 0
                    or processed_files == len(filenames) - first_index
                ):
                    on_progress()
                continue
            if matrix.shape[1] != channel_count:
                raise ValueError(
                    f"NASA IMS test set mixes four- and eight-channel files near {filename!r}."
                )
            count = max(0, (len(matrix) - window_size) // window_size + 1)
            yield from _preprocess_matrix(matrix, filename, row_number + 1, window_size)
            row_number += count
            processed_files += 1
            if on_progress and (
                processed_files % 100 == 0
                or processed_files == len(filenames) - first_index
            ):
                on_progress()
        if row_number == 0:
            raise ValueError("The archive contains no complete vibration windows.")

    return channel_count, rows()


def _tar_command(*arguments: str) -> str:
    try:
        result = subprocess.run(
            [settings.ARCHIVE_TOOL, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise ValueError(
            "NASA's nested IMS.7z and RAR archives require the configured "
            f"libarchive tool ({settings.ARCHIVE_TOOL})."
        ) from error
    if result.returncode:
        message = result.stderr.strip().splitlines()
        detail = message[-1] if message else "archive command failed"
        raise ValueError(f"NASA IMS archive could not be inspected: {detail[:300]}")
    return result.stdout


def _tar_members(archive_path: Path) -> list[tuple[str, int]]:
    names = [name for name in _tar_command("-tf", str(archive_path)).splitlines() if name]
    details = _tar_command("-tvf", str(archive_path)).splitlines()
    entries: list[tuple[str, int]] = []
    for name, line in zip(names, details, strict=True):
        match = _TAR_DETAIL_PATTERN.match(line)
        if not match:
            continue
        entry_type, size = match.groups()
        _safe_member_path(name.rstrip("/"))
        if entry_type in {"l", "h"}:
            raise ValueError("The NASA archive contains a symbolic or hard link.")
        entries.append((name.rstrip("/"), int(size)))
    if len(entries) != len(names):
        raise ValueError("The NASA archive has an unsupported member listing.")
    if len(entries) > MAX_ARCHIVE_FILES:
        raise ValueError("The NASA archive contains too many files to import.")
    total_size = sum(size for _, size in entries)
    if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("The expanded NASA dataset exceeds the 10 GB import limit.")
    if any(
        size > MAX_MEMBER_UNCOMPRESSED_BYTES
        for name, size in entries
        if _is_timestamp_file(name)
    ):
        raise ValueError("A NASA vibration file exceeds the 128 MB per-file limit.")
    return entries


def _validate_window_size(window_size: int) -> None:
    if not 32 <= window_size <= 4096:
        raise ValueError("Window size must be between 32 and 4096 samples.")


def iter_preprocessed_groups(
    archive_path: str,
    window_size: int = WINDOW_SIZE,
    on_progress: Callable[[], None] | None = None,
) -> Iterator[tuple[str, list[str], Iterator[dict]]]:
    _validate_window_size(window_size)
    entries = archive_entries(archive_path)
    direct_entries = [entry for entry in entries if _is_timestamp_file(entry.filename)]
    if direct_entries:
        grouped: dict[str, list[zipfile.ZipInfo]] = defaultdict(list)
        for entry in direct_entries:
            parts = PurePosixPath(entry.filename).parts
            group_name = parts[0] if len(parts) > 1 else "NASA IMS Bearings"
            grouped[group_name].append(entry)
        with zipfile.ZipFile(archive_path) as archive:
            for group_name, group_entries in sorted(grouped.items()):
                filenames = [entry.filename for entry in group_entries]
                channel_count, rows = _iter_file_rows(
                    filenames,
                    lambda filename: archive.read(filename),
                    window_size,
                    on_progress,
                )
                yield group_name, feature_names_for_channels(channel_count), rows
        return

    with tempfile.TemporaryDirectory(prefix="nasa-ims-import-") as temporary:
        work_dir = Path(temporary)
        inner_archive = work_dir / "IMS.7z"
        with zipfile.ZipFile(archive_path) as archive:
            container = entries[0]
            with archive.open(container) as source, inner_archive.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        seven_zip_entries = _tar_members(inner_archive)
        rar_archives = [
            name for name, _ in seven_zip_entries if name.lower().endswith(".rar")
        ]
        if not rar_archives:
            raise ValueError("The NASA IMS.7z archive contains no test-set RAR files.")
        nested_dir = work_dir / "nested"
        nested_dir.mkdir()
        _tar_command(
            "-xf",
            str(inner_archive),
            "-C",
            str(nested_dir),
            *rar_archives,
        )
        expanded_measurement_bytes = 0
        for rar_name in sorted(rar_archives):
            rar_path = nested_dir / Path(*PurePosixPath(rar_name).parts)
            rar_entries = _tar_members(rar_path)
            measurement_names = sorted(
                name
                for name, _ in rar_entries
                if _is_timestamp_file(name)
            )
            if not measurement_names:
                continue
            expanded_measurement_bytes += sum(
                size
                for name, size in rar_entries
                if _is_timestamp_file(name)
            )
            if expanded_measurement_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("The expanded NASA dataset exceeds the 10 GB import limit.")
            extraction_dir = work_dir / Path(rar_path.stem)
            extraction_dir.mkdir()
            _tar_command("-xf", str(rar_path), "-C", str(extraction_dir))
            channel_count, rows = _iter_file_rows(
                measurement_names,
                lambda filename: (
                    extraction_dir / Path(*PurePosixPath(filename).parts)
                ).read_bytes(),
                window_size,
                on_progress,
            )
            yield (
                Path(rar_name).stem,
                feature_names_for_channels(channel_count),
                rows,
            )


def iter_preprocessed_windows(
    archive_path: str,
    entries: list[zipfile.ZipInfo],
    window_size: int = WINDOW_SIZE,
) -> Iterator[dict]:
    _validate_window_size(window_size)
    with zipfile.ZipFile(archive_path) as archive:
        row_number = 0
        valid_files = 0
        for info in entries:
            try:
                contents = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError) as error:
                raise ValueError(
                    f"NASA IMS file {info.filename!r} is damaged or encrypted."
                ) from error
            matrix = _load_measurements(contents, info.filename)
            if matrix is None:
                continue
            valid_files += 1
            row_count = max(0, (len(matrix) - window_size) // window_size + 1)
            yield from _preprocess_matrix(
                matrix, info.filename, row_number + 1, window_size
            )
            row_number += row_count
        if valid_files == 0:
            raise ValueError(
                "No numeric NASA IMS vibration files were found in the archive."
            )
