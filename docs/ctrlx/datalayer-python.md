# ctrlX Data Layer from Python

Exact API (3.6.0, extracted from the SDK's Doxygen): `reference/ctrlxdatalayer-api-3.6.0/`
(`system.py`, `factory.py`, `provider.py`, `provider_node.py`, `client.py`, `variant.py`,
`metadata_utils.py`, `subscription_*.py`, `bulk.py`). **Grep there before using any call;
do not guess signatures.** SDK samples: `reference/sdk-4.6.0-samples-python/datalayer.*`.

## Template structure

* `src/app/datalayer_nodes.py` – **edit here**: `build_node_specs()` returns `NodeSpec`s
  (`name`, `vtype` (every scalar type and `array-of-<type>`, see "Node types for PLC variables"), `initial`,
  `writable`, `minimum`, `maximum`, `max_length`, `max_items`, `description`, `unit`, `on_write`, `json`,
  `view_role`, `write_role`).
  `NodeRegistry` holds values, validates writes. Pure Python → unit-testable.
* **JSON nodes** (template 0.2.0): `NodeSpec("apps/summary", "string", [], json=True)` - every app of the kit
  publishes lists and summaries as JSON strings. `registry.set_internal(name, obj)` stores the object as compact
  JSON (UTF-8, no spaces), `registry.get_json(name)` parses it; a writable one rejects text that isn't JSON
  (`DL_INVALID_VALUE`) and its `on_write` gets the parsed object. `max_length` counts the JSON text.
  Example: `settings/active`.
* `view_role` / `write_role` ("view" | "operate" | "configure", defaults "view" / "operate"): who sees / writes the
  node through the app's web API (`GET/PUT /api/v1/values`). Data Layer clients (PLC, REST) are NOT affected:
  there the ctrlX user's Data Layer permissions apply.
* `src/app/datalayer_service.py` – glue to `ctrlxdatalayer` (lazy import), provider lifecycle,
  automatic reconnect every 5 s, heartbeat. Rarely needs changes.
* `tests/fakes/ctrlxdatalayer` – minimal fake used by `tests/test_datalayer_glue.py`.

## Lifecycle (provider)

```python
with ctrlxdatalayer.system.System("") as system:        # "" = automatic IPC path
    system.start(False)                                   # False: don't start a broker
    provider = system.factory().create_provider("ipc://") # in the snap
    with provider:                                        # provider.close() on exit
        assert provider.start() == Result.OK
        provider.register_node("<root>/x", provider_node) # keep provider_node referenced!
        while provider.is_connected(): ...
        provider.unregister_node("<root>/x"); provider.stop()
    system.stop(False)   # returns False/blocks while clients/providers still exist
```

Client (reading/writing other nodes): the template's `DataLayerClient` (template 0.4.0, section "Client" below).

## Callbacks (`ProviderNodeCallbacks(on_create, on_remove, on_browse, on_read, on_write, on_metadata)`)

* Signatures: `on_read(userdata, address, data, cb)`, `on_write(userdata, address, data, cb)`,
  `on_create(userdata, address, data, cb)`, `on_remove/on_browse/on_metadata(userdata, address, cb)`.
* Call `cb(Result, Variant_or_None)` **exactly once**, also on errors. Runs in a Data Layer
  thread → protect shared state with a lock, return quickly (no blocking I/O).
* `on_write`: check `data.get_type()` against the node type (→ `Result.TYPE_MISMATCH`),
  validate range (→ `Result.INVALID_VALUE`), read-only → `Result.PERMISSION_DENIED`.
* Template: `NodeSpec(on_write=fn)` runs **before** the value is stored (template ≥ 0.1.5); raise
  `NodeWriteError("invalid", msg)` there to reject a value that type/range checks can't judge (e.g.
  JSON content) → the writer gets `DL_INVALID_VALUE` and the node keeps its old value.
* Metadata: `MetadataBuilder.create_metadata(name, description, unit, description_url,
  NodeClass.NodeClass.Variable, read_allowed, write_allowed, create_allowed, delete_allowed,
  browse_allowed, type_path="types/datalayer/<type>")` (import `NodeClass` from `comm.datalayer`).
* Variant: `Variant()` as context manager; `set_<type>()`/`get_<type>()`; `VariantType.<TYPE>`.
  Safe to close the Variant right after `cb()` (SDK samples do `with Variant() as v: cb(OK, v)`).

## Addresses & types

* Root = snap name (`<name>/…`), lowercase, `-` between words, groups like `status/`, `values/`, `settings/`.
* Type addresses: `types/datalayer/bool8|int8..int64|uint8..uint64|float32|float64|string|array-of-<type>|…`
  (on the X3, ctrlX OS 4.6: also `double`, `array-of-double`, `timestamp`, `array-of-timestamp`, `raw`).
  Complex data: flatbuffers (`register_type(address, bfbs_path)`, see sample `datalayer.provider`)
  or a JSON string node (simple, used by community apps; consumers must parse it).

