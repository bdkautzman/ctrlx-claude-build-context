from ctrlxdatalayer.variant import Result

ALL_PROVIDERS = []  # test hook


class Provider:
    def __init__(self, remote):
        self.remote, self.nodes, self.started, self.closed = remote, {}, False, False
        self.connected = True
        ALL_PROVIDERS.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def start(self):
        self.started = True
        return Result.OK

    def stop(self):
        self.started = False
        return Result.OK

    def is_connected(self):
        return self.connected and not self.closed

    def register_node(self, address, node):
        self.nodes[address] = node
        return Result.OK

    def unregister_node(self, address):
        self.nodes.pop(address, None)
        return Result.OK

    # --- test helpers: call the node callbacks like the broker would ---
    def _call(self, address, name, *args):
        out = []
        cb = lambda result, data: out.append((result, None if data is None else data.clone()[1]))  # noqa: E731
        getattr(self.nodes[address].cbs, name)(None, address, *args, cb)
        assert len(out) == 1, "callback must be called exactly once"
        return out[0]

    def read(self, address):
        return self._call(address, "on_read", None)

    def write(self, address, variant):
        return self._call(address, "on_write", variant)

    def metadata(self, address):
        return self._call(address, "on_metadata")
