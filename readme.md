# GStreamWebcam
A Python 3 script that invokes [gstreamer-1.0](https://gstreamer.freedesktop.org/) to read from a webcam, encode the video stream, and send it to a destination host over RTP/UDP. Supports H264, H265, VP8, and AV1. Works on macOS, Linux, and Windows.

It can also generate a valid SDP file (`session.sdp`) that receivers like [VLC](https://www.videolan.org/vlc/index.html) can open directly.

If something isn't working, run `gstreamcam_doctor.py` to check your [installation](#installation).

## Usage

```
$ python gstreamcam.py -h
usage: gstreamcam.py [-h] [--sdp] [--debug] [--port PORT]
                     [--codec {h264,openh264,h265,vp8,av1}]
                     [--format FORMAT] [--camera CAMERA]
                     [--resolution RESOLUTION] [--no-convert]
                     [--list-codecs] [--timeout TIMEOUT]
                     [hostname]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `hostname` | | required | Destination hostname or IP address |
| `--sdp` | | off | Generate a `session.sdp` file for the stream |
| `--debug` | | off | Print the GStreamer command being executed |
| `--port` | `-p` | 5000 | UDP destination port |
| `--codec` | `-c` | openh264 | Video codec (see [codecs](#codecs)) |
| `--camera` | | 0 | Camera device index |
| `--format` | `-f` | I420 | Pixel format (see [pixel formats](#pixel-formats)) |
| `--resolution` | `-r` | native | Request resolution from camera as WIDTHxHEIGHT (e.g., `1280x720`) |
| `--no-convert` | | off | Fail instead of adding converters; shows native camera capabilities |
| `--list-codecs` | | | List available codecs and exit |
| `--timeout` | | 60 | Timeout in seconds for codec/camera probes |

When `--resolution` is omitted the camera negotiates its native resolution.

### Smart pipeline

Before building the pipeline, the script queries the camera's native capabilities via `gst-device-monitor-1.0`. It then builds the **cleanest possible pipeline**:

1. **Direct** — camera outputs exactly the requested format and resolution. No extra elements.
2. **videoconvert** — camera supports the resolution but not the pixel format. Adds format conversion.
3. **videoscale** — camera supports the format but not the resolution. Adds scaling.
4. **Both** — neither matches. Adds both converters.

In all fallback cases the script warns you and suggests native settings you can use to avoid conversion:

```
WARNING: Camera supports 1280x720 but not in I420. Adding videoconvert.
INFO: Native formats at 1280x720: NV12, UYVY, YUY2. Try one of these with --format for zero-conversion.
```

Use `--no-convert` to enforce a direct pipeline — the script will fail with suggestions rather than silently adding converters.

## Examples

Stream from the default webcam to localhost

```
python gstreamcam.py 127.0.0.1
```

Stream using a camera-native format for zero-conversion

```
python gstreamcam.py 127.0.0.1 --format NV12
```

Stream from the second webcam to localhost

```
python gstreamcam.py --camera 1 127.0.0.1
```

Stream at 720p using VP8

```
python gstreamcam.py 127.0.0.1 -c vp8 -r 1280x720
```

Enforce a direct pipeline (fail if converters would be needed)

```
python gstreamcam.py 127.0.0.1 --format NV12 -r 1280x720 --no-convert
```

Stream and create an SDP file

```
python gstreamcam.py 127.0.0.1 --sdp
```

Check which codecs are installed

```
python gstreamcam.py --list-codecs
```

## Playing streams

### H264 (x264 or OpenH264)

```
gst-launch-1.0 udpsrc port=5000 \
  caps="application/x-rtp, clock-rate=(int)90000, payload=(int)96" \
  ! rtph264depay ! decodebin ! videoconvert ! autovideosink
```

### H265

```
gst-launch-1.0 udpsrc port=5000 \
  caps="application/x-rtp, clock-rate=(int)90000, payload=(int)96" \
  ! rtph265depay ! decodebin ! videoconvert ! autovideosink
```

### VP8

```
gst-launch-1.0 udpsrc port=5000 \
  caps="application/x-rtp, clock-rate=(int)90000, payload=(int)96" \
  ! rtpvp8depay ! vp8dec ! videoconvert ! autovideosink
```

### AV1

```
gst-launch-1.0 udpsrc port=5000 \
  caps="application/x-rtp, clock-rate=(int)90000, payload=(int)96" \
  ! rtpav1depay ! av1dec ! videoconvert ! autovideosink
```

## Codecs

| Codec | Encoder | Payloader | GStreamer Package |
|-------|---------|-----------|-------------------|
| `h264` | x264enc | rtph264pay | gst-plugins-ugly |
| `openh264` | openh264enc | rtph264pay | gst-plugins-bad |
| `h265` | x265enc | rtph265pay | gst-plugins-bad |
| `vp8` | vp8enc | rtpvp8pay | gst-plugins-good |
| `av1` | av1enc | rtpav1pay | gst-plugins-bad |

## Pixel Formats

`--format` accepts any GStreamer pixel format string. Common choices:

| Format | Chroma | Notes |
|--------|--------|-------|
| `I420` | 4:2:0 | Default. Accepted by all encoders. Rarely native to cameras. |
| `NV12` | 4:2:0 | Common camera-native format. Works with h264, openh264. |
| `UYVY` | 4:2:2 | Common camera-native format. Needs videoconvert for most encoders. |
| `YV12` | 4:2:0 | Works with h264, av1. |
| `Y42B` | 4:2:2 | Higher chroma quality. Works with h264, h265, av1. |
| `Y444` | 4:4:4 | Full chroma. Works with h264, h265, av1. |

The script queries your camera and tells you exactly which formats it supports. Use a camera-native format (like `NV12`) to skip `videoconvert` for the fastest pipeline.

Not all codecs may be available on your system. Run `python gstreamcam.py --list-codecs` to see which ones are installed. If a codec is missing, install the corresponding GStreamer package.

## Installation

You need Python 3.10+ and GStreamer 1.0 with the plugins for your chosen codec.

### macOS

```
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly
```

### Linux (Debian / Ubuntu)

```
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

### Linux (Fedora)

```
sudo dnf install gstreamer1-tools gstreamer1-plugins-base \
  gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugins-ugly-free
```

### Windows

Download and run the official installer from [gstreamer.freedesktop.org](https://gstreamer.freedesktop.org/download/). Make sure `gst-launch-1.0` is on your PATH after installation.

## Troubleshooting

Run the diagnostic script to check your setup:

```
python gstreamcam_doctor.py
```

It checks whether GStreamer is installed, which plugins and codecs are available, and whether your camera is accessible. If something is missing it tells you what to install.

Common issues:

- **"codec not available"** — the encoder plugin isn't installed. Check the [codecs](#codecs) table for the required package and install it.
- **Camera not accessible** — on Linux, make sure your user is in the `video` group. On macOS, grant camera permissions when prompted.
- **"gst-launch-1.0 not found"** — GStreamer isn't installed or not on PATH. See [installation](#installation).
