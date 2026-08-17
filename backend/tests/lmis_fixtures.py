"""Mock LMIS servers speaking each system's real payload shapes.

These are not stubs that return whatever the connector wants. They reproduce the
awkward parts of each API on purpose, because the awkward parts are where a
connector breaks in the field:

* DHIS2 pages with a ``pager`` block and nests parents inside parents, puts
  coordinates in ``geometry`` on new versions and a JSON *string* on old ones, and
  answers analytics as a headers-and-rows table rather than objects.
* OpenLMIS wants an OAuth2 password grant authorised by a *separate* client
  credential, then pages Spring-style with ``content`` and ``totalPages``.
* GraphQL answers 200 with an ``errors`` array, so a status check proves nothing.

The facility payloads are built from the real seeded PNG network so reconciliation is
exercised against genuine matches: some facilities line up by code, one has moved, one
was renamed, one is new, one has no coordinate, and one is in the Bismarck Sea.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import httpx

# --- the shared scenario ------------------------------------------------------------
# (uid, code, name, lat, lon, note)
PNG_FACILITIES = [
    ("dhisUID0001", "PNG-NCD-001", "Port Moresby General Hospital", -9.4747, 147.1925, "exact match"),
    ("dhisUID0002", "PNG-MOR-001", "Angau Memorial Hospital", -6.7300, 146.9950, "exact match"),
    ("dhisUID0003", "PNG-MAD-001", "Modilon General Hospital", -5.2246, 145.7981, "exact match"),
    # Renamed upstream: same code, different name.
    ("dhisUID0004", "PNG-EHP-001", "Goroka Base Hospital", -6.0833, 145.3833, "renamed"),
    # Moved: a better geocode, about 3 km from where the model has it.
    ("dhisUID0005", "PNG-NIP-001", "Kavieng General Hospital", -2.6000, 150.8100, "moved ~3 km"),
    # No coordinate at all: must import and be flagged, not silently dropped.
    ("dhisUID0006", "PNG-MBP-002", "Losuia Health Centre", None, None, "no coordinate"),
    # Genuinely new facility, not in the model.
    ("dhisUID0007", "PNG-NEW-001", "Tabubil Rural Clinic", -5.2700, 141.2300, "new"),
    # In open water. The validator must reject this whatever system it came from.
    ("dhisUID0008", "PNG-BAD-001", "Bismarck Sea Health Post", -3.5000, 149.0000, "offshore"),
]

CONSUMPTION = [
    ("PNG-NCD-001", "ESSMED-KIT", 5200.0),
    ("PNG-MOR-001", "ESSMED-KIT", 5800.0),
    ("PNG-MAD-001", "VAC-EPI", 16800.0),
    ("PNG-EHP-001", "ESSMED-KIT", 3350.0),
]

DHIS2_DATA_ELEMENTS = {"dxESSMED001": "ESSMED-KIT", "dxVACEPI001": "VAC-EPI"}


def _dhis2_org_unit(uid, code, name, lat, lon, *, legacy_coordinates=False) -> dict:
    unit = {
        "id": uid,
        "code": code,
        "name": name,
        "level": 4,
        "parent": {
            "id": "distUID01",
            "name": f"{name.split()[0]} District",
            "parent": {"id": "provUID01", "name": f"{name.split()[0]} Province"},
        },
    }
    if lat is not None and lon is not None:
        if legacy_coordinates:
            # DHIS2 before 2.32: a JSON array in a string, longitude first.
            unit["coordinates"] = json.dumps([lon, lat])
        else:
            unit["geometry"] = {"type": "Point", "coordinates": [lon, lat]}
    return unit


def dhis2_transport(
    *,
    legacy_coordinates: bool = False,
    page_size_honoured: int = 3,
    fail_analytics: bool = False,
    unauthorised: bool = False,
    facilities: Optional[List] = None,
) -> httpx.MockTransport:
    rows = facilities if facilities is not None else PNG_FACILITIES

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params

        if unauthorised:
            return httpx.Response(401, json={"message": "Unauthorised"})

        if path == "/api/system/info":
            return httpx.Response(200, json={"version": "2.41.1", "contextPath": str(request.url.copy_with(path='', query=None))})

        if path == "/api/organisationUnitLevels":
            return httpx.Response(
                200,
                json={
                    "organisationUnitLevels": [
                        {"level": 1, "name": "National"},
                        {"level": 2, "name": "Province"},
                        {"level": 3, "name": "District"},
                        {"level": 4, "name": "Facility"},
                    ]
                },
            )

        if path == "/api/organisationUnits":
            page = int(params.get("page", 1))
            requested = int(params.get("pageSize", page_size_honoured))
            size = min(requested, page_size_honoured)
            start = (page - 1) * size
            chunk = rows[start : start + size]
            page_count = max(1, -(-len(rows) // size))
            return httpx.Response(
                200,
                json={
                    "pager": {"page": page, "pageCount": page_count, "total": len(rows), "pageSize": size},
                    "organisationUnits": [
                        _dhis2_org_unit(*row[:5], legacy_coordinates=legacy_coordinates) for row in chunk
                    ],
                },
            )

        if path == "/api/analytics.json":
            if fail_analytics:
                return httpx.Response(409, text="Analytics tables have not been generated")
            uid_by_code = {row[1]: row[0] for row in rows}
            sku_to_dx = {v: k for k, v in DHIS2_DATA_ELEMENTS.items()}
            data_rows = []
            for code, sku, quantity in CONSUMPTION:
                if code not in uid_by_code:
                    continue
                # Two periods, to prove the connector sums rather than takes the last.
                data_rows.append([sku_to_dx[sku], "202501", uid_by_code[code], str(quantity * 0.4)])
                data_rows.append([sku_to_dx[sku], "202502", uid_by_code[code], str(quantity * 0.6)])
            # A row for an org unit outside the facility level, which must be reported.
            data_rows.append(["dxESSMED001", "202501", "ghostUID999", "42"])
            return httpx.Response(
                200,
                json={
                    "headers": [
                        {"name": "dx"}, {"name": "pe"}, {"name": "ou"}, {"name": "value"},
                    ],
                    "metaData": {
                        "items": {
                            "dxESSMED001": {"name": "Essential medicines kit issued"},
                            "dxVACEPI001": {"name": "EPI vaccine doses issued"},
                        }
                    },
                    "rows": data_rows,
                },
            )

        return httpx.Response(404, json={"message": f"no route for {path}"})

    return httpx.MockTransport(handler)


# --- OpenLMIS -----------------------------------------------------------------------------


def openlmis_transport(
    *, bad_client_secret: bool = False, with_consumption: bool = True, page_size: int = 4
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params

        if path == "/api/oauth/token":
            if bad_client_secret:
                # OpenLMIS answers 200 with no token when the *client* credential is
                # wrong, which is why the connector checks the body rather than status.
                return httpx.Response(200, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 1800})

        if not (request.headers.get("Authorization") or params.get("access_token")):
            return httpx.Response(401, json={"message": "unauthenticated"})

        if path == "/api/facilities":
            page = int(params.get("page", 0))
            size = min(int(params.get("size", page_size)), page_size)
            content = []
            for uid, code, name, lat, lon, _ in PNG_FACILITIES:
                entry = {
                    "id": uid,
                    "code": code,
                    "name": name,
                    "active": True,
                    "enabled": True,
                    "type": {"code": "health_centre", "name": "Health Centre"},
                    "geographicZone": {
                        "code": "ZONE-01",
                        "name": f"{name.split()[0]} District",
                        "parent": {"code": "PROV-01", "name": f"{name.split()[0]} Province"},
                    },
                }
                if lat is not None:
                    entry["location"] = {"type": "Point", "coordinates": [lon, lat]}
                content.append(entry)
            # One inactive facility, which must be skipped by default.
            content.append(
                {"id": "olmisX", "code": "PNG-OFF-001", "name": "Closed Aid Post", "active": False,
                 "enabled": False, "type": {"code": "health_centre"}}
            )
            start = page * size
            chunk = content[start : start + size]
            return httpx.Response(
                200,
                json={
                    "content": chunk,
                    "totalElements": len(content),
                    "totalPages": max(1, -(-len(content) // size)),
                    "number": page,
                },
            )

        if path == "/api/orderables":
            page = int(params.get("page", 0))
            content = [
                {"id": "ord1", "productCode": "ESSMED-KIT", "fullProductName": "Essential medicines kit"},
                {"id": "ord2", "productCode": "VAC-EPI", "fullProductName": "Routine EPI vaccine carton"},
            ]
            if page > 0:
                content = []
            return httpx.Response(
                200, json={"content": content, "totalElements": 2, "totalPages": 1, "number": page}
            )

        if path == "/api/consumption":
            if not with_consumption:
                return httpx.Response(404, json={"message": "not deployed"})
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "facility": {"code": code},
                            "orderable": {"productCode": sku},
                            "totalConsumedQuantity": quantity,
                        }
                        for code, sku, quantity in CONSUMPTION
                    ]
                    + [
                        # A row for a facility that was filtered out, which must be reported.
                        {
                            "facility": {"code": "PNG-OFF-001"},
                            "orderable": {"productCode": "ESSMED-KIT"},
                            "totalConsumedQuantity": 10,
                        }
                    ]
                },
            )

        return httpx.Response(404, json={"message": f"no route for {path}"})

    return httpx.MockTransport(handler)


# --- Open mSupply -----------------------------------------------------------------------------


def msupply_transport(
    *, bad_password: bool = False, schema_drift: bool = False, page_size: int = 4
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/graphql":
            return httpx.Response(404, json={"message": "not graphql"})

        body = json.loads(request.content.decode() or "{}")
        document = body.get("query", "")
        variables = body.get("variables") or {}

        if "authToken" in document:
            if bad_password:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "authToken": {"error": {"description": "Incorrect username or password"}}
                        }
                    },
                )
            return httpx.Response(200, json={"data": {"authToken": {"token": "msupply-token"}}})

        if request.headers.get("Authorization") != "Bearer msupply-token":
            return httpx.Response(200, json={"errors": [{"message": "Not authenticated"}]})

        if "stores" in document:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "stores": {
                            "totalCount": 1,
                            "nodes": [{"id": "store-1", "code": "NMS", "storeName": "Badili National Medical Store"}],
                        }
                    }
                },
            )

        if "names(" in document:
            if schema_drift:
                # What a schema change actually looks like: 200 with an errors array.
                return httpx.Response(
                    200,
                    json={"errors": [{"message": 'Unknown field "isStore" on type "NameNode"'}]},
                )
            offset = int(variables.get("offset", 0))
            first = min(int(variables.get("first", page_size)), page_size)
            nodes = [
                {"id": uid, "code": code, "name": name, "isStore": False, "isCustomer": True}
                for uid, code, name, _, _, _ in PNG_FACILITIES
            ]
            nodes.append(
                {"id": "store-1", "code": "NMS", "name": "Badili National Medical Store", "isStore": True}
            )
            chunk = nodes[offset : offset + first]
            return httpx.Response(200, json={"data": {"names": {"totalCount": len(nodes), "nodes": chunk}}})

        if "items(" in document:
            offset = int(variables.get("offset", 0))
            nodes = [
                {"id": "item-1", "code": "ESSMED-KIT", "name": "Essential medicines kit", "unitName": "kit"},
                {"id": "item-2", "code": "VAC-EPI", "name": "Routine EPI vaccine carton", "unitName": "carton"},
            ]
            chunk = nodes[offset : offset + int(variables.get("first", page_size))]
            return httpx.Response(200, json={"data": {"items": {"totalCount": len(nodes), "nodes": chunk}}})

        return httpx.Response(200, json={"errors": [{"message": f"unhandled document: {document[:60]}"}]})

    return httpx.MockTransport(handler)


def unreachable_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    return httpx.MockTransport(handler)


def html_login_page_transport() -> httpx.MockTransport:
    """The base URL points at the web interface rather than the API."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html><body>Please log in</body></html>")

    return httpx.MockTransport(handler)
