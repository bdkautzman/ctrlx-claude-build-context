from ctrlxdatalayer.client import Client
from ctrlxdatalayer.provider import Provider


class Factory:
    def create_provider(self, remote):
        return Provider(remote)

    def create_client(self, remote):
        return Client(remote)
