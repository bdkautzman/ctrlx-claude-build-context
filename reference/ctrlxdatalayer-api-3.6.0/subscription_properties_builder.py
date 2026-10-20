"""
    This module provides helper classes to deal with subscription properties flatbuffers.
"""
import flatbuffers
from comm.datalayer import (ChangeEvents, Counting, DataChangeFilter,
                            LosslessRateLimit, Properties, Property, Queueing,
                            Sampling, SubscriptionProperties)

from ctrlxdatalayer.variant import Variant


class SubscriptionPropertiesBuilder:
    """SubscriptionPropertiesBuilder
    """

    def __init__(self, id_val: str):
        """_summary_

        Args:
            id_val (str):
        """
        self.__id = id_val
        self.__keepalive_interval = 60000
        self.__publish_interval = 1000
        self.__error_interval = 10000
        self.__rules = []

    def build(self) -> Variant:
        """Build Subscription Properties as Variant

        Returns:
            Variant: Subscription Properties
        """
        builder = flatbuffers.Builder()
        sub_prop = SubscriptionProperties.SubscriptionPropertiesT()
        sub_prop.id = self.__id
        sub_prop.keepaliveInterval = self.__keepalive_interval
        sub_prop.publishInterval = self.__publish_interval
        sub_prop.rules = None
        if len(self.__rules) != 0:
            sub_prop.rules = self.__rules
        sub_prop.errorInterval = self.__error_interval


        sub_prop_internal = sub_prop.Pack(builder)

        # Closing operation
        builder.Finish(sub_prop_internal)

        subprop = Variant()
        subprop.set_flatbuffers(builder.Output())
        return subprop

    def set_keepalive_interval(self, interval: int):
        """set_keepalive_interval

        Args:
            interval (int): keep alvive interval
        """
        self.__keepalive_interval = interval
        return self

    def set_publish_interval(self, interval: int):
        """set_publish_interval

        Args:
            interval (int): publish interval
        """
        self.__publish_interval = interval
        return self

    def set_error_interval(self, interval: int):
        """set_error_interval

        Args:
            interval (int): error interval
        """
        self.__error_interval = interval
        return self

    def add_rule_sampling(self, rule: Sampling.SamplingT):
        """add_rule_sampling

        !!!Hint: 'samplingInterval = 0' only RT nodes, see "datalayer/nodesrt"
        Args:
            rule (Sampling.SamplingT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().Sampling
        prop.rule = rule
        self.__rules.append(prop)
        return self

    def add_rule_queueing(self, rule: Queueing.QueueingT):
        """add_rule_queueing

        Args:
            rule (Queueing.QueueingT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().Queueing
        prop.rule = rule
        self.__rules.append(prop)
        return self

    def add_rule_datachangefilter(self, rule: DataChangeFilter.DataChangeFilterT):
        """add_rule_datachangefilter

        Args:
            rule (DataChangeFilter.DataChangeFilterT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().DataChangeFilter
        prop.rule = rule
        self.__rules.append(prop)
        return self

    def add_rule_changeevents(self, rule: ChangeEvents.ChangeEventsT):
        """add_rule_changeevents

        Args:
            rule (ChangeEvents.ChangeEventsT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().ChangeEvents
        prop.rule = rule
        self.__rules.append(prop)
        return self

    def add_rule_counting(self, rule: Counting.CountingT):
        """add_rule_counting

        Args:
            rule (Counting.CountingT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().Counting
        prop.rule = rule
        self.__rules.append(prop)
        return self

    def add_rule_losslessratelimit(self, rule: LosslessRateLimit.LosslessRateLimitT):
        """add_rule_losslessratelimit

        Args:
            rule (LosslessRateLimit.LosslessRateLimitT):
        """
        prop = Property.PropertyT()
        prop.ruleType = Properties.Properties().LosslessRateLimit
        prop.rule = rule
        self.__rules.append(prop)
        return self
