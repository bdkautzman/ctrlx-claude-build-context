class ProviderNodeCallbacks:
    def __init__(self, on_create, on_remove, on_browse, on_read, on_write, on_metadata,
                 on_subscribe=None, on_unsubscribe=None):
        self.on_create, self.on_remove, self.on_browse = on_create, on_remove, on_browse
        self.on_read, self.on_write, self.on_metadata = on_read, on_write, on_metadata


class ProviderNode:
    def __init__(self, cbs, userdata=None):
        self.cbs, self.closed = cbs, False

    def close(self):
        self.closed = True
