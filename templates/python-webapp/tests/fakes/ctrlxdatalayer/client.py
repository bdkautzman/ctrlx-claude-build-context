from ctrlxdatalayer import provider as _provider
from ctrlxdatalayer.variant import Result, Variant


class Client:
    """Routes the sync calls to the nodes of the fake providers - an in-process stand-in for the broker (only what
    app/datalayer_client.py uses in the tests: read, write, browse)."""

    def __init__(self, remote):
        self.remote, self.closed = remote, False

    def close(self):
        self.closed = True

    def _provider(self, address):
        for p in reversed(_provider.ALL_PROVIDERS):
            if address in p.nodes:
                return p
        return None

    def read_sync(self, address):
        p = self._provider(address)
        if p is None:
            return Result.INVALID_ADDRESS, Variant()
        result, data = p.read(address)
        return result, data if data is not None else Variant()

    def write_sync(self, address, data):
        p = self._provider(address)
        if p is None:
            return Result.INVALID_ADDRESS, data
        return p.write(address, data)[0], data

    def browse_sync(self, address):
        prefix = address.rstrip("/") + "/"
        names = sorted({a[len(prefix):].split("/")[0] for p in _provider.ALL_PROVIDERS for a in p.nodes
                        if a.startswith(prefix)})
        data = Variant()
        data.set_array_string(names)
        return (Result.OK if names else Result.INVALID_ADDRESS), data
