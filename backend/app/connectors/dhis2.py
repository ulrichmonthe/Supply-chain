"""DHIS2 connector.

DHIS2 is the facility registry and routine data backbone in 80+ countries, which
makes it a non-negotiable integration rather than a nice one. Two things are pulled:

* **Organisation units** at a configured level become facilities. This is the
  authoritative facility master list in most countries that run DHIS2, and it is
  usually better maintained than any spreadsheet copy of it.
* **Analytics** values become consumption, mapped from data element UIDs to the
  model's SKUs by a table on the connection.

Version differences are handled by configuration rather than by code. DHIS2 moved
organisation unit coordinates from a ``coordinates`` string to a GeoJSON ``geometry``
object at 2.32, so both are read. Field lists and endpoint paths are settings, so an
instance that names something differently is a config change, not a release.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

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


class DHIS2Connector(Connector):
    system = "dhis2"
    label = "DHIS2"
    description = (
        "Pulls the facility master list from organisation units, and consumption from "
        "the analytics tables. Used in more than 80 countries and normally the "
        "authoritative registry of which facilities exist and where they are."
    )
    docs_url = "https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-master/introduction.html"
    auth_types = ["basic", "token"]
    # The endpoints and payload shapes below follow the published DHIS2 Web API, and
    # are exercised in tests against recorded-shape responses. They have not been run
    # against a live ministry instance in this build; the first Sprint 0 task for any
    # country is to point this at their server and reconcile what comes back.
    verified_against_live_instance = False

    config_spec = {
        "org_unit_path": {
            "default": "/api/organisationUnits",
            "help": "Endpoint listing organisation units.",
        },
        "org_unit_fields": {
            "default": "id,code,name,level,openingDate,closedDate,geometry,coordinates,parent[id,name,parent[id,name]]",
            "help": "DHIS2 field selector. Keep geometry and coordinates: which one is "
            "populated depends on the DHIS2 version.",
        },
        "facility_level": {
            "default": 4,
            "kind": "number",
            "help": "Organisation unit level treated as a health facility. Typically 4 "
            "(country / province / district / facility). Check against "
            "/api/organisationUnitLevels on your instance.",
        },
        "admin1_level": {
            "default": 2,
            "kind": "number",
            "help": "Level used as the province label on imported facilities.",
        },
        "page_size": {"default": DEFAULT_PAGE_SIZE, "kind": "number", "help": "Records per page."},
        "org_unit_filter": {
            "default": "",
            "help": "Optional DHIS2 filter, e.g. organisationUnitGroups.id:eq:<uid> to pull "
            "only facilities in a group.",
        },
        "analytics_path": {"default": "/api/analytics.json", "help": "Analytics endpoint."},
        "analytics_period": {
            "default": "LAST_12_MONTHS",
            "help": "Relative or absolute period for consumption, e.g. LAST_12_MONTHS or 2025.",
        },
        "product_map": {
            "default": {},
            "kind": "map",
            "help": "Data element (or indicator) UID to product SKU. Nothing is pulled as "
            "consumption unless it appears here — an unmapped data element is a "
            "number whose units nobody has checked.",
        },
        "skip_facilities_without_coordinates": {
            "default": False,
            "kind": "boolean",
            "help": "Off by default: a facility with no coordinate should be reported by the "
            "validator and geocoded, not silently dropped from the national list.",
        },
    }

    # --- test -------------------------------------------------------------------

    def test(self) -> ConnectionInfo:
        checks: List[Check] = []
        version = ""
        with self.client() as client:
            try:
                info = self.get_json(client, "/api/system/info", step="system info")
                version = str(info.get("version", "")) if isinstance(info, dict) else ""
                checks.append(Check("Reached the server and authenticated", True, f"DHIS2 {version}"))
            except ConnectorError as exc:
                checks.append(Check("Reached the server and authenticated", False, exc.full()))
                return ConnectionInfo(False, self.system, version, exc.full(), checks)

            level = int(self.config.get("facility_level") or 4)
            try:
                levels = self.get_json(
                    client, "/api/organisationUnitLevels", step="organisation unit levels",
                    params={"fields": "level,name", "paging": "false"},
                )
                names = {
                    int(row["level"]): row.get("name", "")
                    for row in (levels or {}).get("organisationUnitLevels", [])
                    if row.get("level") is not None
                }
                detail = (
                    f"Level {level} is '{names[level]}'"
                    if level in names
                    else f"This instance has no level {level}. It has: "
                    + ", ".join(f"{k} {v}" for k, v in sorted(names.items()))
                )
                checks.append(Check(f"Facility level {level} exists", level in names, detail))
            except ConnectorError as exc:
                checks.append(Check(f"Facility level {level} exists", False, exc.full()))

            try:
                sample = self._get_org_unit_page(client, page=1, page_size=1)
                total = (sample.get("pager") or {}).get("total")
                units = sample.get("organisationUnits") or []
                checks.append(
                    Check(
                        "Can read organisation units",
                        True,
                        f"{total if total is not None else 'an unknown number of'} units visible "
                        f"to this account"
                        + (f"; first is '{units[0].get('name')}'" if units else ""),
                    )
                )
            except ConnectorError as exc:
                checks.append(Check("Can read organisation units", False, exc.full()))

            product_map = self.config.get("product_map") or {}
            if product_map:
                checks.append(
                    Check("Consumption mapping configured", True, f"{len(product_map)} data elements mapped")
                )
            else:
                checks.append(
                    Check(
                        "Consumption mapping configured",
                        False,
                        "No data elements are mapped to SKUs, so this connection will import "
                        "facilities only. Add a product_map to pull consumption.",
                    )
                )

        ok = all(check.ok for check in checks[:3])
        return ConnectionInfo(
            ok,
            self.system,
            version,
            "Ready" if ok else "Connected, but something needed is not readable.",
            checks,
        )

    # --- facilities --------------------------------------------------------------

    def _get_org_unit_page(self, client, *, page: int, page_size: int) -> dict:
        params = {
            "fields": self.config.get("org_unit_fields"),
            "paging": "true",
            "page": page,
            "pageSize": page_size,
            "level": int(self.config.get("facility_level") or 4),
        }
        filter_expr = (self.config.get("org_unit_filter") or "").strip()
        if filter_expr:
            params["filter"] = filter_expr
        payload = self.get_json(
            client, str(self.config.get("org_unit_path")), step="organisation units", params=params
        )
        if not isinstance(payload, dict):
            raise ConnectorError(
                "The organisation unit endpoint did not return an object.",
                system=self.system,
                step="organisation units",
            )
        return payload

    @staticmethod
    def _coordinates(unit: dict) -> tuple:
        """Read a point from either DHIS2 coordinate representation.

        2.32 and later use GeoJSON `geometry`; earlier versions use a `coordinates`
        string holding a JSON array. Both are longitude-first, which is the single
        most common source of transposed health facility coordinates.
        """
        geometry = unit.get("geometry")
        if isinstance(geometry, dict) and geometry.get("type") == "Point":
            coords = geometry.get("coordinates") or []
            if len(coords) >= 2:
                return coerce_float(coords[1]), coerce_float(coords[0])
        raw = unit.get("coordinates")
        if isinstance(raw, str) and raw.strip().startswith("["):
            try:
                coords = json.loads(raw)
            except ValueError:
                return None, None
            if isinstance(coords, list) and len(coords) >= 2 and not isinstance(coords[0], list):
                return coerce_float(coords[1]), coerce_float(coords[0])
        return None, None

    @staticmethod
    def _ancestor_names(unit: dict) -> tuple:
        """(admin1, admin2) walked up the parent chain that DHIS2 nested in the response."""
        chain: List[str] = []
        node: Any = unit.get("parent")
        while isinstance(node, dict):
            if node.get("name"):
                chain.append(node["name"])
            node = node.get("parent")
        # chain is nearest-first: [district, province, ...]
        admin2 = chain[0] if chain else None
        admin1 = chain[1] if len(chain) > 1 else None
        return admin1, admin2

    def fetch_facilities(self, client, limit: Optional[int] = None) -> tuple:
        page_size = int(self.config.get("page_size") or DEFAULT_PAGE_SIZE)
        skip_uncoded = bool(self.config.get("skip_facilities_without_coordinates"))
        nodes: List[dict] = []
        warnings: List[str] = []
        no_coordinates = 0
        closed = 0
        page = 1

        while True:
            payload = self._get_org_unit_page(client, page=page, page_size=page_size)
            units = payload.get("organisationUnits") or []
            if not units:
                break

            for unit in units:
                uid = unit.get("id") or ""
                name = (unit.get("name") or "").strip()
                code = (unit.get("code") or "").strip() or uid
                if not uid or not name:
                    warnings.append(f"Skipped an organisation unit with no id or name: {unit!r:.120}")
                    continue

                if unit.get("closedDate"):
                    closed += 1

                lat, lon = self._coordinates(unit)
                if lat is None or lon is None:
                    no_coordinates += 1
                    if skip_uncoded:
                        continue

                admin1, admin2 = self._ancestor_names(unit)
                nodes.append(
                    make_node(
                        code=code,
                        name=name,
                        lat=lat,
                        lon=lon,
                        admin1=admin1,
                        admin2=admin2,
                        operating_status="non_operational" if unit.get("closedDate") else "operational",
                        external_ids={"dhis2_uid": uid, "mfl_code": (unit.get("code") or "").strip()},
                        row=len(nodes) + 2,
                        geocode_source="dhis2",
                        geocode_confidence=0.8 if lat is not None else 0.0,
                    )
                )
                if limit and len(nodes) >= limit:
                    break

            if limit and len(nodes) >= limit:
                break
            if len(nodes) >= MAX_RECORDS:
                warnings.append(f"Stopped at {MAX_RECORDS} facilities to stay within memory.")
                break

            pager = payload.get("pager") or {}
            page_count = pager.get("pageCount")
            if page_count is not None and page >= int(page_count):
                break
            if page_count is None and len(units) < page_size:
                break
            page += 1

        if no_coordinates:
            warnings.append(
                f"{no_coordinates} facilities have no coordinate in DHIS2. They are imported "
                f"anyway with confidence 0, so the validator lists them for geocoding rather "
                f"than the map quietly omitting them."
            )
        if closed:
            warnings.append(
                f"{closed} facilities have a closed date in DHIS2 and were imported as "
                f"non-operational."
            )
        return nodes, warnings

    # --- consumption ---------------------------------------------------------------

    def fetch_consumption(self, client, node_codes_by_uid: Dict[str, str]) -> tuple:
        product_map: Dict[str, str] = self.config.get("product_map") or {}
        if not product_map:
            return [], [], []

        level = int(self.config.get("facility_level") or 4)
        params = {
            "dimension": [
                f"dx:{';'.join(product_map.keys())}",
                f"pe:{self.config.get('analytics_period')}",
                f"ou:LEVEL-{level}",
            ],
            "skipMeta": "false",
            "displayProperty": "NAME",
        }
        payload = self.get_json(
            client, str(self.config.get("analytics_path")), step="analytics", params=params
        )
        if not isinstance(payload, dict):
            raise ConnectorError(
                "The analytics endpoint did not return an object.", system=self.system, step="analytics"
            )

        headers = [h.get("name") for h in (payload.get("headers") or [])]
        try:
            dx_index = headers.index("dx")
            ou_index = headers.index("ou")
            value_index = headers.index("value")
        except ValueError as exc:
            raise ConnectorError(
                "The analytics response is missing the dx, ou or value column.",
                system=self.system,
                step="analytics",
                hint=f"Columns returned: {headers}",
            ) from exc

        # DHIS2 returns one row per period; the model wants an annual total per
        # facility and product, so periods are summed rather than averaged.
        totals: Dict[tuple, float] = {}
        unknown_units = 0
        for row in payload.get("rows") or []:
            if len(row) <= max(dx_index, ou_index, value_index):
                continue
            sku = product_map.get(row[dx_index])
            node_code = node_codes_by_uid.get(row[ou_index])
            value = coerce_float(row[value_index])
            if sku is None or value is None:
                continue
            if node_code is None:
                unknown_units += 1
                continue
            key = (node_code, sku)
            totals[key] = totals.get(key, 0.0) + value

        demand = [
            make_demand(node_code=node_code, sku=sku, quantity=round(total, 3), row=index + 2)
            for index, ((node_code, sku), total) in enumerate(sorted(totals.items()))
        ]

        names = ((payload.get("metaData") or {}).get("items") or {})
        products = [
            make_product(
                sku=sku,
                name=(names.get(uid) or {}).get("name", sku) if isinstance(names.get(uid), dict) else sku,
                row=index + 2,
            )
            for index, (uid, sku) in enumerate(sorted(product_map.items(), key=lambda kv: kv[1]))
        ]

        warnings = []
        if unknown_units:
            warnings.append(
                f"{unknown_units} analytics rows are for organisation units that are not in the "
                f"facility list at level {level}. Their consumption was not imported."
            )
        return demand, products, warnings

    # --- fetch ---------------------------------------------------------------------

    def fetch(self, *, include_demand: bool = True, limit: Optional[int] = None) -> FetchResult:
        with self.client() as client:
            nodes, warnings = self.fetch_facilities(client, limit=limit)
            demand: List[dict] = []
            products: List[dict] = []
            if include_demand:
                by_uid = {
                    node["external_ids"].get("dhis2_uid"): node["code"]
                    for node in nodes
                    if node["external_ids"].get("dhis2_uid")
                }
                demand, products, demand_warnings = self.fetch_consumption(client, by_uid)
                warnings.extend(demand_warnings)

        return FetchResult(
            nodes=nodes,
            products=products,
            demand=demand,
            warnings=warnings,
            stats={
                "facilities": len(nodes),
                "products": len(products),
                "demand_rows": len(demand),
                "facility_level": self.config.get("facility_level"),
                "period": self.config.get("analytics_period"),
            },
        )
