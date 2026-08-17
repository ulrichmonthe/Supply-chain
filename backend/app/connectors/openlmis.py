"""OpenLMIS v3 connector.

OpenLMIS runs in around eight national deployments covering 11,000+ facilities. Its
reference data service is the useful part here: facilities with locations and
geographic zones, and orderables as the product catalogue.

Consumption is the awkward part, and the connector is honest about it. OpenLMIS
exposes requisitions rather than a single "what did this facility use" endpoint, and
which reporting endpoint a country has depends on which services they deployed. So
consumption is pulled through a **configurable tabular extraction**: an endpoint, its
parameters, and the keys to read a facility, a product and a quantity out of whatever
comes back. That is not a shortcut — it is the only way one connector can serve
instances that genuinely differ, and it lets a country fix a path in a form field
instead of waiting for a release.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .base import (
    DEFAULT_PAGE_SIZE,
    MAX_RECORDS,
    Check,
    ConnectionInfo,
    Connector,
    ConnectorError,
    FetchResult,
    coerce_float,
    make_demand,
    make_node,
    make_product,
)


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of nested JSON: 'type.code', 'geographicZone.parent.name'."""
    if not path:
        return default
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return default
        if current is None:
            return default
    return current


class OpenLMISConnector(Connector):
    system = "openlmis"
    label = "OpenLMIS v3"
    description = (
        "Pulls facilities and geographic zones from the reference data service, and "
        "products from orderables. Consumption is read through a configurable endpoint "
        "because which reporting service a deployment runs varies by country."
    )
    docs_url = "https://docs.openlmis.org/en/latest/api/"
    auth_types = ["basic"]
    verified_against_live_instance = False

    config_spec = {
        "token_path": {
            "default": "/api/oauth/token",
            "help": "OAuth2 token endpoint. OpenLMIS uses a password grant with a fixed "
            "client credential in addition to the user's own.",
        },
        "client_id": {"default": "user-client", "help": "OAuth2 client id."},
        "client_secret": {"default": "changeme", "help": "OAuth2 client secret."},
        "facilities_path": {"default": "/api/facilities", "help": "Facility list endpoint."},
        "orderables_path": {"default": "/api/orderables", "help": "Product catalogue endpoint."},
        "page_size": {"default": DEFAULT_PAGE_SIZE, "kind": "number", "help": "Records per page."},
        "facility_type_filter": {
            "default": "",
            "help": "Optional comma-separated facility type codes to keep, e.g. health_center,"
            "district_hospital. Blank keeps everything active.",
        },
        "include_inactive": {
            "default": False,
            "kind": "boolean",
            "help": "Import facilities marked inactive or not enabled.",
        },
        "consumption_path": {
            "default": "",
            "help": "Endpoint returning consumption rows. Blank imports facilities and "
            "products only. Whatever it returns is read with the four keys below.",
        },
        "consumption_params": {
            "default": {},
            "kind": "map",
            "help": "Query parameters sent to the consumption endpoint, e.g. programId and a "
            "date range.",
        },
        "consumption_records_key": {
            "default": "content",
            "help": "Key holding the array of rows. Blank if the response is itself an array.",
        },
        "consumption_facility_key": {
            "default": "facility.code",
            "help": "Dotted path to the facility code on each row.",
        },
        "consumption_product_key": {
            "default": "orderable.productCode",
            "help": "Dotted path to the product code on each row.",
        },
        "consumption_quantity_key": {
            "default": "totalConsumedQuantity",
            "help": "Dotted path to the quantity on each row.",
        },
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token: Optional[str] = None

    # --- auth --------------------------------------------------------------------

    def _authenticate(self, client: httpx.Client) -> str:
        """OpenLMIS wants a password grant authorised by a separate client credential."""
        if self._token:
            return self._token
        try:
            response = client.post(
                str(self.config.get("token_path")),
                params={
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.secret,
                },
                auth=httpx.BasicAuth(
                    str(self.config.get("client_id") or ""), str(self.config.get("client_secret") or "")
                ),
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Could not reach the OpenLMIS token endpoint at {self.base_url}.",
                system=self.system,
                step="authentication",
                hint="Check the base URL and that the auth service is running.",
            ) from exc

        payload = self._decode(response, step="authentication")
        token = (payload or {}).get("access_token")
        if not token:
            raise ConnectorError(
                "OpenLMIS accepted the request but returned no access token.",
                system=self.system,
                step="authentication",
                hint="Usually the client id or client secret rather than the user password. "
                "Both are settings on this connection.",
            )
        self._token = str(token)
        return self._token

    def _authed_get(self, client: httpx.Client, path: str, *, step: str, params: Optional[dict] = None):
        token = self._authenticate(client)
        merged = dict(params or {})
        merged.setdefault("access_token", token)
        try:
            response = client.get(path, params=merged, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Request to {self.base_url}{path} failed: {exc}", system=self.system, step=step
            ) from exc
        return self._decode(response, step=step)

    # --- test ---------------------------------------------------------------------

    def test(self) -> ConnectionInfo:
        checks: List[Check] = []
        with self.client() as client:
            try:
                self._authenticate(client)
                checks.append(Check("Authenticated", True, "Password grant accepted."))
            except ConnectorError as exc:
                checks.append(Check("Authenticated", False, exc.full()))
                return ConnectionInfo(False, self.system, "", exc.full(), checks)

            try:
                payload = self._authed_get(
                    client, str(self.config.get("facilities_path")), step="facilities",
                    params={"page": 0, "size": 1},
                )
                total = payload.get("totalElements") if isinstance(payload, dict) else None
                checks.append(
                    Check("Can read facilities", True, f"{total if total is not None else 'some'} facilities visible")
                )
            except ConnectorError as exc:
                checks.append(Check("Can read facilities", False, exc.full()))

            try:
                self._authed_get(
                    client, str(self.config.get("orderables_path")), step="orderables",
                    params={"page": 0, "size": 1},
                )
                checks.append(Check("Can read orderables", True, "Product catalogue readable."))
            except ConnectorError as exc:
                checks.append(Check("Can read orderables", False, exc.full()))

            if self.config.get("consumption_path"):
                checks.append(
                    Check("Consumption endpoint configured", True, str(self.config.get("consumption_path")))
                )
            else:
                checks.append(
                    Check(
                        "Consumption endpoint configured",
                        False,
                        "Blank, so this connection imports facilities and products only. "
                        "OpenLMIS has no single consumption endpoint; set the one your "
                        "deployment exposes.",
                    )
                )

        ok = all(check.ok for check in checks[:2])
        return ConnectionInfo(ok, self.system, "v3", "Ready" if ok else "Connected with problems.", checks)

    # --- paging ---------------------------------------------------------------------

    def _paged(self, client: httpx.Client, path: str, *, step: str, limit: Optional[int] = None):
        page_size = int(self.config.get("page_size") or DEFAULT_PAGE_SIZE)
        page = 0
        seen = 0
        while True:
            payload = self._authed_get(client, path, step=step, params={"page": page, "size": page_size})
            if isinstance(payload, list):
                rows = payload
                total_pages = 1
            else:
                rows = payload.get("content") or []
                total_pages = payload.get("totalPages")
            if not rows:
                break
            for row in rows:
                yield row
                seen += 1
                if (limit and seen >= limit) or seen >= MAX_RECORDS:
                    return
            page += 1
            if total_pages is not None and page >= int(total_pages):
                break
            if total_pages is None and len(rows) < page_size:
                break

    # --- facilities -------------------------------------------------------------------

    def fetch_facilities(self, client: httpx.Client, limit: Optional[int] = None) -> tuple:
        wanted_types = {
            t.strip().lower()
            for t in str(self.config.get("facility_type_filter") or "").split(",")
            if t.strip()
        }
        include_inactive = bool(self.config.get("include_inactive"))
        nodes: List[dict] = []
        warnings: List[str] = []
        skipped_inactive = 0
        skipped_type = 0
        no_coordinates = 0

        for row in self._paged(client, str(self.config.get("facilities_path")), step="facilities", limit=limit):
            active = row.get("active", True) and row.get("enabled", True)
            if not active and not include_inactive:
                skipped_inactive += 1
                continue

            type_code = str(dig(row, "type.code") or "").lower()
            if wanted_types and type_code not in wanted_types:
                skipped_type += 1
                continue

            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if not code or not name:
                warnings.append(f"Skipped a facility with no code or name: {row!r:.120}")
                continue

            coordinates = dig(row, "location.coordinates") or []
            lat = coerce_float(coordinates[1]) if len(coordinates) >= 2 else None
            lon = coerce_float(coordinates[0]) if len(coordinates) >= 2 else None
            if lat is None or lon is None:
                no_coordinates += 1

            nodes.append(
                make_node(
                    code=code,
                    name=name,
                    lat=lat,
                    lon=lon,
                    admin1=dig(row, "geographicZone.parent.name"),
                    admin2=dig(row, "geographicZone.name"),
                    operating_status="operational" if active else "non_operational",
                    external_ids={"openlmis_code": code, "mfl_code": code},
                    row=len(nodes) + 2,
                    geocode_source="openlmis",
                    geocode_confidence=0.8 if lat is not None else 0.0,
                )
            )

        if skipped_inactive:
            warnings.append(f"{skipped_inactive} inactive or disabled facilities were not imported.")
        if skipped_type:
            warnings.append(f"{skipped_type} facilities were filtered out by facility type.")
        if no_coordinates:
            warnings.append(
                f"{no_coordinates} facilities have no location in OpenLMIS. They are imported "
                f"with confidence 0 so the validator lists them for geocoding."
            )
        return nodes, warnings

    # --- products ------------------------------------------------------------------------

    def fetch_products(self, client: httpx.Client) -> tuple:
        products: List[dict] = []
        seen = set()
        for row in self._paged(client, str(self.config.get("orderables_path")), step="orderables"):
            sku = str(row.get("productCode") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            products.append(
                make_product(
                    sku=sku,
                    name=str(row.get("fullProductName") or sku).strip(),
                    row=len(products) + 2,
                )
            )
        warnings = []
        if products:
            warnings.append(
                f"{len(products)} products imported without a packed volume, which OpenLMIS does "
                f"not carry. The model is volumetric, so the validator will reject them until a "
                f"volume is supplied — take it from the EVM assessment and enter it in the "
                f"workbook."
            )
        return products, warnings

    # --- consumption -----------------------------------------------------------------------

    def fetch_consumption(self, client: httpx.Client, known_codes: set) -> tuple:
        path = str(self.config.get("consumption_path") or "").strip()
        if not path:
            return [], []

        payload = self._authed_get(
            client, path, step="consumption", params=dict(self.config.get("consumption_params") or {})
        )
        records_key = str(self.config.get("consumption_records_key") or "").strip()
        rows = dig(payload, records_key, []) if records_key else payload
        if not isinstance(rows, list):
            raise ConnectorError(
                f"The consumption endpoint did not return a list at '{records_key or 'the root'}'.",
                system=self.system,
                step="consumption",
                hint=f"It returned {type(rows).__name__}. Adjust consumption_records_key to the "
                f"key that holds the rows.",
            )

        facility_key = str(self.config.get("consumption_facility_key"))
        product_key = str(self.config.get("consumption_product_key"))
        quantity_key = str(self.config.get("consumption_quantity_key"))

        totals: Dict[tuple, float] = {}
        unknown = 0
        unreadable = 0
        for row in rows:
            facility = dig(row, facility_key)
            product = dig(row, product_key)
            quantity = coerce_float(dig(row, quantity_key))
            if facility is None or product is None or quantity is None:
                unreadable += 1
                continue
            if str(facility) not in known_codes:
                unknown += 1
                continue
            key = (str(facility), str(product))
            totals[key] = totals.get(key, 0.0) + quantity

        demand = [
            make_demand(node_code=node, sku=sku, quantity=round(total, 3), row=index + 2)
            for index, ((node, sku), total) in enumerate(sorted(totals.items()))
        ]

        warnings = []
        if unreadable:
            warnings.append(
                f"{unreadable} consumption rows could not be read with the configured keys "
                f"({facility_key}, {product_key}, {quantity_key}). Check them against a sample "
                f"response from your instance."
            )
        if unknown:
            warnings.append(f"{unknown} consumption rows are for facilities not in the imported list.")
        return demand, warnings

    # --- fetch -------------------------------------------------------------------------------

    def fetch(self, *, include_demand: bool = True, limit: Optional[int] = None) -> FetchResult:
        with self.client() as client:
            nodes, warnings = self.fetch_facilities(client, limit=limit)
            products, product_warnings = self.fetch_products(client)
            warnings.extend(product_warnings)

            demand: List[dict] = []
            if include_demand:
                demand, demand_warnings = self.fetch_consumption(client, {n["code"] for n in nodes})
                warnings.extend(demand_warnings)

        return FetchResult(
            nodes=nodes,
            products=products,
            demand=demand,
            warnings=warnings,
            stats={"facilities": len(nodes), "products": len(products), "demand_rows": len(demand)},
        )
