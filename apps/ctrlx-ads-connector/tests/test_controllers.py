"""controllers.py (thread per controller), plc_nodes.py and the settings validator - on the fake PLC of
app/dev_fake_plc.py (no pyads, no controller needed)."""

import json
import os
import time
import unittest

from app import ads_types as at
from app import controllers
from app.ads_link import AdsError
from app.controllers import Controller, ControllerManager, ControllerUnavailable
from app.datalayer_nodes import NodeWriteError
from app.dev_fake_plc import FAKE_INFO, FakeLink, FakePlc, demo_tables, sym_entry
from app.plc_nodes import NodeMap, NodeUnavailable, default_address, valid_address
from app.settings_rules import validate_settings

HERE = os.path.dirname(os.path.abspath(__file__))
ADS = {"localNetId": "", "timeoutMs": 1000}


def wait_for(cond, timeout=5.0, msg="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for " + msg)


def node(address, symbol, vtype, writable=False, controller="cx"):
    return {"controller": controller, "symbol": symbol, "address": address, "type": vtype, "writable": writable,
            "description": "", "unit": ""}


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self._backoff = controllers.BACKOFF_S
        controllers.BACKOFF_S = (0.05, 0.05, 0.1)
        self.plc = FakePlc()
        self.links = []
        self.discover_fails = False

        def factory(host, net_id, timeout_ms):
            link = FakeLink(self.plc)
            link.args = (host, net_id, timeout_ms)
            self.links.append(link)
            return link

        def discover(host):
            if self.discover_fails:
                raise AdsError(None, "no answer on UDP port 48899")
            return dict(FAKE_INFO)
        self.nodes = NodeMap()
        self.cfg = {"name": "cx", "host": "10.0.0.9", "amsNetId": "", "amsPort": 851, "pollIntervalMs": 20,
                    "enabled": True}
        self.factory, self.discover = factory, discover
        self.c = None

    def tearDown(self):
        if self.c:
            self.c.stop()
        controllers.BACKOFF_S = self._backoff

    def start(self, nodes=(), **cfg):
        self.nodes.configure(list(nodes))
        self.c = Controller(dict(self.cfg, **cfg), ADS, self.nodes, self.factory, self.discover)
        self.nodes.writer = lambda ctrl, sym, vtype, value: self.c.write_symbol(sym, value, vtype=vtype)
        self.c.start()
        wait_for(lambda: self.c.status()["state"] == "connected" and self.c.status()["plcState"] == 5, msg="connect")

    def value(self, address):
        e = self.nodes.get("cx", address)
        return e.value if e.ok else None

    def test_connect_and_status(self):
        self.start()
        st = self.c.status()
        self.assertEqual((st["netId"], st["hostname"], st["twincat"]), ("5.1.2.3.1.1", "CX-FAKE", "3.1.4024"))
        self.assertEqual(self.links[0].args, ("10.0.0.9", "5.1.2.3.1.1", 1000))   # Net ID from the UDP info
        wait_for(lambda: self.c.status()["plcName"] == "Demo PLC", msg="device info")
        self.assertEqual((st["systemStateName"], st["plcStateName"]), ("RUN", "RUN"))
        self.assertEqual(st["localNetId"], "127.0.0.1.1.1")

    def test_configured_net_id_needs_no_udp(self):
        self.discover_fails = True
        self.start(amsNetId="1.2.3.4.1.1")
        self.assertEqual(self.links[0].args[1], "1.2.3.4.1.1")

    def test_unknown_net_id(self):
        self.discover_fails = True
        self.nodes.configure([])
        self.c = Controller(self.cfg, ADS, self.nodes, self.factory, self.discover)
        self.c.start()
        st = wait_for(lambda: self.c.status()["state"] == "offline" and self.c.status(), msg="offline")
        self.assertIn("AMS Net ID unknown", st["error"])
        self.assertEqual(self.links, [])

    def test_nodes_are_polled(self):
        self.start([node("counter", "MAIN.nCounter", "uint32"), node("real", "GVL_Demo.fReal", "float32", True),
                    node("arr", "GVL_Demo.aInt", "array-of-int16"), node("name", "GVL_Demo.aAxis[2].sName", "string"),
                    node("gone", "MAIN.nope", "int16")])
        wait_for(lambda: self.value("arr"), msg="values")
        self.assertEqual(self.value("arr"), tuple(range(10, 110, 10)))
        self.assertEqual(self.value("real"), 0.10000000149011612)
        self.assertEqual(self.value("name"), "Axis 2")
        first = self.value("counter")
        wait_for(lambda: self.value("counter") != first, msg="counter changes")
        self.assertIn("not in the PLC", self.nodes.get("cx", "gone").error)
        self.assertEqual(self.nodes.get("cx", "arr").count, 10)
        # one sum read per cycle for all nodes (+ the symbol version), not one request per node
        before = self.links[-1].requests
        time.sleep(0.25)
        per_cycle = (self.links[-1].requests - before) / max(1, 0.25 / 0.02)
        self.assertLess(per_cycle, 2.0)

    def test_write_through_a_node(self):
        self.start([node("real", "GVL_Demo.fReal", "float32", True), node("text", "GVL_Demo.sText", "string", True)])
        wait_for(lambda: self.value("real") is not None, msg="value")
        self.assertEqual(self.nodes.write("cx/real", 2.5), 2.5)
        self.assertEqual(self.plc.writes[-1], ("GVL_Demo.fReal", b"\x00\x00\x20\x40"))
        wait_for(lambda: self.value("real") == 2.5, msg="new value polled")
        with self.assertRaises(at.AdsTypeError):
            self.nodes.write("cx/text", "x" * 81)                       # STRING(80)
        with self.assertRaises(NodeWriteError):
            self.nodes.write("cx/real", "2.5")                          # the node's type only
        with self.assertRaises(at.AdsTypeError):
            self.c.write_symbol("GVL_Demo.fReal", 1, vtype="float64")   # the PLC variable is a REAL

    def test_online_change_with_another_type(self):
        """A download / online change: new symbol version -> tables loaded again; a node whose variable has
        another type now reports it instead of converting."""
        self.start([node("int", "GVL_Demo.nInt", "int16"), node("real", "GVL_Demo.fReal", "float32")])
        wait_for(lambda: self.value("int") is not None, msg="value")
        syms, dts, layout = demo_tables()
        io, _ = layout["GVL_Demo.nInt"]
        changed = syms.replace(sym_entry("GVL_Demo.nInt", io, 2, 2, "INT"), sym_entry("GVL_Demo.nInt", io, 4, 3, "DINT"))
        self.assertNotEqual(changed, syms)
        self.plc.online_change(changed, dts)
        e = wait_for(lambda: (lambda e: not e.ok and e)(self.nodes.get("cx", "int")), msg="type error")
        self.assertIn("int32", e.error)
        self.assertTrue(self.nodes.get("cx", "real").ok)
        wait_for(lambda: self.c.status()["symbolVersion"] == self.plc.version, msg="new version")

    def test_connection_lost_and_back(self):
        self.start([node("int", "GVL_Demo.nInt", "int16")])
        wait_for(lambda: self.value("int") is not None, msg="value")
        self.plc.down = True
        wait_for(lambda: self.c.status()["state"] in ("offline", "error"), msg="lost")
        with self.assertRaises(NodeUnavailable) as ctx:
            self.nodes.read("cx/int")
        self.assertEqual(ctx.exception.reason, "offline")
        with self.assertRaises(ControllerUnavailable):
            self.c.browse("")
        self.plc.down = False
        wait_for(lambda: self.c.status()["state"] == "connected", msg="back")
        wait_for(lambda: self.nodes.get("cx", "int").ok, msg="values again")
        self.assertGreaterEqual(len(self.links), 2)                     # a new link (route) after the loss

    def test_no_route_on_the_controller(self):
        """TCP opens but TwinCAT doesn't answer (no route for this device): the error names the route it needs."""
        self.plc.no_route = True
        self.nodes.configure([])
        self.c = Controller(self.cfg, ADS, self.nodes, self.factory, self.discover)
        self.c.start()
        st = wait_for(lambda: (lambda s: s["state"] == "error" and s)(self.c.status()), msg="error")
        self.assertIn("probably no route on the controller for AMS Net ID 127.0.0.1.1.1 at " + st["localIp"], st["error"])
        self.plc.no_route = False
        wait_for(lambda: self.c.status()["state"] == "connected", msg="connected once the route is there")

    def test_plc_runtime_gone(self):
        """TwinCAT in CONFIG mode (as the CX was on 2026-11-23): system port answers, the PLC port doesn't."""
        self.start([node("int", "GVL_Demo.nInt", "int16")])
        self.plc.system_state = at.ADS_STATE_CONFIG
        st = wait_for(lambda: (lambda s: s["plcError"] and s)(self.c.status()), msg="plc error")
        self.assertEqual((st["state"], st["systemStateName"], st["plcState"]), ("connected", "CONFIG", None))
        self.assertIn("target port not found", st["plcError"])
        wait_for(lambda: not self.nodes.get("cx", "int").ok, msg="node error")
        with self.assertRaises(NodeUnavailable) as ctx:
            self.nodes.read("cx/int")
        self.assertEqual(ctx.exception.reason, "error")
        self.plc.system_state = at.ADS_STATE_RUN
        wait_for(lambda: self.nodes.get("cx", "int").ok, msg="back in RUN")

    def test_disabled(self):
        self.start([node("int", "GVL_Demo.nInt", "int16")])
        self.c.update(dict(self.cfg, enabled=False), ADS)
        self.c._wake.set()
        wait_for(lambda: self.c.status()["state"] == "disabled", msg="disabled")
        self.assertFalse(self.nodes.get("cx", "int").ok)
        self.assertFalse(self.links[-1].is_open)

    def test_browser_functions(self):
        self.start()
        rows = self.c.browse("MAIN")["children"]
        self.assertEqual([r["name"] for r in rows], ["bToggle", "fbCounter", "fSine", "nCounter", "pValue"])
        self.assertEqual(rows[4]["kind"], "pointer")
        self.assertNotIn("value", rows[4])
        d = self.c.details("MAIN.pValue")
        self.assertIn("pointers", d["valueError"])
        d = self.c.details("GVL_Demo")
        self.assertEqual((d["kind"], d["leafCount"]), ("folder", 44))   # 21 + 2 arrays + 15 + 5 members + nConst
        d = self.c.details("GVL_Demo.eState")
        self.assertEqual((d["value"], d["enum"][1]), (1, ["Running", 1]))
        self.assertTrue(d["writable"])
        self.assertFalse(self.c.details("GVL_Demo.nConst")["writable"])
        self.assertEqual(self.c.write_symbol("GVL_Demo.eState", "error", from_input=True)["value"], 2)
        with self.assertRaises(at.AdsTypeError):
            self.c.write_symbol("GVL_Demo.nConst", 1)
        plan = self.c.plan_nodes("GVL_Demo.aGrid", writable=True)
        self.assertEqual((len(plan), plan[0]["address"], plan[0]["type"]), (1, "GVL_Demo/aGrid", "array-of-float32"))
        plan = self.c.plan_nodes("MAIN.fbCounter", address="counter")
        self.assertEqual([p["address"] for p in plan], ["counter/nCount", "counter/bUp", "counter/tCycle"])
        self.assertEqual(plan[2]["unit"], "ms")
        plan = self.c.plan_nodes("GVL_Demo", writable=True)
        self.assertFalse(next(p for p in plan if p["symbol"] == "GVL_Demo.nConst")["writable"])
        with self.assertRaises(ValueError):
            self.c.plan_nodes("MAIN.pValue")
        with self.assertRaises(ValueError):
            self.c.plan_nodes("GVL_Demo.nInt", address="a b")


