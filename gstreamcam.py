from subprocess import Popen, PIPE
import re
import argparse
import signal
import random
import socket
from collections import defaultdict
import sys

random.seed(None)

def createsdp(hostname, streams):
    params2ignore = set(['encoding-name', 'timestamp-offset', 'payload', 'clock-rate', 'media', 'port'])
    sdp = ['v=0']
    sdp.append('o=- %d %d IN IP4 %s' % (random.randrange(4294967295), 2, hostname))
    sdp.append('t=0 0')
    sdp.append('s=GST2SDP')

    streamnumber = 1

    # add individual streams to SDP
    for stream in streams:
        sdp.append("m=%s %s RTP/AVP %s" % (stream['media'], stream['port'], stream['payload']))
        sdp.append('c=IN IP4 %s' % hostname)
        sdp.append("a=rtpmap:%s %s/%s" % (stream['payload'], stream['encoding-name'], stream['clock-rate']))
        fmtp = ["a=fmtp:%s" % stream['payload']]
        for param,value in stream.items():
            # is parameter an action?
            if param[0] == 'a' and param[1] == '-':
                aparam = "%s:%s" % (param.replace('a-', 'a='), value)
                sdp.append(aparam)
            else:
                if param not in params2ignore:
                    fmtp.append(" %s=%s;" % (param, value))
        fmtp = ''.join(fmtp)
        sdp.append(fmtp)
        sdp.append("a=control:track%d" % streamnumber)
        streamnumber += 1

    # save sdp
    with open('session.sdp','w') as f:
        f.write('\r\n'.join(sdp))


def main(arguments):
    
    gstreamer = 'gst-launch-1.0.exe' if 'win' in sys.platform else 'gst-launch-1.0'
    hostname = arguments.hostname
    encoders = {'h264' : (b'GstRtpH264Pay' , 'x264enc', 'rtph264pay'),
                'vp8' : (b'GstRtpVP8Pay', 'vp8enc', 'rtpvp8pay'),
                'openh264' : (b'GstRtpH264Pay', 'openh264enc', 'rtph264pay')}
    rtppay = encoders[arguments.codec][0]
    port = arguments.port
    arglist = [gstreamer, "-v", "ksvideosrc", "device_index=%d" % arguments.camera, "!", "videoconvert", "!", "videoscale", "!", "video/x-raw,width=800,height=600", "!",
                    encoders[arguments.codec][1],  "!", encoders[arguments.codec][2], "!", "udpsink", "host=%s" % hostname, "port=%d" % port]
    if arguments.debug:
        print("Calling gstreamer:\n"," ".join(arglist))
    process = Popen(arglist, stdout=PIPE)

    def signal_handler(signal, frame):
        process.kill()
        print('Terminating child process')

    signal.signal(signal.SIGINT, signal_handler)
    patternGenerated = False
    try:
        p = re.compile(rb'/GstPipeline:pipeline\d+/%b:\w+\d+.GstPad:src: caps = (.+)' % rtppay)
        for line in process.stdout:
            pattern = p.search(line)
            if pattern and not patternGenerated:
                parameters = re.findall(rb'(([\w-]+)=(?:\(\w+\))?(?:(\w+)|(?:"([^"]+)")))', pattern.groups()[0])
                #print(parameters)
                parammap = defaultdict(str)
                for (_, param, value, value2) in parameters:
                    parammap[param.decode('ascii')] = value.decode('ascii') if value else value2.decode('ascii')
                    parammap['port'] = port

                if len(parammap) > 0:
                    patternGenerated = True
                    if arguments.sdp:
                        createsdp(hostname, [parammap])
                    for param,value in parammap.items():
                        print("%s = %s" % (param, value))
    finally:
        process.wait()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("hostname", help="hostname or IP address of the destination")
    parser.add_argument("--sdp", help="generates SDP file for the stream (defaults to false)", action="store_true")
    parser.add_argument("--debug", help="shows command line in use to call gstreamer", action="store_true")
    parser.add_argument("--port", "-p", help="port (defaults to 5000)", type=int, default=5000)
    parser.add_argument("--codec", help="chooses encoder (defaults to openh264)", choices=['vp8', 'h264', 'openh264'], default='openh264')
    parser.add_argument("--camera", help="Device id (defaults to 0)", type=int, default=0)

    args = parser.parse_args()

    args.hostname = socket.gethostbyname(args.hostname)
    print("Using hostname %s using device %d" % (args.hostname, args.camera))

    main(args)
