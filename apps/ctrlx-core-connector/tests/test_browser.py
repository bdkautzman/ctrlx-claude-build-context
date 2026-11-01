"""Data Layer browser and type conversion tests against an in-memory Data Layer (no ctrlX needed)."""

import json
import math
import unittest

from app import dltypes
from app.browser import Browser, clean_path, describe
from app.monitor import DataLayerError
from tests.fake_adapter import FakeAdapter

R = "devices/remotes/cm-a/"


def tree(base=""):
    return {
        base + "framework/metrics/system/cpu-utilisation-percent": {"nodeClass": "Variable", "type": "float64", "value": 2.5, "unit": "%"},
        base + "plant/speed": {"nodeClass": "Variable", "type": "int32", "value": 7, "write": True, "description": "Speed"},
        base + "plant/flags": {"nodeClass": "Variable", "type": "arbool8", "value": [True, False], "write": True},
        base + "plant/label": {"nodeClass": "Variable", "type": "string", "value": "x" * 1000, "write": True},
        base + "plant/settings": {"nodeClass": "Variable", "readType": "types/plant/settings", "value": {"a": 1}, "write": True},
        base + "plant/info": {"nodeClass": "Resource", "readType": "types/plant/info", "value": {"big": "object"}},
        base + "types/plant/settings": {"nodeClass": "Type", "type": None},
    }


