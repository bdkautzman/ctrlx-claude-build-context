---
name: add-datalayer-node
description: Add or change a ctrlX Data Layer node provided by an app (address, type, read/write, validation). Use when the user wants to expose or accept a value in the ctrlX Data Layer.
---

# Add a Data Layer node

Read `docs/ctrlx/datalayer-python.md` first.

1. Define it in `src/app/datalayer_nodes.py::build_node_specs()`:
   `NodeSpec("<group>/<name>", "<type of the PLC variable: bool8, int8..uint64, float32, float64, string, array-of-...>", initial, writable=..., minimum=..., maximum=...,
   max_length=..., max_items=..., description="...", unit="...", on_write=callback)`. **Type = the type of the PLC
   variable that writes or reads it** (REAL = float32, DINT = int32, ARRAY OF INT = array-of-int16; table in
   `docs/ctrlx/datalayer-python.md` "Node types for PLC variables"): writes of any other type are refused, nothing is
   converted (user's choice 2026-11-08). Ask the user for the PLC type when it isn't clear. Address: lowercase, `-` between words,
   below the app root (= snap name). A JSON document (list, summary, a record from a PLC): `"string"` with
   `json=True` - `set_internal(name, obj)` stores compact JSON, writes must be valid JSON, `on_write` gets the object.
   Web API roles: `write_role="operate"` (default; `"configure"` for nodes that change the setup), `view_role="view"`
   (default; `"configure"` hides it from the others in `GET /values`). Data Layer clients follow the ctrlX user's
   Data Layer permissions instead.
   A PLC starts an action in the app (trigger, start, measure): `PlcCommand(path, run, params=[...])` and
   `*cmd.specs()` - Execute / Busy / Done / Error, the kit's standard handshake (`docs/ctrlx/datalayer-python.md`
   "PLC commands"); no own trigger/request-id scheme. The worker behind it: `Commands` + `bind()`.
2. Values set by the app: `registry.set_internal(name, value)` (e.g. from a worker thread or settings listener).
   Writes from outside: validated by `NodeRegistry.write`; side effects in `on_write` (fast, non-blocking,
   **no disk writes**).
3. Types the template doesn't have (timestamp, flatbuffers): extend `SUPPORTED_TYPES`/`coerce` and verify the
   Variant setter/getter names in `reference/ctrlxdatalayer-api-3.6.0/variant.py`; extend the fake in
   `tests/fakes/ctrlxdatalayer/variant.py` the same way. Never accept a second type for a node.
4. Tests: add cases to `tests/test_web_and_nodes.py` (registry) and, for glue changes, `tests/test_datalayer_glue.py`.
5. The node appears automatically in the web UI values table and under `/api/v1/values`.
   Reading or writing nodes of others (PLC variables, system/info): `DataLayerClient` from
   `DataLayerService(..., on_client=...)`, `docs/ctrlx/datalayer-python.md` "Client".
6. `tools/check.sh apps/<app>`; on a device verify with `tools/ctrlx.py dl-read <snap>/<group>/<name>`.