### Node types for PLC variables (template 0.4.0, user's choice 2026-11-08)

A write must come with **exactly** the node's type, and nothing is converted. On the X3 (ctrlX OS 4.6, 2026-11-07),
the Data Layer handed REST writes of float32, int16, int32, int64, uint8, uint32 and bool8 to the template's
float64 node unchanged, and the template answered `DL_TYPE_MISMATCH`; a bool8 node refused 0 and 1. A PLC writes
with its variable's type, so **give each node the type of the PLC variable that writes or reads it**. The
template accepts no other type on purpose: a conversion (REAL into a float64 node, DINT into an int64 node) would be
a place where values change on the way. The user ruled that out.

| IEC 61131-3 (ctrlX PLC) | Node type (`vtype`) | REST `type` | Range / note |
|---|---|---|---|
| BOOL | `bool8` | `bool8` | true / false (0 and 1 are refused) |
| SINT | `int8` | `int8` | -128..127 |
| USINT, BYTE | `uint8` | `uint8` | 0..255 |
| INT | `int16` | `int16` | -32768..32767 |
| UINT, WORD | `uint16` | `uint16` | 0..65535 |
| DINT | `int32` | `int32` | -2^31..2^31-1 |
| UDINT, DWORD | `uint32` | `uint32` | 0..2^32-1 |
| LINT | `int64` | `int64` | -2^63..2^63-1 |
| ULINT, LWORD | `uint64` | `uint64` | 0..2^64-1 |
| REAL | `float32` | `float32` (reads say `float`) | finite; the node holds a float32 |
| LREAL | `float64` | `float64` (reads say `double`) | finite |
| STRING, WSTRING | `string` | `string` | UTF-8; `max_length` |
| ARRAY [..] OF \<type\> | `array-of-<type>` | `ar<type>`, e.g. `arint16`; float32: `arfloat32` = `arfloat` (reads say `arfloat`) | `max_items`; `minimum`/`maximum`/`max_length` per element |
| TIME, DATE, DT, TOD, STRUCT | not in the template | | a STRUCT as separate nodes or a JSON node (`json=True`); time values as int64 (ms or ns, say which in `unit`) |

* The mapping is the one of `reference/example-apps/datalayer.ab` (CIP tags → Data Layer). **Not verified with a
  ctrlX PLC yet** (no PLC app on the X3). Verified on the X3 (template 0.4.0, 2026-11-08): the float32 array node
  `values/limits` takes REST writes as `arfloat32` and `arfloat` and refuses `arfloat64` / `ardouble`
  (`DL_TYPE_MISMATCH`); through `DataLayerClient` float32 into a float64 node and int32 into float64 are refused too.
  A scalar float32 node was not on the device yet (REST spec: `float32`, reads `float`).
* **float32**: the registry stores the float32 that a REAL holds. A value set from Python or the web UI is stored
  as the nearest float32 once (0.1 → 0.10000000149011612); a value from the Data Layer is already one and stays
  exact. Registry, web API and Data Layer always show the same value. The template page shows float32 values with
  the fewest digits that give the same float32 (0.1), like PLC tools do.
* **Arrays** are tuples in Python (`registry.get()`), JSON arrays in the web API and REST.
* **int64 / uint64 in the browser**: JavaScript numbers lose digits above 2^53 - the web UI can show such values
  rounded; the Data Layer value is exact.
* REST writes need the type name: `tools/ctrlx.py dl-write <snap>/values/limits '[1.5, 2]' --type arfloat32`.
* Node names in Data Layer UI: Settings → Data Layer → `<name>/`.

## PLC commands: Execute / Busy / Done / Error (template 0.3.0, the kit's standard, user's choice 2026-11-07)

When a PLC starts something in the app (a measurement, a read, a scan, a job), use `PlcCommand` from
`src/app/datalayer_nodes.py` - before 0.3.0 four apps did this four different ways (a string that starts, an abort
bool, a trigger the app resets, a request id). It is the PLCopen function-block pattern PLC programmers know:

| Node `<path>/...` | Type | | Meaning |
|---|---|---|---|
| `execute` | bool8 | W | a rising edge starts; a start while busy is rejected (`DL_INVALID_VALUE`); true written again does nothing (cyclic writes are fine) |
| `busy` | bool8 | R | true while it runs |
| `done` | bool8 | R | finished without error - until `execute` is written false |
| `error` / `error-text` | bool8 / string | R | failed, and why - until `execute` is written false |
| `<param>` | any | W | parameters, read at the rising edge (`params=[NodeSpec("target", "float64", 0.0, ...)]`) |

