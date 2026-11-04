from ctrlxdatalayer.factory import Factory


class System:
    def __init__(self, ipc_path):
        self.started = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.stop(True)

    def start(self, bo_start_broker):
        self.started = True

    def stop(self, bo_force_provider_stop):
        self.started = False
        return True

    def factory(self):
        return Factory()
