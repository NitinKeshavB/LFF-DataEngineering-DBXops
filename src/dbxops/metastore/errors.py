"""Errors raised by the metadata database library."""


class MetastoreError(Exception):
    """Base class of every error raised by ``dbxops.metastore``."""


class PublishError(MetastoreError):
    """Raised when a publish violates a versioning or integrity rule and must not be applied."""


class WriterError(MetastoreError):
    """Raised when a publisher tries to write rows or tables it does not own."""


class SchemaError(MetastoreError):
    """Raised when the database schema is missing, too new, or cannot be created."""


class ConfigError(MetastoreError):
    """Raised when ``conf/metastore.yml`` or a credential source is unusable."""


class CollectError(MetastoreError):
    """Raised when a parsed document cannot be mapped to records."""
