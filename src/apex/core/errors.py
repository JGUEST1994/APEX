class ApexError(Exception):
    """Base class for APEX errors."""


class DataQualityError(ApexError):
    """Market data failed validation badly enough that it must not be used."""


class ProviderError(ApexError):
    """A market data provider failed or returned something unusable."""


class UnknownInstrumentError(ApexError):
    pass
