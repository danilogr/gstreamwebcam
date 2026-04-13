#!/usr/bin/env python3
"""Stream webcam video over RTP/UDP using GStreamer."""

import argparse
import logging
import random
import re
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

random.seed(None)

CODECS: dict[str, tuple[bytes, str, str, str]] = {
    "h264": (b"GstRtpH264Pay", "x264enc", "rtph264pay", "gst-plugins-ugly"),
    "openh264": (b"GstRtpH264Pay", "openh264enc", "rtph264pay", "gst-plugins-bad"),
    "h265": (b"GstRtpH265Pay", "x265enc", "rtph265pay", "gst-plugins-bad"),
    "vp8": (b"GstRtpVP8Pay", "vp8enc", "rtpvp8pay", "gst-plugins-good"),
    "av1": (b"GstRtpAV1Pay", "av1enc", "rtpav1pay", "gst-plugins-bad"),
}


def probe_codec(encoder: str, timeout: int = 60) -> bool | None:
    """Check if a GStreamer encoder element is available.

    Returns True if available, False if not found, None if the probe timed out.
    """
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", encoder],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return False


def query_encoder_formats(encoder: str, timeout: int = 60) -> set[str] | None:
    """Query which pixel formats an encoder's sink pad accepts.

    Returns a set of format strings, or None if the encoder has no format
    restriction (accepts anything videoconvert can produce).
    """
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", encoder],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    sink_match = re.search(
        r"SINK template.*?Capabilities:\s*\n(.*?)(?=\n\s*\n|\n  SRC template)",
        result.stdout,
        re.DOTALL,
    )
    if not sink_match:
        return None

    format_match = re.search(r"format[= :]+(\{[^}]+\}|\w+)", sink_match.group(1))
    if not format_match:
        return None

    text = format_match.group(1)
    if text.startswith("{"):
        return set(re.findall(r"\(string\)(\w+)", text))
    return {text}


def list_available_codecs(timeout: int = 60) -> dict[str, bool | None]:
    """Probe all codecs and return availability map."""
    return {name: probe_codec(info[1], timeout=timeout) for name, info in CODECS.items()}


def print_codec_table(timeout: int = 60) -> None:
    """Print a table of all codecs with availability status."""
    available = list_available_codecs(timeout=timeout)
    print(f"{'Codec':<12} {'Encoder':<15} {'Package':<20} {'Available'}")
    print("-" * 60)
    for name, (_, encoder, _, package) in CODECS.items():
        result = available[name]
        if result is None:
            status = "timeout"
        elif result:
            status = "yes"
        else:
            status = "no"
        print(f"{name:<12} {encoder:<15} {package:<20} {status}")
    if any(v is None for v in available.values()):
        print()
        print("Some probes timed out. Try again with --timeout <seconds>.")


@dataclass
class CameraInfo:
    """Capabilities of a single camera device."""

    name: str
    device_index: int
    caps: dict[tuple[int, int], set[str]] = field(default_factory=dict)

    @property
    def all_formats(self) -> set[str]:
        result: set[str] = set()
        for formats in self.caps.values():
            result |= formats
        return result

    @property
    def all_resolutions(self) -> set[tuple[int, int]]:
        return set(self.caps.keys())

    def formats_at(self, resolution: tuple[int, int]) -> set[str]:
        return self.caps.get(resolution, set())

    def resolutions_for(self, pixel_format: str) -> list[tuple[int, int]]:
        return sorted(
            [res for res, fmts in self.caps.items() if pixel_format in fmts],
            key=lambda r: r[0] * r[1],
            reverse=True,
        )


def _parse_int_values(text: str) -> list[int]:
    """Parse `{ (int)360, (int)480 }` or a bare `640`."""
    if text.startswith("{"):
        return [int(x) for x in re.findall(r"\(int\)(\d+)", text)]
    return [int(text)]


def _parse_string_values(text: str) -> list[str]:
    """Parse `{ (string)UYVY, (string)YUY2 }` or a bare `I420`."""
    if text.startswith("{"):
        return re.findall(r"\(string\)(\w+)", text)
    return [text]


def _parse_caps_line(line: str) -> list[tuple[tuple[int, int], str]]:
    """Parse one caps line into (resolution, format) pairs.

    Skips GLMemory caps since software encoders can't use them.
    """
    line = line.strip()
    if "memory:GLMemory" in line or not line.startswith("video/x-raw"):
        return []

    width_m = re.search(r"width=(\{[^}]+\}|\d+)", line)
    height_m = re.search(r"height=(\{[^}]+\}|\d+)", line)
    format_m = re.search(r"format=(\{[^}]+\}|\w+)", line)

    if not (width_m and height_m and format_m):
        return []

    widths = _parse_int_values(width_m.group(1))
    heights = _parse_int_values(height_m.group(1))
    formats = _parse_string_values(format_m.group(1))

    return [((w, h), f) for w in widths for h in heights for f in formats]


