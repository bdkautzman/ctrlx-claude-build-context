from ctrlxdatalayer.provider import Provider


class Factory:
    def create_provider(self, remote):
        return Provider(remote)
