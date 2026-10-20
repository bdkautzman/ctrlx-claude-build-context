#!/usr/bin/env python3

# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import json
from pathlib import Path

import ctrlxdatalayer
from ctrlxdatalayer.variant import Variant, Result

from app.utils import myLogger, DuplicateFilter

from helper.ctrlx_datalayer_helper import get_provider

from app.sql_provider_node import SQLiteNode

#NUMBER_OF_TERMINALS = 4 #define the number of terminals required. 

# addresses of provided values
address_base = "SQLite/"
type_address_string = "types/datalayer/string"


def loadConfig():
    """
    Loads config file and sets logging path depending on environment
        :param void:
        :return configPath: = path to configuration file 
        :return logPath: = path to log file
        :return configData: = parsed JSON of config file
    """
    global configPath
    global logPath
    global configData
    global filePath

    snap_path = os.getenv('SNAP')
    print(snap_path)
    if snap_path is None:
        filePath = "./DEV/"
        configPath = "./DEV/config.json"
        logPath = "./DEV/info.log"
    else:
        #if not os.path.exists(snap_path + "/solutions/activeConfiguration/SQLite"):
        #        os.mkdir(snap_path + "/solutions/activeConfiguration/SQLite")
        filePath = "/var/snap/rexroth-solutions/common/solutions/activeConfiguration/SQLite/"
        configPath = "/var/snap/rexroth-solutions/common/solutions/activeConfiguration/SQLite/config.json"
        logPath = "/var/snap/rexroth-solutions/common/solutions/activeConfiguration/SQLite/info.log"

    # Read config.json
    print(configPath)
    try:
        with open(configPath) as jsonConfig:
            configData = json.load(jsonConfig)
            # Delete previous logs if persistence is disabled
            if not configData["LOG PERSIST"]:
                for file in os.listdir(filePath):
                    if ".log" in file:
                        os.remove(Path(filePath+file))
    except Exception as e:
        myLogger("Failed to read config.json. Exception: "  + repr(e), logging.ERROR, source=__name__)

    # Configure the logger for easier analysis
    logger = logging.getLogger('__main__')
    logger.handlers.clear()
    logFormatter = logging.Formatter(fmt='%(asctime)s:%(msecs)d, %(name)s, %(levelname)s, %(message)s', datefmt='%H:%M:%S')
    # Set max file size to 100kB. Rollover to new logs when exceeded
    logHandler = RotatingFileHandler(logPath, mode='a', maxBytes=100*1024, 
                                 backupCount=10, encoding=None, delay=0)
    logHandler.setFormatter(logFormatter) 
    logHandler.setLevel(logging.DEBUG)
    logger.addHandler(logHandler)
    logger.addFilter(DuplicateFilter())

    # Set log level based on configured value
    if(configData["LOG LEVEL"]):
        logLevel = configData["LOG LEVEL"]
        logger.setLevel(logging.getLevelName(logLevel))
    
    myLogger("cltrX File Path: " + str(snap_path), logging.INFO, source=__name__) 

    # Get the time the files was modified
    fileTime = os.stat(configPath).st_mtime
    myLogger("Config modified at UNIX TIME " + str(fileTime), logging.INFO, source=__name__)

    myLogger("Config data: " + str(configData), logging.INFO, source=__name__)

def main():
    myLogger('#######################################################################', logging.DEBUG, source=__name__)
    myLogger('#######################################################################', logging.DEBUG, source=__name__)
    myLogger('#######################################################################', logging.DEBUG, source=__name__)
    myLogger('Initializing application', logging.INFO)   
    
    with ctrlxdatalayer.system.System("") as datalayer_system:
        datalayer_system.start(False)

        # ip="10.0.2.2", ssl_port=8443: ctrlX virtual with port forwarding and default port mapping
        provider, connection_string = get_provider(
            datalayer_system, ip="10.0.2.2", ssl_port=8443)
        if provider is None:
            print("ERROR Connecting", connection_string, "failed.")
            myLogger("ERROR Connecting", connection_string, "failed.", logging.ERROR, source=__name__)
            sys.exit(1)

        loadConfig() 
        

        #if not os.path.exists("/var/snap/rexroth-solutions/common/solutions/activeConfiguration/SQLite"):
       # common_path = os.getenv("SNAP_COMMON")
       # print(common_path)
       # if common_path is not None:
       #     if not os.path.exists(common_path + "/solutions/activeConfiguration/SQLite"):
       #         os.mkdir(common_path + "/solutions/activeConfiguration/SQLite")
       # else:    
       #     common_path = "./DEV"
           

        with provider:  # provider.close() is called automatically when leaving with... block

            result = provider.start()
            if result != Result.OK:
                print("ERROR Starting Data Layer Provider failed with:", result)
                myLogger("ERROR Starting Data Layer Provider failed with:", result, logging.ERROR, source=__name__)
                return

            #Get the terminals from the config file
            nodePaths = []
            nodePaths = configData["terminals"]
            NUMBER_OF_TERMINALS = len(nodePaths)
            
            provider_node = []
            provider_node = [0 for i in range(NUMBER_OF_TERMINALS)]

            for i in range(NUMBER_OF_TERMINALS):
                provider_node[i] = provide_string(provider, nodePaths[i])
                #provider_node[i] = provide_string(provider, "Terminal_" + str(i))
            
            #print("INFO Running endless loop...")
            myLogger("INFO Running endless loop...", logging.INFO, source=__name__)
            while provider.is_connected():
                time.sleep(1.0)  # Seconds

            print("ERROR Data Layer Provider is disconnected")
            myLogger("Data Layer Provider is disconnected", logging.INFO, source=__name__)

            for i in provider_node:
                i.unregister_node()
                del i

            print("Stopping Data Layer provider:", end=" ")
            result = provider.stop()
            print(result)

        # Attention: Doesn't return if any provider or client instance is still running
        stop_ok = datalayer_system.stop(False)
        print("System Stop", stop_ok)


def provide_string(provider: ctrlxdatalayer.provider, name: str):
    # Create and register simple string provider node
    print("Creating string  provider node " + address_base + name)
    myLogger("Creating string  provider node " + address_base + name, logging.INFO, source=__name__)
    variantString = Variant()
    variantString.set_string("Enter SQL script here. Use ';' as the last character to suppress result")
    provider_node_str = SQLiteNode(
        provider, 
        type_address_string,
        address_base + name, 
        name, 
        "-",
        "SQL-Terminal",
        variantString,
        configData["database path"])
    result = provider_node_str.register_node()
    if result != ctrlxdatalayer.variant.Result.OK:
        #print("ERROR Registering node " + address_base +
        #      name + " failed with:", result)
        myLogger("ERROR Registering node " + address_base +
              name + " failed with:", result, logging.WARNING, source=__name__)

    return provider_node_str

if __name__ == '__main__':
    main()
