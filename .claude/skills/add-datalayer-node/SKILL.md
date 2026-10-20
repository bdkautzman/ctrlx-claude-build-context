---
name: add-datalayer-node
description: Add or change a ctrlX Data Layer node provided by an app (address, type, read/write, validation). Use when the user wants to expose or accept a value in the ctrlX Data Layer.
---

# Add a Data Layer node

Read `docs/ctrlx/datalayer-python.md` first.

1. Define it in `app/datalayer_nodes.py::build_node_specs()`:
   `NodeSpec("<group>/<name>", "<bool8|int64|uint64|float64|string>", initial, writable=..., minimum=..., maximum=...,
   max_length=..., description="...", unit="...", on_write=callback)`. Address: lowercase, `-` between words,
   below the app root (= snap name).
2. Values set by the app: `registry.set_internal(name, value)` (e.g. from a worker thread or settings listener).
   Writes from outside: validated by `NodeRegistry.write`; side effects in `on_write` (fast, non-blocking,
   **no disk writes**).
3. Other types (int32, float32, arrays, flatbuffers): extend `SUPPORTED_TYPES`/`coerce` and verify the
   Variant setter/getter names in `reference/ctrlxdatalayer-api-3.6.0/variant.py`; extend the fake in
   `tests/fakes/ctrlxdatalayer/variant.py` the same way.
4. Tests: add cases to `tests/test_web_and_nodes.py` (registry) and, for glue changes, `tests/test_datalayer_glue.py`.
5. The node appears automatically in the web UI values table and under `/api/v1/values`.
6. `tools/check.sh apps/<app>`; on a device verify with `tools/ctrlx.py dl-read <snap>/<group>/<name>`.
