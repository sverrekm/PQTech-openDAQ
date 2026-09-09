#!/usr/bin/env python3
"""Check OPC-UA channel list as a client would see it."""
import opendaq as daq

for url in ["daq.opcua://127.0.0.1", "daq.opcua://localhost"]:
    try:
        instance = daq.Instance()
        dev = instance.add_device(url)
        channels = list(dev.channels)
        print("{}: {} channels".format(url, len(channels)))
        for i, ch in enumerate(channels):
            print("  Ch{}: {}".format(i, ch.name))
        break
    except Exception as e:
        print("{}: {}".format(url, e))
