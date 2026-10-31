"""Download the pinned LibreHardwareMonitorLib release into vendor/lhm."""

import hashlib
import io
from pathlib import Path
import sys
import urllib.request
import zipfile

VERSION = "0.9.6"
ARCHIVE_URL = (
    "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/"
    f"v{VERSION}/LibreHardwareMonitor.zip"
)
ARCHIVE_SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
LICENSE_URL = (
    "https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/"
    f"v{VERSION}/LICENSE"
)
# GUI-only assemblies from the release are not needed to read sensors.
SKIP_PREFIXES = ("Aga.Controls", "OxyPlot", "Microsoft.Win32.TaskScheduler")
TARGET = Path(__file__).resolve().parents[1] / "vendor" / "lhm"


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main() -> int:
    marker = TARGET / "VERSION"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == VERSION:
        print(f"LibreHardwareMonitorLib {VERSION} already present in {TARGET}")
        return 0
    archive = download(ARCHIVE_URL)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        print(f"SHA-256 mismatch for {ARCHIVE_URL}: {digest}", file=sys.stderr)
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    for old in TARGET.glob("*.dll"):
        old.unlink()
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for name in bundle.namelist():
            if "/" in name or not name.lower().endswith(".dll") or name.startswith(SKIP_PREFIXES):
                continue
            (TARGET / name).write_bytes(bundle.read(name))
    (TARGET / "LICENSE-LibreHardwareMonitor.txt").write_bytes(download(LICENSE_URL))
    marker.write_text(VERSION + "\n", encoding="utf-8")
    print(f"Installed LibreHardwareMonitorLib {VERSION} into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
