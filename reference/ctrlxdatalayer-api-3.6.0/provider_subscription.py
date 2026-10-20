

import ctypes
import datetime
from enum import Enum
import typing

import comm.datalayer.SubscriptionProperties
import flatbuffers
from comm.datalayer import NotifyInfo

import ctrlxdatalayer
import ctrlxdatalayer.clib_provider_node
from ctrlxdatalayer.clib_provider_node import C_DLR_SUBSCRIPTION
from ctrlxdatalayer.variant import Result, Variant, VariantRef


def _subscription_get_timestamp(sub: C_DLR_SUBSCRIPTION) -> ctypes.c_uint64:
    """_subscription_get_timestamp

    Args:
        sub (C_DLR_SUBSCRIPTION): Reference to the subscription

    Returns:
        c_uint64: timestamp
    """
    return ctrlxdatalayer.clib.libcomm_datalayer.DLR_SubscriptionGetTimestamp(sub)


def _subscription_get_props(sub: C_DLR_SUBSCRIPTION) -> VariantRef:
    """_subscription_get_props

    Args:
        sub (C_DLR_SUBSCRIPTION): Reference to the subscription

    Returns:
        VariantRef: Properties of Subscription in a Variant (see sub_properties.fbs)
    """
    return VariantRef(ctrlxdatalayer.clib.libcomm_datalayer.DLR_SubscriptionGetProps(sub))


def _subscription_get_nodes(sub: C_DLR_SUBSCRIPTION) -> Variant:
    """_subscription_get_nodes

    Args:
        sub (C_DLR_SUBSCRIPTION): Reference to the subscription

    Returns:
        Variant: Subscribed nodes as array of strings
    """
    v = Variant()
    ctrlxdatalayer.clib.libcomm_datalayer.DLR_SubscriptionGetNodes(
        sub, v.get_handle())
    return v

class NotifyTypePublish(Enum):
    """NotifyTypePublish
    """
    DATA = 0
    BROWSE = 1
    METADATA = 2
    KEEPALIVE = 3
    TYPE = 4
    EVENT = 5

class NotifyInfoPublish:
    """NotifyInfoPublish

    containing notify_info.fbs (address, timestamp, type, ...)
    """

    __slots__ = ['__node', '__timestamp', '__notify_type', \
        '__event_type', '__sequence_number', '__source_name']

    def __init__(self, node_address: str):
        """__init__

        Args:
            node_address (str):
        """
        self.__node = node_address
        self.__timestamp = datetime.datetime.now()
        self.__notify_type = NotifyTypePublish.DATA
        self.__event_type = None
        self.__sequence_number = 0
        self.__source_name = None

    def get_node(self) -> str:
        """get_node

        Returns:
            str: node address
        """
        return self.__node

    def set_timestamp(self, dt: datetime.datetime):
        """set_timestamp

        Args:
            dt (datetime.datetime):
        """
        self.__timestamp = dt
        return self

    def get_timestamp(self):
        """get_timestamp

        Returns:
            datetime.datetime:
        """
        return self.__timestamp

    def set_notify_type(self, nt: NotifyTypePublish):
        """set_notify_type

        Args:
            nt (NotifyTypePublish):
        """
        self.__notify_type = nt
        return self

    def get_notify_type(self) -> NotifyTypePublish:
        """get_notify_type

        Returns:
            NotifyTypePublish:
        """
        return self.__notify_type

    def set_event_type(self, et: str):
        """set_event_type
        In case of an event, this string contains the information
        what EventType has been fired.
        E.g.: "types/events/ExampleEvent"
        Args:
            et (str):
        """
        self.__event_type = et
        return self

    def get_event_type(self) -> str:
        """get_event_type

        Returns:
            str:
        """
        return self.__event_type

    def set_sequence_number(self, sn: int):
        """set_sequence_number
        sequence number of an event
        Args:
            sn (int): 0 default
        """
        self.__sequence_number = sn
        return self

    def get_sequence_number(self) -> int:
        """get_sequence_number

        Returns:
            int:
        """
        return self.__sequence_number

    def set_source_name(self, source: str):
        """set_source_name
        description of the source of an event

        Args:
            source (str):
        """
        self.__source_name = source
        return self

    def get_source_name(self) -> str:
        """get_source_name

        Returns:
            str:
        """
        return self.__source_name