class TypesTest(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(dltypes.coerce("int32", "42"), 42)
        self.assertEqual(dltypes.coerce("int16", 3.0), 3)
        self.assertEqual(dltypes.coerce("uint8", "0x1F"), 31)
        self.assertEqual(dltypes.coerce("int32", "007"), 7)
        self.assertEqual(dltypes.coerce("uint64", str(2 ** 64 - 1)), 2 ** 64 - 1)  # exact, beyond JS numbers
        self.assertIs(dltypes.coerce("bool8", "true"), True)
        self.assertIs(dltypes.coerce("bool8", False), False)
        self.assertEqual(dltypes.coerce("float32", "1.5"), 1.5)
        self.assertEqual(dltypes.coerce("double", 2), 2.0)            # KVD alias
        self.assertEqual(dltypes.coerce("string", "a b"), "a b")
        self.assertEqual(dltypes.coerce("arint32", "1, 2,3"), [1, 2, 3])
        self.assertEqual(dltypes.coerce("arint32", "[4, 5]"), [4, 5])
        self.assertEqual(dltypes.coerce("arstring", '["a,b", "c"]'), ["a,b", "c"])
        self.assertEqual(dltypes.coerce("arfloat64", ""), [])
        for vtype, raw in [("int8", 128), ("uint16", -1), ("int32", "1.5"), ("int32", True), ("bool8", "yes"),
                           ("float64", "nan"), ("float32", 1e39), ("arstring", "a, b"), ("arint8", "[300]"),
                           ("string", {"a": 1}), ("types/x", 1)]:
            with self.assertRaises(ValueError, msg=(vtype, raw)):
                dltypes.coerce(vtype, raw)

    def test_json_safe_and_references(self):
        self.assertEqual(dltypes.json_safe([math.nan, 1.0, b"\x01"]), ["nan", 1.0, "01"])
        json.dumps(dltypes.json_safe({"x": math.inf}), allow_nan=False)
        self.assertEqual(dltypes.type_from_reference("types/datalayer/int32"), "int32")
        self.assertEqual(dltypes.type_from_reference("types/datalayer/double"), "float64")
        self.assertEqual(dltypes.type_from_reference("types/datalayer/array-of-int16"), "arint16")   # ctrlX OS 4.6
        self.assertEqual(dltypes.type_from_reference("types/datalayer/array-of-double"), "arfloat64")
        self.assertEqual(dltypes.type_from_reference("types/system/systeminfo"), "types/system/systeminfo")
        # a remote's node browsed from this device's root (ctrlX OS 3.6 target, 2026-10-31)
        self.assertEqual(dltypes.type_from_reference("devices/remotes/iot-core/types/datalayer/uint32"), "uint32")
        self.assertEqual(dltypes.type_from_reference("devices/remotes/x/types/plant/info"), "devices/remotes/x/types/plant/info")

    def test_clean_path(self):
        self.assertEqual(clean_path("/a/b/"), "a/b")
        self.assertEqual(clean_path(""), "")
        for bad in ("a//b", "a\nb", None, "x" * 2000):
            with self.assertRaises(ValueError):
                clean_path(bad)

    def test_describe_makes_references_relative(self):
        meta = {"nodeClass": "Variable", "operations": {"read": True},
                "references": [{"type": "readType", "targetAddress": R + "types/datalayer/int32"},
                               {"type": "createType", "targetAddress": R + "types/x"}]}
        info = describe(meta, R)
        self.assertEqual(info["readType"], "int32")
        self.assertEqual(info["references"][1]["targetAddress"], "types/x")
        self.assertEqual(info["operations"], {"read": True, "write": False, "create": False, "delete": False, "browse": False})


class BrowserTest(unittest.TestCase):
    def setUp(self):
        self.dl = FakeAdapter(dict(tree(""), **tree(R)))
        self.br = Browser(lambda: self.dl)

    def test_children_root_and_folder(self):
        for base, extra in (("", ["devices"]), (R, [])):   # this device's root also has devices/remotes/...
            root = self.br.children(base, "")
            self.assertEqual([c["name"] for c in root["children"]], extra + ["framework", "plant", "types"])
            self.assertTrue(all(c["hasChildren"] and c["nodeClass"] == "Folder" for c in root["children"]))
            plant = {c["name"]: c for c in self.br.children(base, "plant")["children"]}
            self.assertEqual(plant["speed"]["path"], "plant/speed")
            self.assertEqual((plant["speed"]["type"], plant["speed"]["value"]), ("int32", 7))
            self.assertTrue(plant["speed"]["operations"]["write"])
            self.assertFalse(plant["speed"]["hasChildren"])
            self.assertEqual((plant["flags"]["type"], plant["flags"]["value"]), ("arbool8", [True, False]))
            self.assertTrue(plant["label"]["valueCut"])
            self.assertLess(len(plant["label"]["value"]), 400)
            # objects are not read in the tree (only their type is shown)
            self.assertNotIn("value", plant["settings"])
            self.assertEqual(plant["settings"]["type"], "types/plant/settings")
            self.assertNotIn("value", plant["info"])

    def test_bulk_calls(self):
        self.dl.calls.clear()
        self.br.children(R, "plant")
        self.assertEqual([c for c in self.dl.calls if c[0] == "metadata_many"], [("metadata_many", 5)])

    def test_node_details(self):
        speed = self.br.node(R, "plant/speed")
        self.assertEqual((speed["value"], speed["valueType"], speed["type"]), (7, "int32", "int32"))
        self.assertEqual(speed["metadata"]["references"][0]["targetAddress"], "types/datalayer/int32")
        obj = self.br.node(R, "plant/settings")
        self.assertEqual((obj["value"], obj["valueType"]), ({"a": 1}, "object"))
        root = self.br.node(R, "")
        self.assertEqual((root["metadata"]["nodeClass"], root["childCount"]), ("Folder", 3))
        self.assertNotIn("value", root)
        typ = self.br.node(R, "types/plant/settings")
        self.assertNotIn("value", typ)
        with self.assertRaises(DataLayerError) as ctx:
            self.br.node(R, "nope")
        self.assertEqual(ctx.exception.result, "INVALID_ADDRESS")

    def test_node_without_value(self):
        # KVD's sample collection: readable, but the value is empty - details without a value, no error
        self.dl.nodes[R + "plant/collection"] = {"nodeClass": "Collection", "empty": True}
        info = self.br.node(R, "plant/collection")
        self.assertEqual(info["metadata"]["nodeClass"], "Collection")
        self.assertNotIn("value", info)
        self.assertNotIn("valueError", info)
        self.assertEqual(self.br.value(R, "plant/collection"), {})

    def test_write(self):
        out = self.br.write(R, "plant/speed", {"value": "12"})
        self.assertEqual(out["value"], 12)
        self.assertEqual(json.loads(self.dl.writes[-1][1]), {"type": "int32", "value": 12})
        out = self.br.write(R, "plant/flags", {"value": "[false, true]"})
        self.assertEqual(out["value"], [False, True])
        out = self.br.write(R, "plant/settings", {"value": '{"a": 2}'})   # flatbuffers: plain JSON object
        self.assertEqual(json.loads(self.dl.writes[-1][1]), {"a": 2})
        self.assertEqual(out["value"], {"a": 2})
        with self.assertRaises(ValueError):
            self.br.write(R, "plant/speed", {"value": "fast"})
        with self.assertRaises(ValueError):
            self.br.write(R, "plant/settings", {"value": "not json"})
        with self.assertRaises(DataLayerError) as ctx:
            self.br.write(R, "framework/metrics/system/cpu-utilisation-percent", {"value": 1})
        self.assertEqual(ctx.exception.result, "PERMISSION_DENIED")

    def test_values_refresh(self):
        self.dl.nodes[R + "plant/speed"]["value"] = 99
        values = self.br.values(R, ["plant/speed", "plant/missing"])
        self.assertEqual(values[0]["value"], 99)
        self.assertEqual(values[1]["valueError"], "INVALID_ADDRESS")

    def test_not_connected(self):
        br = Browser(lambda: None)
        with self.assertRaises(DataLayerError) as ctx:
            br.children("", "")
        self.assertEqual(ctx.exception.result, "CLIENT_NOT_CONNECTED")


if __name__ == "__main__":
    unittest.main()