PLC sequence: write the parameters → `execute := TRUE` → wait for `done OR error` → read the results →
`execute := FALSE`. When `execute` goes false while it is still busy, `done`/`error` show the result until the
next start (a Data Layer node has no "one PLC cycle", unlike a PLCopen function block).

```python
def run_scan(params, registry):             # its own thread, one at a time
    result = scanner.scan(params["profile"])    # may take seconds
    registry.set_internal("last/summary", result)   # results first: complete when done goes true
    # raise CommandError("unknown profile") -> error = true, error-text = that text

scan = PlcCommand("scan/start", run_scan, description="start a scan",
                  params=[NodeSpec("profile", "string", "quick", max_length=32)])
return [..., *scan.specs()]                 # in build_node_specs
```

* The web UI writes the same nodes (operate role: `write_role`), so the page and a PLC use one path.
* The action needs the app's worker, which needs the registry: `Commands` (below) binds it after both exist.
* Example in the template: `commands/set-setpoint` (copies `target` into `values/setpoint` after 1 s; error while
  `values/enabled` is false), unit tests `tests/test_web_and_nodes.py` `PlcCommandTest`, UI step in `tests/ui_scenario.py`.
* Not tried with a real ctrlX PLC yet (only REST and the web UI); a PLC writes `execute` as BOOL = bool8.

## Nodes that call the app: `Commands` (template 0.3.0)

The nodes are built before the object that does the work (it needs `datalayer.registry` to publish its results).
`main.py`: `commands = Commands()` → `DataLayerService(settings, version, commands)` (passed on to
`build_node_specs(settings, version, commands)`) → `worker = Worker(registry=datalayer.registry)` →
`commands.bind(worker)`. In the nodes, `commands.start(...)`; before `bind()` every call is rejected with
`DL_INVALID_VALUE` ("not ready"), and `need(commands)` rejects when tests pass no commands. Three apps had built
this by hand (`ReaderCommands`, `DataLayerCommands`, `commands=services.queries`).

## JSON string input node (structured data from a PLC) - see `apps/ctrlx-amr-fleet`

When a PLC/fleet manager sends a whole list at once (e.g. all AMR positions), one writable `string`
node with JSON is the simplest contract (no flatbuffers schema to share). Pattern that worked:
* Validate the whole document in `on_write` (pure-Python parser → unit tests); reject it as a whole
  with a message that names the element (`amrs[2].rotation: missing`); keep the last good value.
* Publish the reason in a read-only `.../last-error` node (empty after the next good write) plus
  counters (`updates`, `rejected`) - PLC programmers can't see the app's log.
* `max_length` on the node (e.g. 1 MiB), a maximum element count, unknown keys ignored (lets the
  format grow), `null` = optional field not set. Keep it in memory only (cyclic writes).
* Document the format in the node description (shown in the Data Layer browser) and the app README.

## Nodes per item: one wildcard provider node - verified 2026-11-01, see `apps/ctrlx-amr-fleet` 0.4.0

