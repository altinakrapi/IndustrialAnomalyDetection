import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import BackgroundTasks, HTTPException, UploadFile

from app.modules.datasets.application.import_nasa_ims import (
    persist_nasa_ims_archive,
)
from app.modules.datasets.application.nasa_ims import (
    FEATURE_NAMES,
    archive_entries,
    feature_names_for_channels,
    iter_preprocessed_groups,
    iter_preprocessed_windows,
)
from app.modules.datasets.api.router import initialize_nasa_ims, upload_dataset
from app.shared.models import Dataset, Device


class _DatasetImportSession:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        datasets = [value for value in self.added if isinstance(value, Dataset)]
        for index, dataset in enumerate(datasets, start=1):
            dataset.id = index

    def execute(self, statement, values) -> None:
        return None


class NasaImsPreprocessingTests(unittest.TestCase):
    def _make_archive(self, directory: str, filename: str = "2003.10.22.12.06.24") -> Path:
        archive_path = Path(directory) / "ims.zip"
        readings = np.column_stack(
            (
                np.arange(64, dtype=float),
                np.arange(64, dtype=float) * 2,
                np.arange(64, dtype=float) * 3,
                np.arange(64, dtype=float) * 4,
            )
        )
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                f"1st_test/{filename}",
                "\n".join(" ".join(map(str, row)) for row in readings),
            )
        return archive_path

    def test_timestamp_named_extensionless_files_are_preprocessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = self._make_archive(directory)
            entries = archive_entries(str(archive_path))
            rows = list(iter_preprocessed_windows(str(archive_path), entries, 32))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_row_number"], 1)
        self.assertEqual(rows[0]["timestamp"].year, 2003)
        self.assertEqual(rows[0]["values"]["source_file"], "2003.10.22.12.06.24")
        self.assertEqual(len(FEATURE_NAMES), 36)
        self.assertAlmostEqual(
            rows[0]["values"]["bearing_1_rms"],
            np.sqrt(np.mean(np.arange(32) ** 2)),
        )
        self.assertEqual(rows[0]["values"]["bearing_4_peak_to_peak"], 124.0)
        self.assertGreaterEqual(rows[0]["values"]["bearing_1_fft_peak_hz"], 0)
        self.assertGreater(rows[0]["values"]["bearing_1_spectral_energy"], 0)

    def test_timestamp_named_text_files_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = self._make_archive(directory, "2003.10.22.12.06.24.txt")
            entries = archive_entries(str(archive_path))
            rows = list(iter_preprocessed_windows(str(archive_path), entries, 32))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["timestamp"].minute, 6)

    def test_eight_channel_files_keep_all_sensor_features(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "eight-channels.zip"
            readings = np.column_stack(
                [np.arange(64, dtype=float) * channel for channel in range(1, 9)]
            )
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "1st_test/2003.10.22.12.06.24",
                    "\n".join(" ".join(map(str, row)) for row in readings),
                )
            entries = archive_entries(str(archive_path))
            rows = list(iter_preprocessed_windows(str(archive_path), entries, 32))

        self.assertEqual(len(feature_names_for_channels(8)), 72)
        self.assertIn("sensor_8_rms", rows[0]["values"])
        self.assertAlmostEqual(rows[0]["timestamp"].microsecond, 800)
        self.assertAlmostEqual(
            rows[0]["values"]["sensor_8_peak_to_peak"],
            8 * 31,
        )

    def test_group_iterator_keeps_test_sets_with_different_channel_counts_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "mixed-tests.zip"
            four_channels = np.column_stack(
                [np.arange(64, dtype=float) * channel for channel in range(1, 5)]
            )
            eight_channels = np.column_stack(
                [np.arange(64, dtype=float) * channel for channel in range(1, 9)]
            )
            with zipfile.ZipFile(archive_path, "w") as archive:
                for group, readings in (
                    ("1st_test", eight_channels),
                    ("2nd_test", four_channels),
                ):
                    archive.writestr(
                        f"{group}/2003.10.22.12.06.24",
                        "\n".join(" ".join(map(str, row)) for row in readings),
                    )
            groups = iter_preprocessed_groups(str(archive_path), 32)
            first_name, first_features, first_rows = next(groups)
            first_count = len(list(first_rows))
            second_name, second_features, second_rows = next(groups)
            second_count = len(list(second_rows))
            groups.close()

        self.assertEqual((first_name, len(first_features), first_count), ("1st_test", 72, 2))
        self.assertEqual((second_name, len(second_features), second_count), ("2nd_test", 36, 2))

    def test_fft_features_capture_a_known_tone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "tone.zip"
            samples = np.arange(256)
            tone = np.sin(2 * np.pi * 4 * samples / 256)
            readings = np.column_stack((tone, tone, tone, tone))
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "1st_test/2003.10.22.12.06.24",
                    "\n".join(" ".join(map(str, row)) for row in readings),
                )
            entries = archive_entries(str(archive_path))
            rows = list(iter_preprocessed_windows(str(archive_path), entries, 256))

        self.assertAlmostEqual(
            rows[0]["values"]["bearing_1_fft_peak_hz"],
            4 * 20_000 / 256,
        )

    def test_archive_paths_cannot_escape_the_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../2003.10.22.12.06.24", "1 2 3 4\n")

            with self.assertRaisesRegex(ValueError, "unsafe file path"):
                archive_entries(str(archive_path))

    def test_imported_test_sets_remain_independent_of_registered_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = self._make_archive(directory)
            db = _DatasetImportSession()
            with (
                patch("app.modules.datasets.application.import_nasa_ims.notify_user"),
                patch("app.modules.datasets.application.import_nasa_ims.notify_roles"),
            ):
                datasets = persist_nasa_ims_archive(
                    db,
                    archive_path,
                    "NASA IMS Bearings.zip",
                    user_id=1,
                    window_size=32,
                )

        self.assertEqual(len(datasets), 1)
        self.assertIsNone(datasets[0].device_id)
        self.assertFalse(any(isinstance(value, Device) for value in db.added))

    def test_nasa_ims_initialization_rejects_equipment_device_assignment(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            initialize_nasa_ims(BackgroundTasks(), device_id=1)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("historical experiments", raised.exception.detail)

    def test_nasa_ims_upload_rejects_equipment_device_assignment(self) -> None:
        upload = UploadFile(filename="bearings.zip", file=BytesIO(b"archive"))

        with self.assertRaises(HTTPException) as raised:
            upload_dataset(device_id=1, file=upload, user=None, db=None)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("historical experiments", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
