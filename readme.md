# GStreamWebcam
This tool is a python 3.0 script that invokes [gstreamer-1.0](https://gstreamer.freedesktop.org/), reads from the webcam (or other valid video source), encodes the video stream in either `H264` or `VP8`, and sends it to the desired host through RTP/UDP. 

Playing the stream is quite simple since it also creates a valid SDP file (`session.sdp`) that can be used with [VLC](https://www.videolan.org/vlc/index.html)

## Requirements

| plugin name | package |
|:-----------:|:-------:|
|autovideosrc | gst-plugins-good|
|videoconvert | gst-plugins-base|
|vp8enc       | gst-plugins-good|
|rtpvp8pay    | gst-plugins-good|
|x264enc      | gst-plugins-ugly|
|rtph264pay   | gst-plugins-good|
|openh264enc  | gst-plugins-bad|

Plugin `x264enc` is only required if encoding H264 using libx264.

## Usage

```
$ python gstreamcam.py -h
usage: gstreamcam.py [-h] [--sdp] [--debug] [--port PORT]
                     [--codec {vp8,h264,openh264}] [--camera CAMERA]
                     hostname

positional arguments:
  hostname              hostname or IP address of the destination

optional arguments:
  -h, --help            show this help message and exit
  --sdp                 generates SDP file for the stream (defaults to false)
  --debug               shows command line in use to call gstreamer
  --port PORT, -p PORT  port (defaults to 5000)
  --codec {vp8,h264,openh264}
                        chooses encoder (defaults to openh264)
  --camera CAMERA       Device id (defaults to 0)

```

## Examples

Streaming from the default webcam to localhost

```python gstreamcam.py 127.0.0.1``` 


Streaming from the second webcam to localhost

```python gstreamcam.py --camera 1 127.0.0.1``` 


Streaming and creating an SDP file

```python gstreamcam.py 127.0.0.1 --sdp```


## Playing streams using gstreamer

### H264 (with either x264 or openH264)

```
gst-launch-1.0 udpsrc port=5000 caps="application/x-rtp, clock-rate=(int)90000, packetization-mode=(string)1, payload=(int)96, a-framerate=(string)30" ! rtph264depay ! decodebin ! videoconvert ! autovideosink
```

Note that the command line above requires other plugins.


### VP8

```
gst-launch-1.0 udpsrc port=9001 caps = "application/x-rtp, clock-rate=(int)90000, payload=(int)96, a-framerate=(string)30" ! rtpvp8depay ! vp8dec ! videoconvert ! autovideosink
```







