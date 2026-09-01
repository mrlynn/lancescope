"""The language layer: providers, the model registry, and how one is chosen.

Everything above this package narrates facts computed elsewhere. Nothing in here
opens a dataset, and nothing it sends to a model contains a row.
"""

from server.intel.config import ROLES, Resolved, provider_for, resolve
from server.intel.providers import (
    Completion,
    NoProvider,
    Provider,
    ProviderError,
    Usage,
)

__all__ = [
    "ROLES", "Completion", "NoProvider", "Provider", "ProviderError", "Resolved",
    "Usage", "provider_for", "resolve",
]
