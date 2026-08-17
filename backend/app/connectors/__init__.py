"""Read-only connectors to logistics information systems.

Registry only. The contract lives in ``base``, the reconciliation in ``reconcile``,
and each system in its own module.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from .base import Connector, ConnectorError, FetchResult
from .dhis2 import DHIS2Connector
from .msupply import MSupplyConnector
from .openlmis import OpenLMISConnector

REGISTRY: Dict[str, Type[Connector]] = {
    DHIS2Connector.system: DHIS2Connector,
    MSupplyConnector.system: MSupplyConnector,
    OpenLMISConnector.system: OpenLMISConnector,
}


def get_connector_class(system: str) -> Type[Connector]:
    try:
        return REGISTRY[system]
    except KeyError:
        raise ConnectorError(
            f"'{system}' is not a system this build can connect to.",
            system=system,
            step="lookup",
            hint=f"Available: {', '.join(sorted(REGISTRY))}. Anything else comes in through "
            f"the Excel importer, which is a first-class path rather than a fallback.",
        ) from None


def describe_all() -> List[dict]:
    return [cls.describe() for cls in REGISTRY.values()]


def build(connection, *, transport=None, secret: Optional[str] = None) -> Connector:
    """Instantiate a connector from a stored Connection row."""
    cls = get_connector_class(connection.system)
    return cls(
        base_url=connection.base_url,
        auth_type=connection.auth_type,
        username=connection.username or "",
        secret=secret if secret is not None else (connection.resolved_secret() or ""),
        config=connection.config or {},
        verify_tls=connection.verify_tls,
        timeout_s=connection.timeout_s,
        transport=transport,
    )


__all__ = [
    "REGISTRY",
    "Connector",
    "ConnectorError",
    "FetchResult",
    "build",
    "describe_all",
    "get_connector_class",
]
