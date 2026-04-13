# Architecture

## Pipeline Decision Flow

```
                    ┌──────────────┐
                    │  User Input  │
                    │  --format?   │
                    │  --resolution│
                    │  --codec     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ probe_codec  │──── not found ──► exit with alternatives
                    └──────┬───────┘
                           │ ok
               ┌───────────┴───────────┐
               │                       │
    ┌──────────▼──────────┐ ┌──────────▼──────────┐
    │ query_camera_caps   │ │query_encoder_formats │
    │ gst-device-monitor  │ │ gst-inspect-1.0     │
    └──────────┬──────────┘ └──────────┬──────────┘
               │ CameraInfo | None     │ set[str] | None
               └───────────┬───────────┘
                           │
                    ┌──────▼───────┐
                    │  _analyze    │
                    │  _pipeline   │
                    └──────┬───────┘
                           │ (needs_convert, needs_scale)
                    ┌──────▼───────┐
               ┌────┤ --no-convert?├──── yes + needs work ──► exit with suggestions
               │    └──────────────┘
               │ no / not needed
               │
        ┌──────▼──────────────────┐
        │ resolve pipeline_format │  (pick encoder-compatible format
        │                         │   when videoconvert is needed)
        └──────┬──────────────────┘
               │
        ┌──────▼──────────────┐
        │ build_pipeline_args │
        │   (PipelineConfig)  │
        └──────┬──────────────┘
               │
      ┌────────┴────────┬──────────────┬───────────────┐
      ▼                 ▼              ▼               ▼
   Direct          Convert only   Scale only      Both
   src [! caps]    src ! [res]    src [! fmt]     src !
   ! encoder       ! videoconvert ! videoscale    videoconvert !
                   [! fmt_caps]   ! full_caps     videoscale !
                   ! encoder      ! encoder       full_caps !
                                                  encoder
```

When `--format` is omitted (the default), caps filters are only added where
needed (resolution, or after videoconvert). GStreamer negotiates the pixel
format directly between camera and encoder. Brackets `[ ]` indicate optional
elements that depend on whether format/resolution was specified.

## Analysis Logic

`_analyze_pipeline` checks compatibility between camera, user request, and
encoder in this order:

```
Was --format specified?
├─ No  → Do camera and encoder share any format?
│        ├─ Yes → direct (no caps filter needed)
│        └─ No  → needs_convert (pick I420 for encoder)
│
└─ Yes → Does camera output this format?
         ├─ Yes → Does encoder accept it?
         │        ├─ Yes → direct
         │        └─ No  → needs_convert (pick encoder-compatible format)
         └─ No  → needs_convert
                   (suggest intersection of camera + encoder formats)

For each path above, also check:
  Was --resolution specified?
  ├─ Camera supports it? → request via caps
  └─ Camera doesn't?    → needs_scale
```

## Classes

```
┌─────────────────────────────────────┐
│           CameraInfo                │
├─────────────────────────────────────┤
│ name: str                           │
│ device_index: int                   │
│ caps: dict[(w,h), set[format]]      │
├─────────────────────────────────────┤
│ all_formats: set[str]               │
│ all_resolutions: set[(int,int)]     │
│ formats_at(resolution) → set[str]   │
│ resolutions_for(format) → list[res] │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│         PipelineConfig              │
├─────────────────────────────────────┤
│ hostname: str                       │
│ port: int                           │
│ codec: str                          │
│ camera: int                         │
│ resolution: (int,int) | None        │
│ pixel_format: str | None            │
│ needs_convert: bool                 │
│ needs_scale: bool                   │
└─────────────────────────────────────┘
```

## Key Functions

| Function | Input | Output | Side effects |
|----------|-------|--------|-------------|
| `probe_codec` | encoder name | `True`/`False`/`None` | None |
| `query_encoder_formats` | encoder name | `set[str]` or `None` | None |
| `query_camera_caps` | device index | `CameraInfo` or `None` | Logs warnings on timeout |
| `query_all_cameras` | timeout | `list[CameraInfo]` | Logs warnings on timeout |
| `_analyze_pipeline` | camera, format?, resolution, encoder_formats | `(needs_convert, needs_scale)` | Logs warnings + suggestions |
| `build_pipeline_args` | `PipelineConfig` | `list[str]` (argv) | None |
| `stream` | `argparse.Namespace` | None | Launches GStreamer, may `sys.exit` |

## Pipeline Examples

```
# No format, no resolution — pure negotiation
autovideosrc ! x264enc ! rtph264pay ! udpsink

# No format, with resolution — only resolution constrained
autovideosrc ! video/x-raw,width=1280,height=720 ! x264enc ! ...

# Camera and encoder share no format — videoconvert picks I420
autovideosrc ! videoconvert ! video/x-raw,format=I420 ! av1enc ! ...

# Explicit NV12, camera supports it, encoder accepts it — direct
autovideosrc ! video/x-raw,format=NV12,width=1280,height=720 ! x264enc ! ...

# Explicit NV12, encoder rejects it — convert to I420
autovideosrc ! video/x-raw,width=1920,height=1080 ! videoconvert ! video/x-raw,format=I420 ! x265enc ! ...
```
