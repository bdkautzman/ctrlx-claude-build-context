# ctrlX Data Layer from Python

Exact API (3.6.0, extracted from the SDK's Doxygen): `reference/ctrlxdatalayer-api-3.6.0/`
(`system.py`, `factory.py`, `provider.py`, `provider_node.py`, `client.py`, `variant.py`,
`metadata_utils.py`, `subscription_*.py`, `bulk.py`). **Grep there before using any call;
do not guess signatures.** SDK samples: `reference/sdk-4.6.0-samples-python/datalayer.*`.

## Template structure

* `src/app/datalayer_nodes.py` – **edit here**: `build_node_specs()` returns `NodeSpec`s
  (`name`, `vtype` ∈ bool8/int64/uint64/float64/string, `initial`, `writable`, `minimum`,
  `maximum`, `max_length`, `description`, `unit`, `on_write`, `json`, `view_role`, `write_role`).
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

Client (reading other nodes) – same system, `system.factory().create_client("ipc://")`, then
`client.read_sync(addr) -> (Result, Variant)`, `client.write_sync(addr, variant)`,
`client.metadata_sync(addr)`; subscriptions via `subscription_sync`/`subscription_async`
(see sample `datalayer.client.sub`). Prefer subscriptions over polling for frequent values.

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
* Type addresses: `types/datalayer/bool8|int8..int64|uint8..uint64|float32|float64|string|…`.
  Complex data: flatbuffers (`register_type(address, bfbs_path)`, see sample `datalayer.provider`)
  or a JSON string node (simple, used by community apps; consumers must parse it).
* Node names in Data Layer UI: Settings → Data Layer → `<name>/`.

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
subtree (gotchas #88). Keep the address logic in a pure-Python class (`amr_inputs.py`) for unit tests; accept any
number type on writes (a PLC writes REAL or INT); merge many single writes into one state update when the
writes pause, not per write.

## Client (reading other nodes) - verified 2026-10-21, see `apps/ctrlx-core-connector` (until 0.1.x `ctrlx-core-monitor`)

* Same `System` as the provider: `client = system.factory().create_client("ipc://")`; close it
  before `system.stop()`. Wrap it in a small adapter (read / create / remove that raise an
  exception with the result name) so the app logic is testable with a fake.
* `result, data = client.read_sync(addr)` → `with data:` → getter by type:
  `getattr(data, "get_" + data.get_type().name.lower())()` for simple types.
* Object nodes (`VariantType.FLATBUFFERS`): `client.read_json_sync(system.json_converter(), addr, 0)`
  → JSON string (unwrap `{"type", "value"}` if present).
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
  call per folder is ~25x faster through a remote than node by node): gotchas #79, example
  `apps/ctrlx-core-connector` (`src/app/datalayer_service.py` `ClientAdapter`, `src/app/browser.py`).
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
