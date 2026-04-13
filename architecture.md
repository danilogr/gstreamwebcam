# Architecture

## Pipeline Decision Flow

```
                    ┌──────────────┐
                    │  User Input  │
                    │  --format    │
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
        ┌──────▼──────────────┐
        │ build_pipeline_args │
        │   (PipelineConfig)  │
        └──────┬──────────────┘
               │
      ┌────────┴────────┬──────────────┬───────────────┐
      ▼                 ▼              ▼               ▼
   Direct          Convert only   Scale only      Both
   src ! caps      src ! [res]    src ! fmt       src !
   ! encoder       ! videoconvert ! videoscale    videoconvert !
                   ! fmt_caps     ! full_caps     videoscale !
                   ! encoder      ! encoder       full_caps !
                                                  encoder
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
│ pixel_format: str                   │
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
| `_analyze_pipeline` | camera, format, resolution, encoder_formats | `(needs_convert, needs_scale)` | Logs warnings + suggestions |
| `build_pipeline_args` | `PipelineConfig` | `list[str]` (argv) | None |
| `stream` | `argparse.Namespace` | None | Launches GStreamer, may `sys.exit` |
