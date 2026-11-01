import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES = []


def configure_postgres_dll_search_path() -> None:
    if sys.platform != "win32":
        return

    directories: set[Path] = set()
    configured_bin = os.environ.get("PG_BIN")
    if configured_bin:
        configured_path = Path(configured_bin)
        if not (configured_path / "libpq.dll").is_file():
            raise ValueError(f"PG_BIN does not contain libpq.dll: {configured_path}")
        directories.add(configured_path)

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            path = Path(entry)
            if (path / "libpq.dll").is_file():
                directories.add(path)

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    postgres_installations = program_files / "PostgreSQL"
    if postgres_installations.is_dir():
        directories.update(
            dll_path.parent
            for dll_path in postgres_installations.glob("*/bin/libpq.dll")
        )

    discovered_directories = [str(directory) for directory in directories]
    if discovered_directories:
        os.environ["PATH"] = os.pathsep.join(
            [*discovered_directories, os.environ.get("PATH", "")]
        )

    # Keep the handles alive so Windows continues searching these DLL directories.
    _DLL_DIRECTORY_HANDLES.extend(
        os.add_dll_directory(directory) for directory in discovered_directories
    )
