#!/usr/bin/env python3
"""Diagnostic tool for checking GStreamer installation and setup."""

import shutil
import subprocess
import sys

CODECS: dict[str, tuple[str, str, str]] = {
    "h264": ("x264enc", "rtph264pay", "gst-plugins-ugly"),
    "openh264": ("openh264enc", "rtph264pay", "gst-plugins-bad"),
    "h265": ("x265enc", "rtph265pay", "gst-plugins-bad"),
    "vp8": ("vp8enc", "rtpvp8pay", "gst-plugins-good"),
    "av1": ("av1enc", "rtpav1pay", "gst-plugins-bad"),
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"


def run_quiet(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a command and return the result, suppressing output."""
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def check_binary_on_path() -> bool:
    """Check if gst-launch-1.0 is on PATH."""
    found = shutil.which("gst-launch-1.0")
    if found:
        print(f"  [{PASS}] gst-launch-1.0 found at {found}")
        return True
    print(f"  [{FAIL}] gst-launch-1.0 not found on PATH")
    return False


def check_inspect_on_path() -> bool:
    """Check if gst-inspect-1.0 is on PATH."""
    found = shutil.which("gst-inspect-1.0")
    if found:
        print(f"  [{PASS}] gst-inspect-1.0 found at {found}")
        return True
    print(f"  [{FAIL}] gst-inspect-1.0 not found on PATH")
    return False


def check_gstreamer_version() -> str | None:
    """Print GStreamer version."""
    try:
        result = run_quiet(["gst-launch-1.0", "--version"])
        if result.returncode == 0:
            version_line = result.stdout.decode().strip().splitlines()[0]
            print(f"  [{PASS}] {version_line}")
            return version_line
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print(f"  [{FAIL}] Could not determine GStreamer version")
    return None


def check_element(element: str, label: str | None = None) -> bool:
    """Check if a GStreamer element is available."""
    display = label or element
    try:
        result = run_quiet(["gst-inspect-1.0", element])
        if result.returncode == 0:
            print(f"  [{PASS}] {display}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print(f"  [{FAIL}] {display}")
    return False


def check_camera(device_index: int = 0) -> bool:
    """Try to access a camera with a short test pipeline."""
    print(f"  Trying autovideosrc device-index={device_index} ...")
    try:
        result = run_quiet(
            [
                "gst-launch-1.0",
                "autovideosrc", f"device-index={device_index}",
                "!", "fakesink", "num-buffers=1",
            ],
            timeout=15,
        )
        if result.returncode == 0:
            print(f"  [{PASS}] Camera {device_index} accessible")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print(f"  [{FAIL}] Camera {device_index} not accessible")
    return False


def get_install_hint() -> str:
    """Return platform-specific install instructions."""
    if sys.platform == "darwin":
        return (
            "  macOS (Homebrew):\n"
            "    brew install gstreamer gst-plugins-base gst-plugins-good "
            "gst-plugins-bad gst-plugins-ugly"
        )
    if sys.platform == "win32":
        return (
            "  Windows:\n"
            "    Download the official installer from\n"
            "    https://gstreamer.freedesktop.org/download/"
        )
    # Linux
    return (
        "  Debian/Ubuntu:\n"
        "    sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base \\\n"
        "      gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly\n"
        "\n"
        "  Fedora:\n"
        "    sudo dnf install gstreamer1-tools gstreamer1-plugins-base \\\n"
        "      gstreamer1-plugins-good gstreamer1-plugins-bad-free "
        "gstreamer1-plugins-ugly-free"
    )


def main() -> None:
    """Run all diagnostic checks."""
    print("GStreamWebcam Doctor")
    print("=" * 40)
    issues: list[str] = []

    print("\n1. GStreamer binaries")
    has_launch = check_binary_on_path()
    has_inspect = check_inspect_on_path()
    if not has_launch:
        issues.append("gst-launch-1.0 not found")
    if not has_inspect:
        issues.append("gst-inspect-1.0 not found")

    if not has_launch and not has_inspect:
        print("\n  GStreamer does not appear to be installed.")
        print(get_install_hint())
        sys.exit(1)

    print("\n2. GStreamer version")
    check_gstreamer_version()

    print("\n3. Video source")
    if not check_element("autovideosrc"):
        issues.append("autovideosrc not available (need gst-plugins-good)")

    print("\n4. Camera access")
    if not check_camera(0):
        issues.append("Camera 0 not accessible")

    print("\n5. Video encoders")
    available_codecs: list[str] = []
    for name, (encoder, _, package) in CODECS.items():
        if check_element(encoder, f"{name} ({encoder}, {package})"):
            available_codecs.append(name)
        else:
            issues.append(f"Encoder {encoder} not available ({package})")

    print("\n6. RTP payloaders")
    checked_payloaders: set[str] = set()
    for name, (_, payloader, package) in CODECS.items():
        if payloader in checked_payloaders:
            continue
        checked_payloaders.add(payloader)
        if not check_element(payloader, f"{payloader} ({package})"):
            issues.append(f"Payloader {payloader} not available ({package})")

    print("\n7. Core pipeline elements")
    for element in ["videoconvert", "videoscale", "udpsink"]:
        if not check_element(element):
            issues.append(f"{element} not available")

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)

    if available_codecs:
        print(f"\n  Available codecs: {', '.join(available_codecs)}")
    else:
        print(f"\n  [{WARN}] No codecs available")

    if issues:
        print(f"\n  Issues found ({len(issues)}):")
        for issue in issues:
            print(f"    - {issue}")
        print(f"\n  Install missing packages:")
        print(get_install_hint())
    else:
        print(f"\n  [{PASS}] Everything looks good!")


if __name__ == "__main__":
    main()
