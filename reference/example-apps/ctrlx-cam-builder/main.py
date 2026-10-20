#!/usr/bin/env python3

# SPDX-FileCopyrightText: Bosch Rexroth AG
#
# SPDX-License-Identifier: MIT

import os
import signal
import threading
import time

import http.server
import web.request_handler
import web.unix_socket_server

import app.datalayer

# From Provider sample START
import sys
import ctrlxdatalayer
from ctrlxdatalayer.variant import Result, Variant
from app.my_provider_node import MyProviderNode
from app.ctrlx_datalayer_helper import get_provider

ROOT_PATH = "cam-builder/"
# From Provider sample END

httpServerPort = 12345
token = "eyJhbGciOiJFUzM4NCIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjAxNDg3NzQsImlhdCI6MTY2MDExOTk3NCwiaWQiOiIxMDAwIiwibmFtZSI6ImJvc2NocmV4cm90aCIsIm5vbmNlIjoiMGU0NTVhODMtMThlOC00YjY2LTllMWUtYTE0NWM2ZWIzZWQzIiwicGxjaGFuZGxlIjowLCJyZW1vdGVhdXRoIjoiIiwic2NvcGUiOlsicmV4cm90aC1kZXZpY2UuYWxsLnJ3eCJdfQ.VqCCRh2ga1Ujn5C_vBAf7dZHXNr6gY0Aqvrwu39_6L9d7fWBYXr-MmqdYxGB85fHBhs56MFrCacYjN5SbctqSyH1LTeXLKAdP4Etx8V7B2QB_5XZdVCLqIwYOAU8Gdzv"


__close_app = False


def handler(signum, frame):
    """handler"""
    global __close_app
    __close_app = True
    # print('Here you go signum: ', signum, __close_app, flush=True)


def run():
    """run"""

    client, connection_string = web.request_handler.data_layer.connect_client(
        ip="10.0.2.2", https_port=8443
    )

    if client is None:
        print("ERROR Could not connect", connection_string, flush=True)
        return

    new_thread = threading.Thread(target=thread_start)
    new_thread.start()

    new_thread.join()

def main():
    """main"""
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGABRT, handler)

    # From Provider sample START
    with ctrlxdatalayer.system.System("") as datalayer_system:
        datalayer_system.start(False)

        # ip="10.0.2.2", ssl_port=8443: ctrlX COREvirtual with port forwarding and default port mapping
        provider, connection_string = get_provider(datalayer_system,
                                                    ip="10.0.2.2",
                                                    ssl_port=8443)
        if provider is None:
            print("ERROR Connecting", connection_string, "failed.", flush=True)
            sys.exit(1)

        with (
                provider
        ):  # provider.close() is called automatically when leaving with... block

            provider_node_str = provide_string(provider,
                                                ROOT_PATH + "position-array",
                                                "types/datalayer/string")

            while provider.is_connected() and not __close_app:
                web.request_handler.data_layer = app.datalayer.DataLayer()
                web.request_handler.data_layer.start()

                run()
                time.sleep(10.0)

                web.request_handler.data_layer.stop()

            if provider.is_connected() == False:
                print("WARNING ctrlX Data Layer Provider is disconnected",
                    flush=True)

            provider_node_str.unregister_node()
            del provider_node_str

            print("Stopping ctrlX Data Layer provider:", end=" ", flush=True)
            result = provider.stop()
            print(result, flush=True)

        # Attention: Doesn't return if any provider or client instance is still running
        stop_ok = datalayer_system.stop(False)
        print("System Stop", stop_ok, flush=True)
        

# From Provider sample START
def provide_string(provider: ctrlxdatalayer.provider, nodeAddress: str,
                   typeAddress: str):
    """provide_string"""
    # Create and register simple string provider node
    print("Creating string  provider node " + nodeAddress, flush=True)
    variantString = Variant()
    variantString.set_string('[{"x":0,"y":0},{"x":60,"y":"90"},{"x":120,"y":270},{"x":180,"y":45},{"x":240,"y":135},{"x":300,"y":135},{"x":360,"y":0}]')
    return provide_node(provider, nodeAddress, typeAddress, variantString)


def provide_node(provider: ctrlxdatalayer.provider, nodeAddress: str,
                 typeAddress: str, value: Variant):
    """provide_node"""
    node = MyProviderNode(provider, nodeAddress, typeAddress, value)
    result = node.register_node()
    if result != ctrlxdatalayer.variant.Result.OK:
        print(
            "ERROR Registering node " + nodeAddress + " failed with:",
            result,
            flush=True,
        )

    return node
# From Provider sample END


def thread_start():
    """thread_start"""
    # If running with a snap (on a ctrlX) start UNIX socket
    # If running as app in an app builder envorinemtn start a TCP server
    run_webserver_unixsock() if "SNAP" in os.environ else run_webserver_tcp()


def run_webserver_tcp():
    """run_webserver_tcp"""
    with http.server.HTTPServer(
        ("", httpServerPort), web.request_handler.RequestHandler
    ) as http_server:
        print("TCP/IP server started - listening on 0.0.0.0:", httpServerPort)
        print("")
        print(
            "------------------Copy this link into the address field of your browser ----------------------"
        )
        # print(
        #     "http://localhost:"
        #     + str(httpServerPort)
        #     + "/cam-builder?token="
        #     + token
        # )
        print(
            "http://localhost:"
            + str(httpServerPort)
            + "/cam-builder"
        )
        print(
            "----------------------------------------------------------------------------------------------",
            flush=True,
        )

        http_server.serve_forever()

        http_server.server_close()


def run_webserver_unixsock():
    """run_webserver_unixsock"""
    sock_dir = os.getenv("SNAP_DATA") + "/package-run/cam-builder/"
    if not os.path.exists(sock_dir):
        os.makedirs(sock_dir)

    sock_file = sock_dir + "web.sock"

    try:
        os.unlink(sock_file)
    except OSError:
        pass

    with web.unix_socket_server.UnixSocketServer(
        sock_file, web.request_handler.RequestHandler
    ) as http_server:
        print("UNIX SOCKET server started - listening on", sock_file, flush=True)

        http_server.serve_forever()

        http_server.server_close()
        os.remove(sock_file)


if __name__ == "__main__":
    main()