class AdsLibLogLevelTest(unittest.TestCase):
    def test_level(self):
        from app import ads_link
        self.assertEqual(ads_link.ADSLIB_LEVELS["INFO"], 2)        # no "connection closed by remote" at INFO
        self.assertEqual(ads_link.ADSLIB_LEVELS["DEBUG"], 0)
        if ads_link.available() is None:                          # pyads in .dev/pylib: the symbol is there
            import ctypes
            self.assertTrue(ads_link.set_adslib_log_level("ERROR"))
            lib = ads_link._load().pyads_ex._adsDLL
            self.assertEqual(ctypes.c_size_t.in_dll(lib, "_ZN6Logger8logLevelE").value, 3)
            ads_link.set_adslib_log_level("INFO")
        else:
            self.assertFalse(ads_link.set_adslib_log_level("INFO"))


class ManagerTest(unittest.TestCase):
    def test_apply_add_change_remove(self):
        plc = FakePlc()
        made = []

        def factory(host, net_id, t):
            made.append(host)
            return FakeLink(plc)
        nodes = NodeMap()
        m = ControllerManager(nodes, link_factory=factory, discover=lambda host: dict(FAKE_INFO))
        with open(os.path.join(HERE, "..", "defaults", "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        a = {"name": "a", "host": "h1", "amsNetId": "", "amsPort": 851, "pollIntervalMs": 20, "enabled": True}
        try:
            settings["controllers"] = [a]
            settings["nodes"] = [node("x", "GVL_Demo.nInt", "int16", controller="a")]
            m.apply(settings)
            wait_for(lambda: nodes.get("a", "x").ok, msg="node value")
            first = m.get("a")
            settings["controllers"] = [dict(a, pollIntervalMs=50)]
            m.apply(settings)
            self.assertIs(m.get("a"), first)                            # no new connection for a poll interval
            settings["controllers"] = [dict(a, host="h2")]
            m.apply(settings)
            self.assertIsNot(m.get("a"), first)                         # a new host: a new connection
            wait_for(lambda: "h2" in made, msg="reconnect")
            summary = wait_for(lambda: (lambda s: s[0]["state"] == "connected" and s)(m.summary()), msg="summary")
            self.assertEqual(set(summary[0]), {"name", "host", "netId", "state", "systemStateName", "plcStateName",
                                               "nodes", "nodesOk", "error"})
            settings["controllers"], settings["nodes"] = [], []
            m.apply(settings)
            self.assertEqual((m.snapshot(), nodes.counts()), ([], (0, 0)))
        finally:
            m.stop()


class NodeMapTest(unittest.TestCase):
    def test_tree_and_values(self):
        nodes = NodeMap()
        nodes.configure([node("MAIN/a", "MAIN.a", "int16"), node("MAIN/b", "MAIN.b", "bool8", True),
                         node("c", "GVL.c", "string", controller="cy")])
        self.assertEqual(nodes.browse(""), ["cx", "cy"])
        self.assertEqual(nodes.browse("cx/MAIN"), ["a", "b"])
        self.assertIsNone(nodes.browse("cx/nope"))
        self.assertEqual(nodes.describe("cx"), ("folder",))
        self.assertEqual(nodes.describe("cx/MAIN/b")[:3], ("variable", "bool8", True))
        with self.assertRaises(NodeUnavailable):
            nodes.read("cx/MAIN/a")                                       # no value yet
        nodes.update("cx", "MAIN/a", 5)
        self.assertEqual(nodes.read("cx/MAIN/a"), ("int16", 5))
        nodes.configure([node("MAIN/a", "MAIN.a", "int16")])              # kept: same symbol and type
        self.assertEqual(nodes.read("cx/MAIN/a"), ("int16", 5))
        nodes.configure([node("MAIN/a", "MAIN.a", "int32")])              # another type: no old value
        with self.assertRaises(NodeUnavailable):
            nodes.read("cx/MAIN/a")
        with self.assertRaises(NodeWriteError) as ctx:
            nodes.write("cx/MAIN/a", 1)
        self.assertEqual(ctx.exception.reason, "readonly")
        with self.assertRaises(NodeWriteError):
            nodes.read("cx/zzz")
        self.assertEqual(nodes.counts(), (1, 1))
        self.assertEqual(nodes.for_symbol("cx", "main.A"), ["MAIN/a"])

    def test_addresses(self):
        self.assertEqual(default_address("POU_Counter.iCounter"), "POU_Counter/iCounter")
        self.assertEqual(default_address("GVL.aAxis[1].fPos"), "GVL/aAxis/1/fPos")
        self.assertEqual(default_address("GVL.aGrid[1, -2]"), "GVL/aGrid/1_-2")
        self.assertEqual(default_address("Object1 (Module).In"), "Object1__Module_/In")
        self.assertTrue(valid_address("a/b-c/_1"))
        for bad in ("", "a//b", "/a", "a b", "x" * 65, "/".join("a" * 17)):
            self.assertFalse(valid_address(bad), bad)


class ValidatorTest(unittest.TestCase):
    def settings(self, controllers=(), nodes=(), local=""):
        return {"ads": {"localNetId": local, "timeoutMs": 1000}, "controllers": list(controllers),
                "nodes": list(nodes)}

    def ctrl(self, name, net=""):
        return {"name": name, "host": "h", "amsNetId": net, "amsPort": 851, "pollIntervalMs": 100, "enabled": True}

    def test_ok(self):
        self.assertEqual(validate_settings(self.settings([self.ctrl("cx", "1.2.3.4.1.1")],
                                                         [node("a/b", "A.b", "int16")])), [])

    def test_errors(self):
        errors = validate_settings(self.settings(
            [self.ctrl("cx", "1.2.3.4.1.1"), self.ctrl("CX"), self.ctrl("cy", "1.2.3.4.1.1"),
             self.ctrl("cz", "1.2.3.4.1.300")],
            [node("a", "A", "int16"), node("a/b", "A.b", "int16"), node("A", "A2", "int16"),
             node("x y", "X", "int16"), node("q", "Q", "int16", controller="nope")], local="1.1.1.1.1.999"))
        text = "\n".join(errors)
        for part in ("ads.localNetId: each part", "controllers[1].name: differs only in case",
                     "controllers[2].amsNetId: already used by cx", "controllers[3].amsNetId: each part",
                     "nodes[2].address: A is already a node", "nodes[3].address: letters",
                     "nodes[4].controller: no controller nope", "cx/a is a node, so it can't be a folder of cx/a/b"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
