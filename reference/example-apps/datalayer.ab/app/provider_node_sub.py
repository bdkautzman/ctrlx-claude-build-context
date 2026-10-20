"""
Class Provider Node Sub
"""
import ctypes
import typing

import ctrlxdatalayer
from ctrlxdatalayer.clib import C_DLR_RESULT
from ctrlxdatalayer.clib_provider_node import (C_DLR_PROVIDER_NODE_CALLBACK,
                                         C_DLR_PROVIDER_NODE_CALLBACKDATA,
                                         C_DLR_PROVIDER_NODE_CALLBACKS,
                                         C_DLR_PROVIDER_NODE_FUNCTION,
                                         C_DLR_PROVIDER_NODE_FUNCTION_DATA,
                                         C_DLR_VARIANT, address_c_char_p,
                                         userData_c_void_p, C_DLR_SUBSCRIPTION)
from ctrlxdatalayer.variant import Result, Variant

from ctrlxdatalayer.provider_node import ProviderNode, _CallbackPtr

SubscriptionFunction = typing.Callable[[userData_c_void_p, C_DLR_SUBSCRIPTION, str], None]
NodeCallback = typing.Callable[[Result, typing.Optional[Variant]], None]
NodeFunction = typing.Callable[[userData_c_void_p, str, NodeCallback], None]
NodeFunctionData = typing.Callable[[
    userData_c_void_p, str, Variant, NodeCallback], None]

class ProviderNodeCallbacks2:
    """
        Provider Node callbacks  interface
    """
    __slots__ = ['on_create', 'on_remove', 'on_browse',
                 'on_read', 'on_write', 'on_metadata', 
                 'on_subscribe', 'on_unsubscribe']

    def __init__(self,
                 on_create: NodeFunctionData,
                 on_remove: NodeFunction,
                 on_browse: NodeFunction,
                 on_read: NodeFunctionData,
                 on_write: NodeFunctionData,
                 on_metadata: NodeFunction,
                 on_subscribe: SubscriptionFunction,
                 on_unsubscribe: SubscriptionFunction):
        """
        init ProviderNodeCallbacks
        """
        self.on_create = on_create
        self.on_remove = on_remove
        self.on_browse = on_browse
        self.on_read = on_read
        self.on_write = on_write
        self.on_metadata = on_metadata
        self.on_subscribe = on_subscribe
        self.on_unsubscribe = on_unsubscribe

class SubscriptionNode(ProviderNode):
    """
    Extends provider node interface for providing data to the system. Addition of subsciption event callbacks.

    Hint: see python context manager for instance handling
    """
    def __init__(self, cbs: ProviderNodeCallbacks2, userdata: userData_c_void_p = None):
        """
        init ProviderNode
        """
        self.__ptrs: typing.List[_CallbackPtr] = []
        self.__c_cbs = C_DLR_PROVIDER_NODE_CALLBACKS(
            userdata,
            self.__create_function_data(cbs.on_create),
            self.__create_function(cbs.on_remove),
            self.__create_function(cbs.on_browse),
            self.__create_function_data(cbs.on_read),
            self.__create_function_data(cbs.on_write),
            self.__create_function(cbs.on_metadata),
            self.__create_subscription_function(cbs.on_subscribe),
            self.__create_subscription_function(cbs.on_unsubscribe)
        )
        self.__closed = False
        self.__provider_node = ctrlxdatalayer.clib.libcomm_datalayer.DLR_providerNodeCreate(
            self.__c_cbs)
        
    def __create_subscription_function(self, func: SubscriptionFunction):
        """
        create callback management for subscription functions
        """
        cb_ptr = _CallbackPtr()
        self.__ptrs.append(cb_ptr)

        def _func(c_userdata: userData_c_void_p,
                  c_address: address_c_char_p,
                  c_cb: C_DLR_PROVIDER_NODE_CALLBACK,
                  c_cbdata: C_DLR_PROVIDER_NODE_CALLBACKDATA) -> C_DLR_RESULT:
            """
            datalayer calls this function
            """
            address = c_address.decode('utf-8')
            cb = self.__create_callback(c_cb, c_cbdata)
            func(c_userdata, address, cb)
            return Result.OK.value
        cb_ptr.set_ptr(C_DLR_PROVIDER_NODE_FUNCTION(_func))
        return cb_ptr.get_ptr()