When the nodes follow a list that changes at runtime (`fleet/amr/<id>/x` for every AMR of a list), register
ONE `ProviderNode` at `<path>/**` (plus `<path>` for the folder) instead of a node per item and value: its
callbacks get the full address; `on_browse` returns the children, `on_metadata` a Folder or a Variable with its
type, an unknown address `INVALID_ADDRESS`. Register it only while needed and unregister to remove the whole
subtree (gotchas #88). Keep the address logic in a pure-Python class (`amr_inputs.py`) for unit tests; give every
value the type of the PLC variable that writes it and accept only that type ("Node types for PLC variables" -
amr-fleet 0.4.0 still accepts any number type, from before that rule); merge many single writes into one state
update when the writes pause, not per write.

## Client (reading/writing other nodes): `src/app/datalayer_client.py` (template 0.4.0)

core-connector's `ClientAdapter` (verified 2026-10-21 .. 11-01 on the COREvirtual, X3 and X7, gotchas #79/#87),
moved into the template 2026-11-08 without its app-specific calls (creating remotes). **Use it; don't write
another client wrapper** (user's request: a feature several apps need lives in the template).

```python
class Worker:
    def attach(self, client):            # called from the Data Layer thread
        self._dl = client                # a DataLayerClient while connected, None when not
worker = Worker()
datalayer = DataLayerService(settings, version, commands, on_client=worker.attach)   # main.py
# in the worker, with self._dl not None:
self._dl.read("system/info/hostname")                    # value (scalars, lists, flatbuffers objects as dict)
self._dl.read_typed("plc/app/Application/sym/PLC_PRG/x") # ("float32", 1.5)
self._dl.write("plc/app/.../setpoint", "float32", 2.5)   # exactly the node's type, else TYPE_MISMATCH
self._dl.write_json(addr, {"type": "int32", "value": 5}) # or a flatbuffers object as dict
self._dl.browse(addr), self._dl.metadata(addr), self._dl.remove(addr)
self._dl.read_many([...]), browse_many([...]), metadata_many([...])   # one bulk request: [(result, ...)]
```

* Errors: `DataLayerError` with `.result` = the normalized result name (`INVALID_ADDRESS`, `TYPE_MISMATCH`,
  `PERMISSION_DENIED`; `DL_` prefixes and garbled enum names fixed, gotchas #78). `write()` checks the value
  against the type's range first (`ValueError`).
* Without `on_client` the service opens no client (no cost for apps that only provide nodes).
* One lock for all calls; every call waits for the answer (the sync API has no per-request timeout:
  `TimeoutSetting` has IDLE, PING, Reconnect only). Call it from worker threads or web handlers, **never from a
  provider callback** (`on_read`/`on_write` run in a Data Layer thread and must return fast).
* Values that change often: subscriptions (`subscription_async`, sample `datalayer.client.sub`) are cheaper than
  polling - not in `DataLayerClient` yet (no kit app needed one); add them there when the first app does.
* Tests: `tests/fakes/ctrlxdatalayer/client.py` routes read / write / browse to the fake provider's nodes
  (`tests/test_datalayer_glue.py` `ClientGlueTest`); a worker's own tests can use a small fake with the same methods.
* Under the hood: `client.read_sync(addr)` → `with data:` → getter by type; object nodes (`FLATBUFFERS`):
  `client.read_json_sync(system.json_converter(), addr, -1)` → JSON text (`{"type", "value"}` for simple types).
* Creating object nodes: build the flatbuffer with the generated classes of `ctrlx-fbs`
  (e.g. `comm.datalayer.remote.ConfigItem`) → `Variant.set_flatbuffers(builder.Output())` →
  `client.create_sync(parent, variant)`. Types: read `types/...` via REST to see the schema.
* No per-request timeout in the Python API (`TimeoutSetting`: IDLE, PING, Reconnect only).

## Remote connections to other devices (`devices/remotes`) - verified on ctrlX OS 4.6

* Create `{"name": N, "address": "tcp://<user>:<pw>@<ip>?sslport=443&NoCheckCertificate=true"}`
  under `devices/remotes` (type `types/datalayer/remoteconfig`); the other device's whole Data
  Layer appears at `devices/remotes/N/...` for every app on this device. Delete the node to remove it.
* `NoCheckCertificate=true` is required for devices with self-signed certificates (else
  `DL_SEC_UNAUTHORIZED`; gotchas #40). Reading the config back masks credentials (`tcp://***:***@`).
* The Data Layer persists remotes itself (`hasSave: datalayer/admin/cfg/save`, part of configurations).
* Remote names may contain capital letters (`devices/remotes/cm-Line-8-Core` created, read through and removed
  on ctrlX OS 4.6, 2026-10-31).
* `datalayer/reverse-connections` is the opposite direction: *this* device's Data Layer appears on
  the remote device under the given name.
* A remote can get stuck at `SEC_INVALID_SESSION` (e.g. after the other device's clock was set); it does not
  recover by itself - delete and recreate it (gotchas #77). Python result names have no `DL_` prefix (#78).
* Browsing/reading/writing other nodes with a client (JSON forms, metadata as JSON, bulk requests - one bulk
  call per folder is ~25x faster through a remote than node by node): gotchas #79, the template's
  `DataLayerClient` (from `apps/ctrlx-core-connector`, whose `src/app/browser.py` shows the bulk calls in use).
* Useful nodes on any device: `system/info/{hostname,ctrlxOsVersion,architecture,uptime}`,
  `scheduler/admin/state` (`{"state": "OPERATING" | "SETUP" | "SERVICE"}`),
  `framework/metrics/system/{cpu-utilisation-percent,memused-percent,memused-mb,memtotal-mb}`,
  `system/apps/installed/<snap>` (object, `release.version`). ctrlX OS 3.6 differs: no
  `system/info/ctrlxOsVersion`, app nodes named `snap_<snap>` (gotchas #44).

## Rules

* **Never persist on every Data Layer write** (a PLC may write cyclically → flash wear).
  Persist only explicit user changes (web UI / settings).
* Don't busy-poll other nodes faster than needed; ctrlX CORE has limited CPU and real-time
  tasks have priority (App Guide 6.3).
* Outside the snap: `CTRLX_HOST=10.0.2.2 CTRLX_SSL_PORT=8443 ./run-local.sh` uses
  `tcp://user:pw@host?sslport=8443` (needs `ctrlx-datalayer` pip + the .deb installed locally).
* REST access for tests: `tools/ctrlx.py dl-read <name>/status/heartbeat`,
  `tools/ctrlx.py dl-write <name>/values/setpoint 1.5 --type float64`.
