"""The connector contract.

Three rules shape everything in this package.

**Live data earns no privileges.** A connector's only job is to turn a remote
system's response into the same record shape the Excel importer produces. From that
point the data goes through the identical validator, the identical reconciliation and
the identical commit path. A facility pulled from DHIS2 with its coordinate in the
Bismarck Sea gets caught by exactly the check that catches it in a spreadsheet. An
integration that bypassed validation would be a downgrade dressed as an upgrade.

**Nothing is written until a human has seen what would change.** Every sync is a
preview first: what matched, what is new, what exists here but not there, and what
the validator thinks of it. Then commit. A sync that silently mutated a national
facility list would be the fastest possible way to lose a ministry's trust.

**The mapping is data, not code.** Endpoints, field lists, GraphQL documents and
product mappings all live in ``Connection.config``, because a DHIS2 2.36 instance and
a DHIS2 2.41 instance disagree about details, and a country should not need a release
to fix a field name. Defaults are supplied and documented; every one is overridable
from the API.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

#: Connectors are read-only by contract. Nothing in this package writes to an LMIS:
#: the tool is a consumer of the record of truth, never an author of it.
READ_ONLY = True

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_PAGE_SIZE = 500
#: Refuse to pull an unbounded facility list into memory by accident.
MAX_RECORDS = 200_000


class ConnectorError(RuntimeError):
    """A connector could not do its job, with a message aimed at the operator.

    Connector failures are shown to a logistics officer, not to a developer, so the
    message says which system, which step, and what to try.
    """

    def __init__(self, message: str, *, system: str = "", step: str = "", hint: str = ""):
        self.system = system
        self.step = step
        self.hint = hint
        super().__init__(message)

    def as_dict(self) -> dict:
        return {
            "message": str(self),
            "system": self.system,
            "step": self.step,
            "hint": self.hint,
        }

    def full(self) -> str:
        """Message and hint together.

        The message says what failed; the hint says what to do about it. Reporting
        only the first half is how a connector error becomes a support ticket.
        """
        return f"{self} {self.hint}".strip()


@dataclass
class Check:
    """One step of a connection test, reported individually.

    'Connection failed' is not actionable. 'Reached the server, authenticated, but
    the account cannot read organisation units' is.
    """

    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ConnectionInfo:
    ok: bool
    system: str
    version: str = ""
    detail: str = ""
    checks: List[Check] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "system": self.system,
            "version": self.version,
            "detail": self.detail,
            "checks": [c.as_dict() for c in self.checks],
        }


@dataclass
class FetchResult:
    """Records in the same shape ``excel_in.parse_workbook`` produces.

    Deliberately identical, so the validator, the reconciler and the commit path do
    not need to know or care which one they are looking at.
    """

    nodes: List[dict] = field(default_factory=list)
    products: List[dict] = field(default_factory=list)
    demand: List[dict] = field(default_factory=list)
    #: Non-fatal problems worth surfacing: skipped records, missing coordinates, and
    #: anything the remote system returned that this connector chose not to map.
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": [],  # lanes are never sourced from an LMIS; see the note below
            "products": self.products,
            "demand": self.demand,
            "missing_sheets": [],
        }


class Connector(abc.ABC):
    """Base class for a read-only pull from a logistics information system.

    Note what is deliberately absent: no connector returns *edges*. No LMIS knows
    which boat calls at which island on which day. Facilities and consumption come
    from the LMIS; the transport network comes from the workbook, from interviews,
    and from the distance cascade. Pretending otherwise would put a fabricated lane
    into a model that a ministry is about to spend money on.
    """

    system: str = ""
    label: str = ""
    #: One paragraph for the operator choosing a system in the UI.
    description: str = ""
    #: Documentation link so an operator can check a field name themselves.
    docs_url: str = ""
    #: Config keys this connector understands, each with a default and an explanation.
    config_spec: Dict[str, dict] = {}
    #: Which auth styles the remote system accepts.
    auth_types: List[str] = ["basic"]
    #: Set False where the endpoints could not be confirmed against a live instance
    #: of the system; the UI says so rather than implying a verified integration.
    verified_against_live_instance: bool = False

    def __init__(
        self,
        *,
        base_url: str,
        auth_type: str = "basic",
        username: str = "",
        secret: str = "",
        config: Optional[dict] = None,
        verify_tls: bool = True,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.auth_type = auth_type
        self.username = username
        self.secret = secret
        self.config = {**self.defaults(), **(config or {})}
        self.verify_tls = verify_tls
        self.timeout_s = timeout_s
        self._transport = transport

    # --- configuration ---------------------------------------------------------

    @classmethod
    def defaults(cls) -> dict:
        return {key: spec.get("default") for key, spec in cls.config_spec.items()}

    @classmethod
    def describe(cls) -> dict:
        return {
            "system": cls.system,
            "label": cls.label,
            "description": cls.description,
            "docs_url": cls.docs_url,
            "auth_types": cls.auth_types,
            "verified_against_live_instance": cls.verified_against_live_instance,
            "config_spec": {
                key: {
                    "default": spec.get("default"),
                    "help": spec.get("help", ""),
                    "kind": spec.get("kind", "text"),
                }
                for key, spec in cls.config_spec.items()
            },
        }

    # --- http ------------------------------------------------------------------

    def _auth(self) -> Optional[httpx.Auth]:
        if self.auth_type == "basic" and (self.username or self.secret):
            return httpx.BasicAuth(self.username, self.secret)
        return None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "hscn-network-design/0.1"}
        if self.auth_type == "token" and self.secret:
            # DHIS2 personal access tokens use this scheme rather than Bearer.
            headers["Authorization"] = f"ApiToken {self.secret}"
        elif self.auth_type == "bearer" and self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            auth=self._auth(),
            headers=self._headers(),
            timeout=self.timeout_s,
            verify=self.verify_tls,
            transport=self._transport,
            follow_redirects=True,
        )

    def get_json(self, client: httpx.Client, path: str, *, step: str, params: Optional[dict] = None) -> Any:
        """GET and decode, converting every failure into an operator-readable error."""
        try:
            response = client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ConnectorError(
                f"{self.label} did not respond within {self.timeout_s:.0f} seconds.",
                system=self.system,
                step=step,
                hint="A slow link is normal on a provincial connection. Raise the timeout on "
                "the connection, or pull a smaller page size.",
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectorError(
                f"Could not reach {self.base_url}.",
                system=self.system,
                step=step,
                hint="Check the base URL and that this machine can reach the server. An LMIS "
                "on a ministry intranet is often not reachable from outside it.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Request to {self.base_url} failed: {exc}",
                system=self.system,
                step=step,
            ) from exc
        return self._decode(response, step=step)

    def post_json(self, client: httpx.Client, path: str, *, step: str, json: Any) -> Any:
        try:
            response = client.post(path, json=json)
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Request to {self.base_url} failed: {exc}", system=self.system, step=step
            ) from exc
        return self._decode(response, step=step)

    def _decode(self, response: httpx.Response, *, step: str) -> Any:
        if response.status_code in (401, 403):
            raise ConnectorError(
                f"{self.label} refused the credentials ({response.status_code}).",
                system=self.system,
                step=step,
                hint="Check the username and token. The account also needs read access to "
                "this resource — an account that can log in is not necessarily one that "
                "can export the facility list.",
            )
        if response.status_code == 404:
            raise ConnectorError(
                f"{self.label} has no endpoint at {response.request.url.path}.",
                system=self.system,
                step=step,
                hint="Usually a version difference. Endpoints are configurable on the "
                "connection — correct the path there rather than waiting for a release.",
            )
        if response.status_code >= 400:
            raise ConnectorError(
                f"{self.label} returned {response.status_code} for {response.request.url.path}.",
                system=self.system,
                step=step,
                hint=(response.text or "")[:300],
            )
        try:
            return response.json()
        except ValueError as exc:
            body = (response.text or "")[:200]
            raise ConnectorError(
                f"{self.label} returned something that is not JSON.",
                system=self.system,
                step=step,
                hint=f"The server sent: {body!r}. A login page here usually means the base "
                f"URL points at the web interface rather than the API.",
            ) from exc

    # --- contract ---------------------------------------------------------------

    @abc.abstractmethod
    def test(self) -> ConnectionInfo:
        """Reach the server, authenticate, and confirm the data can be read."""

    @abc.abstractmethod
    def fetch(self, *, include_demand: bool = True, limit: Optional[int] = None) -> FetchResult:
        """Pull facilities, products and consumption in importer record shape."""


# --- shared record builders ---------------------------------------------------------
# Every connector produces records through these, so a field that the Excel importer
# understands is spelled the same way whatever system it came from.


def make_node(
    *,
    code: str,
    name: str,
    lat: Optional[float],
    lon: Optional[float],
    external_ids: Dict[str, str],
    row: int,
    level: int = 3,
    node_type: str = "health_facility",
    admin1: Optional[str] = None,
    admin2: Optional[str] = None,
    terrain_class: str = "mainland_road",
    catchment_population: float = 0.0,
    operating_status: str = "operational",
    geocode_source: str = "lmis",
    geocode_confidence: float = 0.75,
) -> dict:
    return {
        "_row": row,
        "code": code,
        "name": name,
        "level": level,
        "type": node_type,
        "lat": lat,
        "lon": lon,
        "geocode_source": geocode_source,
        "geocode_confidence": geocode_confidence,
        "admin1": admin1,
        "admin2": admin2,
        "terrain_class": terrain_class,
        "catchment_population": catchment_population,
        "operating_status": operating_status,
        # An LMIS knows where a facility is and what it consumed. It does not know how
        # many cubic metres of shelving it has, so storage is left for the workbook or
        # a cold chain inventory rather than invented here.
        "capacity": {"dry_m3": 0.0, "cold_by_band": {"+2-8": 0.0, "-20": 0.0, "-70": 0.0}},
        "hub_capable": False,
        "hub_fixed_cost": 0.0,
        "hub_open_capex": 0.0,
        "hub_throughput_m3": 0.0,
        "external_ids": {k: v for k, v in external_ids.items() if v},
    }


def make_demand(
    *,
    node_code: str,
    sku: str,
    quantity: float,
    row: int,
    period: int = 0,
    source: str = "actual",
    confidence: float = 0.85,
) -> dict:
    return {
        "_row": row,
        "node": node_code,
        "product": sku,
        "period": period,
        "quantity": quantity,
        "source": source,
        "confidence": confidence,
    }


def make_product(
    *,
    sku: str,
    name: str,
    row: int,
    temperature_band: str = "ambient",
    volume_per_unit_cm3: Optional[float] = None,
    unit_cost: float = 0.0,
    shelf_life_days: int = 730,
) -> dict:
    return {
        "_row": row,
        "sku": sku,
        "name": name,
        "temperature_band": temperature_band,
        # No LMIS carries packed volume. Left as None so the validator says so loudly
        # rather than the model quietly treating the product as taking up no space.
        "volume_per_unit_cm3": volume_per_unit_cm3,
        "unit_cost": unit_cost,
        "shelf_life_days": shelf_life_days,
    }


def coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


ClientFactory = Callable[[], httpx.Client]