def query_camera_caps(device_index: int, timeout: int = 60) -> CameraInfo | None:
    """Query camera capabilities via gst-device-monitor-1.0."""
    try:
        result = subprocess.run(
            ["gst-device-monitor-1.0", "Video/Source"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Camera capability query timed out after %ds. "
            "Try increasing --timeout.", timeout,
        )
        return None
    except FileNotFoundError:
        logger.debug("gst-device-monitor-1.0 not found, skipping camera query")
        return None

    if result.returncode != 0:
        return None

    devices = re.split(r"^Device found:", result.stdout, flags=re.MULTILINE)
    for block in devices[1:]:
        index_match = re.search(r"device-index=(\d+)", block)
        if not index_match or int(index_match.group(1)) != device_index:
            continue

        name_match = re.search(r"name\s*:\s*(.+)", block)
        name = name_match.group(1).strip() if name_match else "Unknown"

        camera = CameraInfo(name=name, device_index=device_index)

        caps_match = re.search(r"caps\s*:\s*(.+?)(?=\tproperties:)", block, re.DOTALL)
        if caps_match:
            for line in caps_match.group(1).splitlines():
                for resolution, fmt in _parse_caps_line(line):
                    if resolution not in camera.caps:
                        camera.caps[resolution] = set()
                    camera.caps[resolution].add(fmt)

        return camera

    return None


def parse_resolution(value: str) -> tuple[int, int]:
    """Parse a WIDTHxHEIGHT string into (width, height)."""
    match = re.match(r"^(\d+)x(\d+)$", value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid resolution '{value}', expected WIDTHxHEIGHT (e.g., 1280x720)"
        )
    width, height = int(match.group(1)), int(match.group(2))
    if width == 0 or height == 0:
        raise argparse.ArgumentTypeError(
            f"invalid resolution '{value}', width and height must be positive"
        )
    return width, height


def create_sdp_file(hostname: str, streams: list[dict[str, str]], path: Path) -> None:
    """Write an SDP file from parsed RTP stream parameters."""
    params_to_ignore = {
        "encoding-name", "timestamp-offset", "payload", "clock-rate", "media", "port",
    }
    sdp = [
        "v=0",
        f"o=- {random.randrange(4294967295)} 2 IN IP4 {hostname}",
        "t=0 0",
        "s=GST2SDP",
    ]

    for stream_number, stream in enumerate(streams, start=1):
        sdp.append(f"m={stream['media']} {stream['port']} RTP/AVP {stream['payload']}")
        sdp.append(f"c=IN IP4 {hostname}")
        sdp.append(
            f"a=rtpmap:{stream['payload']} {stream['encoding-name']}/{stream['clock-rate']}"
        )

        fmtp_parts = [f"a=fmtp:{stream['payload']}"]
        for param, value in stream.items():
            if param.startswith("a-"):
                sdp.append(f"{param.replace('a-', 'a=')}:{value}")
            elif param not in params_to_ignore:
                fmtp_parts.append(f" {param}={value};")

        sdp.append("".join(fmtp_parts))
        sdp.append(f"a=control:track{stream_number}")

    path.write_text("\r\n".join(sdp))
    logger.info("SDP file written to %s", path)


def _platform_video_source() -> str:
    """Return the platform-specific GStreamer video source element."""
    if sys.platform == "darwin":
        return "avfvideosrc"
    if sys.platform == "win32":
        return "mfvideosrc"
    return "v4l2src"


@dataclass(slots=True)
class PipelineConfig:
    """All settings needed to build a GStreamer pipeline."""

    hostname: str
    port: int
    codec: str
    camera: int
    resolution: tuple[int, int] | None
    pixel_format: str
    needs_convert: bool = False
    needs_scale: bool = False


def build_pipeline_args(config: PipelineConfig) -> list[str]:
    """Build the gst-launch-1.0 argument list.

    The pipeline is assembled from the minimum set of elements needed:
    - Direct: src ! caps ! encoder (camera outputs exactly what we need)
    - Convert: src ! [res caps] ! videoconvert ! format caps ! encoder
    - Scale: src ! format caps ! videoscale ! full caps ! encoder
    - Both: src ! videoconvert ! videoscale ! full caps ! encoder
    """
    _, encoder, payloader, _ = CODECS[config.codec]

    args = ["gst-launch-1.0", "-e", "-v"]

    # autovideosrc doesn't support device-index; use platform source for non-default cameras
    if config.camera == 0:
        args.extend(["autovideosrc", "!"])
    else:
        source = _platform_video_source()
        args.extend([source, f"device-index={config.camera}", "!"])

    if config.needs_convert and config.needs_scale:
        args.extend([
            "videoconvert", "!", "videoscale", "!",
            f"video/x-raw,format={config.pixel_format},"
            f"width={config.resolution[0]},height={config.resolution[1]}", "!",
        ])
    elif config.needs_convert:
        if config.resolution:
            args.extend([
                f"video/x-raw,width={config.resolution[0]},height={config.resolution[1]}", "!",
            ])
        args.extend(["videoconvert", "!", f"video/x-raw,format={config.pixel_format}", "!"])
    elif config.needs_scale:
        args.extend([
            f"video/x-raw,format={config.pixel_format}", "!",
            "videoscale", "!",
            f"video/x-raw,format={config.pixel_format},"
            f"width={config.resolution[0]},height={config.resolution[1]}", "!",
        ])
    else:
        caps = f"video/x-raw,format={config.pixel_format}"
        if config.resolution:
            caps += f",width={config.resolution[0]},height={config.resolution[1]}"
        args.extend([caps, "!"])

    args.extend([
        encoder, "!", payloader, "!",
        "udpsink", f"host={config.hostname}", f"port={config.port}",
    ])
    return args


def parse_rtp_caps(line: bytes, gst_type_name: bytes) -> dict[str, str] | None:
    """Parse RTP caps from a gstreamer verbose output line."""
    pattern = re.compile(
        rb"/GstPipeline:pipeline\d+/%b:\w+\d+.GstPad:src: caps = (.+)" % gst_type_name
    )
    match = pattern.search(line)
    if not match:
        return None

    parameters = re.findall(
        rb'(([\w-]+)=(?:\(\w+\))?(?:(\w+)|(?:"([^"]+)")))',
        match.group(1),
    )
    if not parameters:
        return None

    param_map: dict[str, str] = {}
    for _, param, value, value2 in parameters:
        decoded_value = value.decode("ascii") if value else value2.decode("ascii")
        param_map[param.decode("ascii")] = decoded_value

    return param_map


def _format_resolution(res: tuple[int, int]) -> str:
    return f"{res[0]}x{res[1]}"


def _suggest_formats(
    camera_formats: set[str],
    encoder_formats: set[str] | None,
) -> list[str]:
    """Return formats the camera and encoder both support, sorted."""
    if encoder_formats is None:
        return sorted(camera_formats)
    return sorted(camera_formats & encoder_formats)


def _analyze_pipeline(
    camera: CameraInfo | None,
    pixel_format: str,
    resolution: tuple[int, int] | None,
    encoder_formats: set[str] | None,
) -> tuple[bool, bool]:
    """Decide what converters are needed and log warnings with suggestions.

    Returns (needs_convert, needs_scale). Does not terminate the process —
    the caller decides how to act on the result.

    encoder_formats is the set of pixel formats the encoder accepts, or None
    if it has no restriction.
    """
    encoder_accepts = encoder_formats is None or pixel_format in encoder_formats

    if camera is None:
        if not encoder_accepts:
            logger.warning(
                "Encoder doesn't accept %s. Adding videoconvert.", pixel_format,
            )
            return True, False
        logger.warning(
            "Could not query camera capabilities. Adding videoconvert as a safety net."
        )
        return True, False

    logger.info("Camera: %s", camera.name)
    needs_convert = False
    needs_scale = False

    if resolution:
        has_resolution = resolution in camera.all_resolutions
        camera_has_format = pixel_format in camera.formats_at(resolution)
        direct_ok = camera_has_format and encoder_accepts

        if direct_ok:
            logger.info(
                "Camera natively supports %s at %s — direct pipeline.",
                pixel_format, _format_resolution(resolution),
            )
        elif has_resolution:
            needs_convert = True
            if camera_has_format and not encoder_accepts:
                logger.warning(
                    "Camera supports %s at %s but encoder doesn't accept it. "
                    "Adding videoconvert.",
                    pixel_format, _format_resolution(resolution),
                )
            else:
                logger.warning(
                    "Camera supports %s but not in %s. Adding videoconvert.",
                    _format_resolution(resolution), pixel_format,
                )
            direct = _suggest_formats(camera.formats_at(resolution), encoder_formats)
            if direct:
                logger.info(
                    "Formats supported by both camera and encoder at %s: %s. "
                    "Try one with --format for zero-conversion.",
                    _format_resolution(resolution), ", ".join(direct),
                )
            else:
                logger.info(
                    "No format is shared by camera and encoder at %s — "
                    "videoconvert is required.",
                    _format_resolution(resolution),
                )
        elif camera_has_format or pixel_format in camera.all_formats:
            needs_scale = True
            if not encoder_accepts:
                needs_convert = True
                logger.warning(
                    "Camera supports %s but encoder doesn't, and %s isn't a native "
                    "resolution. Adding videoconvert + videoscale.",
                    pixel_format, _format_resolution(resolution),
                )
            else:
                native_res = camera.resolutions_for(pixel_format)
                logger.warning(
                    "Camera supports %s but not at %s. Adding videoscale.",
                    pixel_format, _format_resolution(resolution),
                )
                logger.info(
                    "Native resolutions for %s: %s. "
                    "Try one of these with -r for zero-scaling.",
                    pixel_format,
                    ", ".join(_format_resolution(r) for r in native_res),
                )
        else:
            needs_convert = True
            needs_scale = True
            logger.warning(
                "Camera doesn't support %s or %s natively. "
                "Adding videoconvert + videoscale.",
                pixel_format, _format_resolution(resolution),
            )
            direct = _suggest_formats(camera.all_formats, encoder_formats)
            if direct:
                logger.info(
                    "Formats supported by both camera and encoder: %s",
                    ", ".join(direct),
                )
            logger.info(
                "Native resolutions: %s",
                ", ".join(
                    _format_resolution(r)
                    for r in sorted(camera.all_resolutions, key=lambda r: -r[0] * r[1])
                ),
            )
    else:
        camera_has_format = pixel_format in camera.all_formats
        direct_ok = camera_has_format and encoder_accepts

        if direct_ok:
            logger.info(
                "Camera natively supports %s — direct pipeline.", pixel_format,
            )
        elif camera_has_format and not encoder_accepts:
            needs_convert = True
            logger.warning(
                "Camera supports %s but encoder doesn't accept it. "
                "Adding videoconvert.", pixel_format,
            )
            direct = _suggest_formats(camera.all_formats, encoder_formats)
            if direct:
                logger.info(
                    "Formats supported by both camera and encoder: %s. "
                    "Try one with --format for zero-conversion.",
                    ", ".join(direct),
                )
        else:
            needs_convert = True
            logger.warning(
                "Camera doesn't natively output %s. Adding videoconvert.",
                pixel_format,
            )
            direct = _suggest_formats(camera.all_formats, encoder_formats)
            if direct:
                logger.info(
                    "Formats supported by both camera and encoder: %s. "
                    "Try one with --format for zero-conversion.",
                    ", ".join(direct),
                )
            elif camera.all_formats:
                logger.info(
                    "Camera native formats: %s",
                    ", ".join(sorted(camera.all_formats)),
                )

    return needs_convert, needs_scale


def stream(args: argparse.Namespace) -> None:
    """Run the streaming pipeline."""
    hostname = socket.gethostbyname(args.hostname)
    codec = args.codec
    port = args.port

    probe_result = probe_codec(CODECS[codec][1], timeout=args.timeout)
    if probe_result is None:
        logger.error(
            "Codec probe for '%s' (encoder: %s) timed out after %ds.",
            codec,
            CODECS[codec][1],
            args.timeout,
        )
        logger.info(
            "This often happens on first run while GStreamer scans its plugin registry. "
            "Try again, or increase the timeout with --timeout <seconds>."
        )
        sys.exit(1)
    if not probe_result:
        available = list_available_codecs(timeout=args.timeout)
        available_names = [name for name, is_available in available.items() if is_available]
        logger.error(
            "Codec '%s' (encoder: %s) is not available. Install %s.",
            codec,
            CODECS[codec][1],
            CODECS[codec][3],
        )
        if available_names:
            logger.info("Available codecs: %s", ", ".join(available_names))
        else:
            logger.error("No codecs available. Check your GStreamer installation.")
        sys.exit(1)

    camera = query_camera_caps(args.camera, timeout=args.timeout)
    encoder_formats = query_encoder_formats(CODECS[codec][1], timeout=args.timeout)

    if args.no_convert and camera is None:
        logger.warning(
            "Could not query camera capabilities. "
            "Trying direct pipeline as requested by --no-convert."
        )
        needs_convert, needs_scale = False, False
    else:
        needs_convert, needs_scale = _analyze_pipeline(
            camera, args.format, args.resolution, encoder_formats,
        )
        if args.no_convert and (needs_convert or needs_scale):
            converters = []
            if needs_convert:
                converters.append("videoconvert")
            if needs_scale:
                converters.append("videoscale")
            logger.error(
                "--no-convert specified but camera can't produce the requested output "
                "without %s.", " + ".join(converters),
            )
            sys.exit(1)

    # If videoconvert is needed, pick the format for the encoder side of the
    # pipeline. Use the user's format if the encoder accepts it, otherwise
    # fall back to I420 or the first encoder-accepted format.
    pipeline_format = args.format
    if needs_convert and encoder_formats and args.format not in encoder_formats:
        pipeline_format = "I420" if "I420" in encoder_formats else sorted(encoder_formats)[0]
        logger.info(
            "Converting to %s for encoder (requested %s).", pipeline_format, args.format,
        )

    config = PipelineConfig(
        hostname=hostname,
        port=port,
        codec=codec,
        camera=args.camera,
        resolution=args.resolution,
        pixel_format=pipeline_format,
        needs_convert=needs_convert,
        needs_scale=needs_scale,
    )
    pipeline_args = build_pipeline_args(config)

    if args.debug:
        logger.info("GStreamer command: %s", " ".join(pipeline_args))

    logger.info(
        "Streaming to %s:%d using %s (device %d)", hostname, port, codec, args.camera
    )

    stderr_target = None if args.debug else subprocess.PIPE
    process = subprocess.Popen(pipeline_args, stdout=subprocess.PIPE, stderr=stderr_target)

    # Drain stderr in a background thread to prevent pipe buffer deadlock
    stderr_chunks: list[bytes] = []
    if process.stderr is not None:
        def _drain_stderr() -> None:
            stderr_chunks.append(process.stderr.read())

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

    def handle_sigint(signum: int, frame: object) -> None:
        process.send_signal(signal.SIGINT)
        logger.info("Shutting down GStreamer process")

    signal.signal(signal.SIGINT, handle_sigint)

    gst_type_name = CODECS[codec][0]
    caps_parsed = False

    try:
        for line in process.stdout:
            if caps_parsed:
                continue

            param_map = parse_rtp_caps(line, gst_type_name)
            if param_map is None:
                continue

            caps_parsed = True
            param_map["port"] = str(port)

            for param, value in param_map.items():
                logger.info("%s = %s", param, value)

            if args.sdp:
                create_sdp_file(hostname, [param_map], Path("session.sdp"))
    finally:
        returncode = process.wait()
        if returncode not in (0, -signal.SIGINT):
            logger.error("GStreamer exited with code %d", returncode)
            if stderr_chunks:
                stderr_output = stderr_chunks[0].decode(errors="replace").strip()
                if stderr_output:
                    logger.error("GStreamer stderr:\n%s", stderr_output)
            if not args.debug:
                logger.info("Run with --debug for full GStreamer output.")
            sys.exit(returncode)


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(
        description="Stream webcam video over RTP/UDP using GStreamer",
    )
    parser.add_argument(
        "hostname",
        nargs="?",
        default=None,
        help="destination hostname or IP address",
    )
    parser.add_argument(
        "--sdp",
        action="store_true",
        help="generate a session.sdp file for the stream",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print the GStreamer command being executed",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5000,
        help="UDP destination port (default: 5000)",
    )
    parser.add_argument(
        "--codec", "-c",
        choices=list(CODECS),
        default="openh264",
        help="video codec (default: openh264)",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="camera device index (default: 0)",
    )
    parser.add_argument(
        "--format", "-f",
        default="I420",
        help="pixel format (default: I420; common: I420, NV12, YV12, Y42B, Y444)",
    )
    parser.add_argument(
        "--resolution", "-r",
        type=parse_resolution,
        default=None,
        help="force resolution as WIDTHxHEIGHT (e.g., 1280x720); omit for camera native",
    )
    parser.add_argument(
        "--no-convert",
        action="store_true",
        help="fail instead of adding converters; shows what the camera supports natively",
    )
    parser.add_argument(
        "--list-codecs",
        action="store_true",
        help="list available codecs and exit",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="timeout in seconds for codec probes (default: 60)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    if args.list_codecs:
        print_codec_table(timeout=args.timeout)
        sys.exit(0)

    if args.hostname is None:
        parser.error("the following arguments are required: hostname")

    stream(args)


if __name__ == "__main__":
    main()
