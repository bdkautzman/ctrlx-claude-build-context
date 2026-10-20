# MIT License
#
# Copyright (c) 2021, Bosch Rexroth AG
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


import ctrlxdatalayer
from ctrlxdatalayer.provider import Provider
from ctrlxdatalayer.provider_node import ProviderNode, ProviderNodeCallbacks, NodeCallback
from app.provider_node_sub import SubscriptionNode, ProviderNodeCallbacks2
from ctrlxdatalayer.variant import Result, Variant
from pylogix import PLC
from comm.datalayer import NodeClass
from ctrlxdatalayer.metadata_utils import MetadataBuilder

class CIPnode:

    def __init__(self, provider : Provider, CIPTagName : str, controller : PLC, type : str, path : str):
        
        self.cbs = ProviderNodeCallbacks(
            self.__on_create,
            self.__on_remove,
            self.__on_browse,
            self.__on_read,
            self.__on_write,
            self.__on_metadata
        )

        self.providerNode = ProviderNode(self.cbs)
        self.provider = provider
        self.data = Variant()
        self.address = "CIP-Data/" + path 
        self.CIPTagName = CIPTagName
        self.controller = controller
        self.dataType = self.getVariantType(type)
        self.type = type      

        self.metadata = MetadataBuilder.create_metadata(
            self.CIPTagName, self.CIPTagName, "", "", NodeClass.NodeClass.Variable, 
            read_allowed=True, write_allowed=True, create_allowed=False, delete_allowed=False, browse_allowed=True,
            type_path = "")
        
    def register_node(self):
      self.provider.register_node(self.address, self.providerNode)      
    
    def unregister_node(self):
        self.provider.unregister_node(self.address)
    
    def set_value(self,value: Variant):
        self.data = value

    def __on_create(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        cb(Result.OK, data)

    def __on_remove(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.UNSUPPORTED, None)

    def __on_browse(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        new_data = Variant()
        new_data.set_array_string([])
        cb(Result.OK, new_data)

    def __on_read(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        new_data = self.data
        try:
            ret = self.controller.Read(self.CIPTagName)
            self.readVariantValue(ret.Value)
            new_data = self.data
            cb(Result.OK, new_data)
        except:
            cb(Result.FAILED, new_data)    
        

    def __on_write(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        _data = data
        try:
            self.writeVariantValue(data)
            cb(Result.OK, self.data)
        except:
            cb(Result.FAILED, self.data)

    def __on_metadata(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.OK, self.metadata)

    def readVariantValue(self, data : object) -> Result:
        try:
            if self.type == "BOOL":
                return self.data.set_bool8(data)
            elif self.type == "SINT":
                return self.data.set_int8(data)
            elif self.type == "INT":
                return self.data.set_int16(data)    
            elif self.type == "DINT":
                return self.data.set_int32(data)    
            elif self.type == "LINT":
                return self.data.set_int64(data)    
            elif self.type == "USINT":
                return self.data.set_uint8(data)    
            elif self.type == "UINT":
                return self.data.set_uint16(data)    
            elif self.type == "UDINT":
                return self.data.set_uint32(data)    
            elif self.type == "LWORD":
                return self.data.set_uint64(data)    
            elif self.type == "REAL":
                return self.data.set_float32(data)    
            elif self.type == "LREAL":
                return self.data.set_float64(data)    
            elif self.type == "DWORD":
                return self.data.set_uint32(data)
            elif self.type == "STRING":
                return self.data.set_string(data)
            else:
                return self.data
        except Exception as e:
            print("Failed to read tag: " + self.CIPTagName + " with exception: " + e)

    def writeVariantValue(self, data : Variant):
        try:
            if self.type == "BOOL":
                self.controller.Write(self.CIPTagName, data.get_bool8())
            elif self.type == "SINT":
                self.controller.Write(self.CIPTagName, data.get_int8())
            elif self.type == "INT":
                self.controller.Write(self.CIPTagName, data.get_int16())
            elif self.type == "DINT":
                self.controller.Write(self.CIPTagName, data.get_int32())
            elif self.type == "LINT":
                self.controller.Write(self.CIPTagName, data.get_int64())
            elif self.type == "USINT":
                self.controller.Write(self.CIPTagName, data.get_uint8())
            elif self.type == "UINT":
                self.controller.Write(self.CIPTagName, data.get_uint16())
            elif self.type == "UDINT":
                self.controller.Write(self.CIPTagName, data.get_uint32())
            elif self.type == "LWORD":
                self.controller.Write(self.CIPTagName, data.get_uint64())
            elif self.type == "REAL":
                self.controller.Write(self.CIPTagName, data.get_float32())
            elif self.type == "LREAL":
                self.controller.Write(self.CIPTagName, data.get_float64())
            elif self.type == "DWORD":
                self.controller.Write(self.CIPTagName, data.get_uint32())
            elif self.type == "STRING":
                self.controller.Write(self.CIPTagName, data.get_string())
            else:
                print("Failed to write tag: " + self.CIPTagName)        
        except Exception as e:
            print("Failed to write tag: " + self.CIPTagName + " with exception: " + e)

    def getVariantType(self, type : str):
        try:
            if type == "BOOL":
                return "bool8"
            elif type == "SINT":
                return "int8"
            elif type == "INT":
                return "int16"
            elif type == "DINT":
                return "int32"    
            elif type == "LINT":
                return "int64"    
            elif type == "USINT":
                return "uint8"
            elif type == "UINT":
                return "uint16"    
            elif type == "UDINT":
                return "uint32"
            elif type == "LWORD":
                return "uint64"
            elif type == "REAL":
                return "float32"
            elif type == "LREAL":
                return "float64"
            elif type == "DWORD":
                return "uint32"
            elif type == "STRING":
                return "string"
            else:
                return "UNKNON"
        except Exception as e:
            print("Failed to get type for tag: " + self.CIPTagName + " with exception: " + e)

class CIPnode2(CIPnode):

    def __init__(self, provider : Provider, CIPTagName : str, controller : PLC, type : str, path : str):
        
        self.cbs = ProviderNodeCallbacks2(
            self.__on_create,
            self.__on_remove,
            self.__on_browse,
            self.__on_read,
            self.__on_write,
            self.__on_metadata,
            self.__on_subscribe,
            self.__on_unsubscribe
        )

        self.providerNode = SubscriptionNode(self.cbs)
        self.provider = provider
        self.data = Variant()
        self.address = "CIP-Data/" + path 
        self.CIPTagName = CIPTagName
        self.controller = controller
        self.dataType = self.getVariantType(type)
        self.type = type      

        self.metadata = MetadataBuilder.create_metadata(
            self.CIPTagName, self.CIPTagName, "", "", NodeClass.NodeClass.Variable, 
            read_allowed=True, write_allowed=True, create_allowed=False, delete_allowed=False, browse_allowed=True,
            type_path = "")
        
    def __on_subscribe(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        print("Subscribed to node: " + self.address)

    def __on_subscribe(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        print("Unsubscribed to node: " + self.address)

class CIPnodeBulk:

    def __init__(self, provider : Provider, CIPTagName : str, controller : PLC, type : str, path : str, tagListData: list, index : int):
        
        self.cbs = ProviderNodeCallbacks(
            self.__on_create,
            self.__on_remove,
            self.__on_browse,
            self.__on_read,
            self.__on_write,
            self.__on_metadata
        )

        self.providerNode = ProviderNode(self.cbs)
        self.provider = provider
        self.data = Variant()
        self.address = "CIP-Data/" + path 
        self.CIPTagName = CIPTagName
        self.controller = controller
        self.dataType = self.getVariantType(type)
        self.type = type 
        self.index = index   
        self.tagList = tagListData  

        self.metadata = MetadataBuilder.create_metadata(
            self.CIPTagName, self.CIPTagName, "", "", NodeClass.NodeClass.Variable, 
            read_allowed=True, write_allowed=True, create_allowed=False, delete_allowed=False, browse_allowed=True,
            type_path = "")

    def register_node(self):
      self.provider.register_node(self.address, self.providerNode)      
    
    def unregister_node(self):
        self.provider.unregister_node(self.address)
    
    def set_value(self,value: Variant):
        self.data = value

    def __on_create(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        cb(Result.OK, data)

    def __on_remove(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.UNSUPPORTED, None)

    def __on_browse(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        new_data = Variant()
        new_data.set_array_string([])
        cb(Result.OK, new_data)

    def __on_read(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        new_data = self.data
        try:
            ret = self.tagList[self.index].Value
            self.readVariantValue(ret)
            new_data = self.data
            cb(Result.OK, new_data)
        except:
            cb(Result.FAILED, new_data)    
        

    def __on_write(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        _data = data
        try:
            self.writeVariantValue(data)
            cb(Result.OK, self.data)
        except:
            cb(Result.FAILED, self.data)

    def __on_metadata(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.OK, self.metadata)

    def readVariantValue(self, data : object) -> Result:
        try:
            if self.type == "BOOL":
                return self.data.set_bool8(data)
            elif self.type == "SINT":
                return self.data.set_int8(data)
            elif self.type == "INT":
                return self.data.set_int16(data)    
            elif self.type == "DINT":
                return self.data.set_int32(data)    
            elif self.type == "LINT":
                return self.data.set_int64(data)    
            elif self.type == "USINT":
                return self.data.set_uint8(data)    
            elif self.type == "UINT":
                return self.data.set_uint16(data)    
            elif self.type == "UDINT":
                return self.data.set_uint32(data)    
            elif self.type == "LWORD":
                return self.data.set_uint64(data)    
            elif self.type == "REAL":
                return self.data.set_float32(data)    
            elif self.type == "LREAL":
                return self.data.set_float64(data)    
            elif self.type == "DWORD":
                return self.data.set_uint32(data)
            elif self.type == "STRING":
                return self.data.set_string(data)
            else:
                return self.data
        except Exception as e:
            print("Failed to read tag: " + self.CIPTagName + " with exception: " + e)

    def writeVariantValue(self, data : Variant):
        try:
            if self.type == "BOOL":
                self.controller.Write(self.CIPTagName, data.get_bool8())
            elif self.type == "SINT":
                self.controller.Write(self.CIPTagName, data.get_int8())
            elif self.type == "INT":
                self.controller.Write(self.CIPTagName, data.get_int16())
            elif self.type == "DINT":
                self.controller.Write(self.CIPTagName, data.get_int32())
            elif self.type == "LINT":
                self.controller.Write(self.CIPTagName, data.get_int64())
            elif self.type == "USINT":
                self.controller.Write(self.CIPTagName, data.get_uint8())
            elif self.type == "UINT":
                self.controller.Write(self.CIPTagName, data.get_uint16())
            elif self.type == "UDINT":
                self.controller.Write(self.CIPTagName, data.get_uint32())
            elif self.type == "LWORD":
                self.controller.Write(self.CIPTagName, data.get_uint64())
            elif self.type == "REAL":
                self.controller.Write(self.CIPTagName, data.get_float32())
            elif self.type == "LREAL":
                self.controller.Write(self.CIPTagName, data.get_float64())
            elif self.type == "DWORD":
                self.controller.Write(self.CIPTagName, data.get_uint32())
            elif self.type == "STRING":
                self.controller.Write(self.CIPTagName, data.get_string())
            else:
                print("Failed to write tag: " + self.CIPTagName)        
        except Exception as e:
            print("Failed to write tag: " + self.CIPTagName + " with exception: " + e)

    def getVariantType(self, type : str):
        try:
            if type == "BOOL":
                return "bool8"
            elif type == "SINT":
                return "int8"
            elif type == "INT":
                return "int16"
            elif type == "DINT":
                return "int32"    
            elif type == "LINT":
                return "int64"    
            elif type == "USINT":
                return "uint8"
            elif type == "UINT":
                return "uint16"    
            elif type == "UDINT":
                return "uint32"
            elif type == "LWORD":
                return "uint64"
            elif type == "REAL":
                return "float32"
            elif type == "LREAL":
                return "float64"
            elif type == "DWORD":
                return "uint32"
            elif type == "STRING":
                return "string"
            else:
                return "UNKNON"
        except Exception as e:
            print("Failed to get type for tag: " + self.CIPTagName + " with exception: " + e)

class CIPnode_Array:

    def __init__(self, provider : Provider, CIPTagName : str, controller : PLC, type : str, path : str):
        
        self.cbs = ProviderNodeCallbacks(
            self.__on_create,
            self.__on_remove,
            self.__on_browse,
            self.__on_read,
            self.__on_write,
            self.__on_metadata
        )

        self.providerNode = ProviderNode(self.cbs)
        self.provider = provider
        self.data = Variant()
        self.address = "CIP-Data/" + path 
        self.CIPTagName = CIPTagName
        self.controller = controller
        self.dataType = self.getVariantType(type)
        self.type = type

        self.metadata = MetadataBuilder.create_metadata(
            self.CIPTagName, self.CIPTagName, "", "", NodeClass.NodeClass.Variable, 
            read_allowed=True, write_allowed=True, create_allowed=False, delete_allowed=False, browse_allowed=False,
            type_path= self.dataType)

    def register_node(self):
      self.provider.register_node(self.address, self.providerNode)      
    
    def unregister_node(self):
        self.provider.unregister_node(self.address)
    
    def set_value(self,value: Variant):
        self.data = value

    def __on_create(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        cb(Result.OK, data)

    def __on_remove(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.UNSUPPORTED, None)

    def __on_browse(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        new_data = Variant()
        new_data.set_array_string([])
        cb(Result.OK, new_data)

    def __on_read(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        new_data = self.data
        print(self.CIPTagName)
        with self.controller as con:
            ret = con.Read(self.CIPTagName)
            self.readVariantValue(ret.Value)
        new_data = self.data
        cb(Result.OK, new_data)

    def __on_write(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, data: Variant, cb: NodeCallback):
        _data = data
        self.writeVariantValue(data)
        cb(Result.OK, self.data)

    def __on_metadata(self, userdata: ctrlxdatalayer.clib.userData_c_void_p, address: str, cb: NodeCallback):
        cb(Result.OK, self.metadata)

    def readVariantValue(self, data : object) -> Result:
        try:
            if self.type == "BOOL":
                return self.data.set_bool8(data)
            elif self.type == "SINT":
                return self.data.set_int8(data)
            elif self.type == "INT":
                return self.data.set_int16(data)    
            elif self.type == "DINT":
                return self.data.set_int32(data)    
            elif self.type == "LINT":
                return self.data.set_int64(data)    
            elif self.type == "USINT":
                return self.data.set_uint8(data)    
            elif self.type == "UINT":
                return self.data.set_uint16(data)    
            elif self.type == "UDINT":
                return self.data.set_uint32(data)    
            elif self.type == "LWORD":
                return self.data.set_uint64(data)    
            elif self.type == "REAL":
                return self.data.set_float32(data)    
            elif self.type == "LREAL":
                return self.data.set_float64(data)    
            elif self.type == "DWORD":
                return self.data.set_uint32(data)
            elif self.type == "STRING":
                return self.data.set_string(data)
        except Exception as e:
            print(e)

    def writeVariantValue(self, data : Variant):
        with self.controller as con:
            try:
                if self.type == "BOOL":
                   con.Write(self.CIPTagName, data.get_bool8())
                elif self.type == "SINT":
                    con.Write(self.CIPTagName, data.get_int8())
                elif self.type == "INT":
                    con.Write(self.CIPTagName, data.get_int16())
                elif self.type == "DINT":
                    con.Write(self.CIPTagName, data.get_int32())
                elif self.type == "LINT":
                    con.Write(self.CIPTagName, data.get_int64())
                elif self.type == "USINT":
                    con.Write(self.CIPTagName, data.get_uint8())
                elif self.type == "UINT":
                    con.Write(self.CIPTagName, data.get_uint16())
                elif self.type == "UDINT":
                    con.Write(self.CIPTagName, data.get_uint32())
                elif self.type == "LWORD":
                    con.Write(self.CIPTagName, data.get_uint64())
                elif self.type == "REAL":
                    con.Write(self.CIPTagName, data.get_float32())
                elif self.type == "LREAL":
                    con.Write(self.CIPTagName, data.get_array_float64())
                elif self.type == "DWORD":
                    con.Write(self.CIPTagName, data.get_uint32())
                elif self.type == "STRING":
                    con.Write(self.CIPTagName, data.get_string())
            except Exception as e:
                print(e)

    def getVariantType(self, type : str):
        try:
            if type == "BOOL":
                return "bool8"
            elif type == "SINT":
                return "int8"
            elif type == "INT":
                return "int16"
            elif type == "DINT":
                return "int32"    
            elif type == "LINT":
                return "int64"    
            elif type == "USINT":
                return "uint8"
            elif type == "UINT":
                return "uint16"    
            elif type == "UDINT":
                return "uint32"
            elif type == "LWORD":
                return "uint64"
            elif type == "REAL":
                return "float32"
            elif type == "LREAL":
                return "float64"
            elif type == "DWORD":
                return "uint32"
            elif type == "STRING":
                return "string"
        except Exception as e:
            print(e) 
