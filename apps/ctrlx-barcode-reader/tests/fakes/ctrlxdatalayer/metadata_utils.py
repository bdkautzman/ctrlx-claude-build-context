from ctrlxdatalayer.variant import Variant


class MetadataBuilder:
    @staticmethod
    def create_metadata(name, description, unit, description_url, node_class, read_allowed, write_allowed,
                        create_allowed, delete_allowed, browse_allowed, type_path, references=None):
        v = Variant()
        v.meta = dict(name=name, write_allowed=write_allowed, type_path=type_path)
        return v