class NotifyItemPublish:
    """
    class NotifyItemPublish:

    """
    __slots__ = ['__data', '__info', '__notify_info']

    def __init__(self, node_address: str):
        """
            __init__
        """
        self.__data = Variant()
        self.__info = Variant()
        self.__notify_info = NotifyInfoPublish(node_address)

    def __enter__(self):
        """
        use the python context manager
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        use the python context manager
        """
        self.close()

    def __del__(self):
        """
        __del__
        """
        self.close()

    def close(self):
        """
        closes the instance
        """
        self.__data.close()
        self.__info.close()

    def get_data(self) -> Variant:
        """get_data

        Returns:
            Variant: data of the notify item
        """
        return self.__data

    def get_notify_info(self) -> NotifyInfoPublish:
        """get_notify_info

        Returns:
            get_notify_info:
        """
        return self.__notify_info

    def get_info(self) -> Variant:
        """internal use

        Returns:
            Variant: containing notify_info.fbs (address, timestamp, type, ...)
        """
        builder = flatbuffers.Builder(1024)

        ni = NotifyInfo.NotifyInfoT()
        ni.node = self.__notify_info.get_node()
        ni.timestamp = Variant.to_filetime(self.__notify_info.get_timestamp())
        ni.notifyType = self.__notify_info.get_notify_type().value
        ni.eventType = self.__notify_info.get_event_type()
        ni.sequenceNumber = self.__notify_info.get_sequence_number()
        ni.sourceName = self.__notify_info.get_source_name()

        ni_int = ni.Pack(builder)
        builder.Finish(ni_int)

        self.__info.set_flatbuffers(builder.Output())
        return self.__info


def _subscription_publish(sub: C_DLR_SUBSCRIPTION,  status: Result, items: typing.List[NotifyItemPublish]) -> Result:
    """_subscription_publish

    Args:
        sub (C_DLR_SUBSCRIPTION): Reference to the subscription
        status (Result): Status of notification. On failure subscription is canceled for all items.
        items (typing.List[NotifyItemPublish]): Notification items

    Returns:
        Result:
    """
    elems = (ctrlxdatalayer.clib_provider_node.C_DLR_NOTIFY_ITEM * len(items))()
    #for i in range(len(items)):
    for i, item in enumerate(items):
        elems[i].data = item.get_data().get_handle()
        elems[i].info = item.get_info().get_handle()

    notify_items = ctypes.cast(elems, ctypes.POINTER(
                ctrlxdatalayer.clib_provider_node.C_DLR_NOTIFY_ITEM))
    len_item = ctypes.c_size_t(len(items))
    return Result(ctrlxdatalayer.clib.libcomm_datalayer.DLR_SubscriptionPublish(sub, status.value, notify_items, len_item))


class ProviderSubscription:
    """
    ProviderSubscription helper class
    """
    __slots__ = ['__subscription', '__id']

    def __init__(self, sub: C_DLR_SUBSCRIPTION):
        """
        init ProviderSubscription
        """
        self.__subscription = sub
        self.__id = None

    def get_unique_id(self) -> int:
        """get_unique_id

        Returns:
            int: unique subscription identifier
        """
        return int(self.__subscription)

    def get_id(self) -> str:
        """get_id

        Returns:
            str: subscription id
        """
        if self.__id is None:
            self.__id = self.get_props().Id().decode('utf-8')

        return self.__id

    def get_props(self) -> comm.datalayer.SubscriptionProperties:
        """get_props

        Returns:
            comm.datalayer.SubscriptionProperties: subscription properties
        """
        v = _subscription_get_props(self.__subscription)
        return comm.datalayer.SubscriptionProperties.SubscriptionProperties.GetRootAsSubscriptionProperties(v.get_flatbuffers(), 0)

    def get_timestamp(self) -> datetime.datetime:
        """timestamp

        Returns:
            datetime.datetime: timestamp
        """
        val = _subscription_get_timestamp(self.__subscription)
        return Variant.from_filetime(val)

    def get_notes(self) -> typing.List[str]:
        """get_notes

        Returns:
            typing.List[str]: Subscribed nodes as array of strings
        """
        val = _subscription_get_nodes(self.__subscription)
        with val:
            return val.get_array_string()

    def publish(self, status: Result, items: typing.List[NotifyItemPublish]) -> Result:
        """publish

        Args:
            status (Result): Status of notification. On failure subscription is canceled for all items.
            items (typing.List[NotifyItemPublish]): Notification items
        """
        return _subscription_publish(self.__subscription, status, items)
