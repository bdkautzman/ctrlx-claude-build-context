"""Fake ctrlxdatalayer.client.Client: just enough for DataLayerService (monitor uses its own fake)."""

ALL_CLIENTS = []


class Client:
    def __init__(self, remote):
        self.remote = remote
        self.closed = False
        ALL_CLIENTS.append(self)

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True
