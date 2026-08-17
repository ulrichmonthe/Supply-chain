"""mSupply connector.

In the Pacific this is the incumbent that matters. mSupply is PNG's national eLMIS,
used by MSPDB, and it is where PNG's demand and consumption data actually lives. A
tool that cannot read it has no PNG product.

**Two mSupplys, and the difference decides your integration.**

*Open mSupply* — the current open-source product, deployed in South Sudan and rolling
out elsewhere — exposes a GraphQL API, and that is what this connector speaks.

*Legacy mSupply* — the long-standing commercial desktop and server product, which is
what PNG runs — does not expose that API. There is no endpoint here to point at it.
The realistic paths are a scheduled export handled through this tool's Excel importer,
or a direct feed agreed with Beyond Essential Systems. This connector says so rather
than implying a live link that does not exist, because being caught overstating an
integration in front of MSPDB would cost more than the integration is worth.

Every GraphQL document below is a configurable string. The Open mSupply schema is
still moving, and a country should be able to correct a field name in a form rather
than wait for a release.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .base import (
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
from .openlmis import dig

AUTH_DOCUMENT = """
mutation ($username: String!, $password: String!) {
  authToken(username: $username, password: $password) {
    ... on AuthToken { token }
    ... on AuthTokenError { error { description } }
  }
}
""".strip()

STORES_DOCUMENT = """
query {
  stores {
    ... on StoreConnector { totalCount nodes { id code storeName } }
  }
}
""".strip()

FACILITIES_DOCUMENT = """
query ($storeId: String!, $first: Int!, $offset: Int!) {
  names(storeId: $storeId, page: { first: $first, offset: $offset }) {
    ... on NameConnector {
      totalCount
      nodes { id code name isCustomer isSupplier isStore }
    }
  }
}
""".strip()

ITEMS_DOCUMENT = """
query ($storeId: String!, $first: Int!, $offset: Int!) {
  items(storeId: $storeId, page: { first: $first, offset: $offset }) {
    ... on ItemConnector {
      totalCount
      nodes { id code name unitName }
    }
  }
}
""".strip()


class MSupplyConnector(Connector):
    system = "msupply"
    label = "Open mSupply"
    description = (
        "Reads facilities and items from an Open mSupply server over GraphQL. Legacy "
        "mSupply — including PNG's national deployment — does not expose this API; use "
        "the Excel importer or agree an export feed with Beyond Essential Systems."
    )
    docs_url = "https://github.com/openmsupply/open-msupply"
    auth_types = ["basic", "bearer"]
    verified_against_live_instance = False

    config_spec = {
        "graphql_path": {"default": "/graphql", "help": "GraphQL endpoint path."},
        "store_id": {
            "default": "",
            "help": "Store to read from. Leave blank to use the first store the account can "
            "see; the connection test lists what is available.",
        },
        "page_size": {"default": 500, "kind": "number", "help": "Records per GraphQL page."},
        "auth_document": {"default": AUTH_DOCUMENT, "kind": "graphql", "help": "Login mutation."},
        "stores_document": {"default": STORES_DOCUMENT, "kind": "graphql", "help": "Store list query."},
        "facilities_document": {
            "default": FACILITIES_DOCUMENT,
            "kind": "graphql",
            "help": "Facility (name) list query. Must accept $storeId, $first and $offset.",
        },
        "items_document": {
            "default": ITEMS_DOCUMENT,
            "kind": "graphql",
            "help": "Item catalogue query. Must accept $storeId, $first and $offset.",
        },
        "facilities_nodes_key": {
            "default": "data.names.nodes",
            "help": "Dotted path to the facility array in the response.",
        },
        "items_nodes_key": {
            "default": "data.items.nodes",
            "help": "Dotted path to the item array in the response.",
        },
        "stores_nodes_key": {"default": "data.stores.nodes", "help": "Dotted path to the store array."},
        "exclude_stores": {
            "default": True,
            "kind": "boolean",
            "help": "mSupply models warehouses and customers in one 'names' table. On by "
            "default so warehouses do not arrive as health facilities.",
        },
        "consumption_document": {
            "default": "",
            "kind": "graphql",
            "help": "Optional query returning consumption rows. Blank imports facilities and "
            "items only.",
        },
        "consumption_nodes_key": {"default": "data.invoiceLines.nodes", "help": "Path to the rows."},
        "consumption_facility_key": {"default": "invoice.otherPartyStore.code", "help": "Facility code path."},
        "consumption_product_key": {"default": "item.code", "help": "Item code path."},
        "consumption_quantity_key": {"default": "numberOfPacks", "help": "Quantity path."},
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token: Optional[str] = None

    def _auth(self):
        """No client-level auth.

        The username and password are exchanged for a token through a GraphQL
        mutation, and every later request carries that token as a Bearer header.
        Leaving httpx's BasicAuth attached would overwrite that header on the way
        out, and every authenticated query would come back "Not authenticated".
        """
        return None

    # --- graphql ---------------------------------------------------------------------

    def _graphql(
        self,
        client: httpx.Client,
        document: str,
        *,
        step: str,
        variables: Optional[dict] = None,
        authenticated: bool = True,
    ) -> dict:
        headers = {}
        if authenticated:
            token = self._authenticate(client)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            response = client.post(
                str(self.config.get("graphql_path")),
                json={"query": document, "variables": variables or {}},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Could not reach the Open mSupply GraphQL endpoint at {self.base_url}.",
                system=self.system,
                step=step,
                hint="Check the base URL. If this is a legacy mSupply server there is no "
                "GraphQL API to reach — use the Excel importer instead.",
            ) from exc

        payload = self._decode(response, step=step)
        # GraphQL answers 200 with an errors array, so a status check is not enough.
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors[:3])
            raise ConnectorError(
                f"Open mSupply rejected the query: {messages}",
                system=self.system,
                step=step,
                hint="The Open mSupply schema changes between releases. Every query on this "
                "connection is editable — correct the field name there.",
            )
        return payload if isinstance(payload, dict) else {}

    def _authenticate(self, client: httpx.Client) -> Optional[str]:
        if self.auth_type == "bearer":
            return self.secret or None
        if self._token:
            return self._token

        payload = self._graphql(
            client,
            str(self.config.get("auth_document")),
            step="authentication",
            variables={"username": self.username, "password": self.secret},
            authenticated=False,
        )
        token = dig(payload, "data.authToken.token")
        if not token:
            described = dig(payload, "data.authToken.error.description") or "no token returned"
            raise ConnectorError(
                f"Open mSupply refused the credentials: {described}",
                system=self.system,
                step="authentication",
            )
        self._token = str(token)
        return self._token

    def _resolve_store_id(self, client: httpx.Client) -> str:
        configured = str(self.config.get("store_id") or "").strip()
        if configured:
            return configured
        payload = self._graphql(client, str(self.config.get("stores_document")), step="stores")
        stores = dig(payload, str(self.config.get("stores_nodes_key")), []) or []
        if not stores:
            raise ConnectorError(
                "This account can see no stores, so there is nothing to read from.",
                system=self.system,
                step="stores",
                hint="Grant the account access to a store, or set store_id explicitly.",
            )
        return str(stores[0].get("id"))

    def _paged_nodes(
        self, client: httpx.Client, document: str, nodes_key: str, store_id: str, *, step: str,
        limit: Optional[int] = None,
    ):
        page_size = int(self.config.get("page_size") or 500)
        offset = 0
        seen = 0
        while True:
            payload = self._graphql(
                client, document, step=step,
                variables={"storeId": store_id, "first": page_size, "offset": offset},
            )
            rows = dig(payload, nodes_key, []) or []
            if not rows:
                break
            for row in rows:
                yield row
                seen += 1
                if (limit and seen >= limit) or seen >= MAX_RECORDS:
                    return
            if len(rows) < page_size:
                break
            offset += page_size

    # --- test ------------------------------------------------------------------------

    def test(self) -> ConnectionInfo:
        checks: List[Check] = []
        with self.client() as client:
            try:
                self._authenticate(client)
                checks.append(Check("Authenticated", True, "Token issued."))
            except ConnectorError as exc:
                checks.append(Check("Authenticated", False, exc.full()))
                return ConnectionInfo(False, self.system, "", exc.full(), checks)

            store_id = ""
            try:
                payload = self._graphql(client, str(self.config.get("stores_document")), step="stores")
                stores = dig(payload, str(self.config.get("stores_nodes_key")), []) or []
                store_id = self._resolve_store_id(client)
                listed = ", ".join(f"{s.get('storeName')} ({s.get('code')})" for s in stores[:5])
                checks.append(Check("Can list stores", True, f"{len(stores)} visible: {listed}"))
            except ConnectorError as exc:
                checks.append(Check("Can list stores", False, exc.full()))

            if store_id:
                try:
                    rows = list(
                        self._paged_nodes(
                            client, str(self.config.get("facilities_document")),
                            str(self.config.get("facilities_nodes_key")), store_id,
                            step="facilities", limit=1,
                        )
                    )
                    checks.append(
                        Check(
                            "Can read facilities",
                            True,
                            f"First is '{rows[0].get('name')}'" if rows else "The store has no names.",
                        )
                    )
                except ConnectorError as exc:
                    checks.append(Check("Can read facilities", False, exc.full()))

            checks.append(
                Check(
                    "Server flavour",
                    True,
                    "Speaking to an Open mSupply GraphQL server. Legacy mSupply — including "
                    "PNG's national deployment — has no such API; that data comes in through "
                    "the Excel importer.",
                )
            )

        ok = all(check.ok for check in checks[:3])
        return ConnectionInfo(ok, self.system, "open-msupply", "Ready" if ok else "Connected with problems.", checks)

    # --- fetch ---------------------------------------------------------------------------

    def fetch(self, *, include_demand: bool = True, limit: Optional[int] = None) -> FetchResult:
        nodes: List[dict] = []
        products: List[dict] = []
        demand: List[dict] = []
        warnings: List[str] = []
        excluded_stores = 0

        with self.client() as client:
            store_id = self._resolve_store_id(client)

            for row in self._paged_nodes(
                client, str(self.config.get("facilities_document")),
                str(self.config.get("facilities_nodes_key")), store_id, step="facilities", limit=limit,
            ):
                if self.config.get("exclude_stores") and row.get("isStore"):
                    excluded_stores += 1
                    continue
                code = str(row.get("code") or "").strip()
                name = str(row.get("name") or "").strip()
                if not code or not name:
                    warnings.append(f"Skipped a name record with no code or name: {row!r:.120}")
                    continue
                nodes.append(
                    make_node(
                        code=code,
                        name=name,
                        # mSupply's name table carries no coordinates. Importing without one
                        # is correct: the validator then lists the facility for geocoding
                        # rather than the tool inventing a location.
                        lat=None,
                        lon=None,
                        external_ids={"msupply_id": str(row.get("id") or ""), "mfl_code": code},
                        row=len(nodes) + 2,
                        geocode_source="msupply",
                        geocode_confidence=0.0,
                    )
                )

            for row in self._paged_nodes(
                client, str(self.config.get("items_document")),
                str(self.config.get("items_nodes_key")), store_id, step="items",
            ):
                sku = str(row.get("code") or "").strip()
                if not sku:
                    continue
                products.append(
                    make_product(sku=sku, name=str(row.get("name") or sku).strip(), row=len(products) + 2)
                )

            document = str(self.config.get("consumption_document") or "").strip()
            if include_demand and document:
                known = {n["code"] for n in nodes}
                totals: Dict[tuple, float] = {}
                unknown = 0
                for row in self._paged_nodes(
                    client, document, str(self.config.get("consumption_nodes_key")), store_id,
                    step="consumption",
                ):
                    facility = dig(row, str(self.config.get("consumption_facility_key")))
                    product = dig(row, str(self.config.get("consumption_product_key")))
                    quantity = coerce_float(dig(row, str(self.config.get("consumption_quantity_key"))))
                    if facility is None or product is None or quantity is None:
                        continue
                    if str(facility) not in known:
                        unknown += 1
                        continue
                    key = (str(facility), str(product))
                    totals[key] = totals.get(key, 0.0) + quantity
                demand = [
                    make_demand(node_code=node, sku=sku, quantity=round(total, 3), row=index + 2)
                    for index, ((node, sku), total) in enumerate(sorted(totals.items()))
                ]
                if unknown:
                    warnings.append(f"{unknown} consumption rows are for facilities not in the import.")

        if nodes:
            warnings.append(
                f"All {len(nodes)} facilities were imported without coordinates, because "
                f"mSupply does not hold them. Match them against the DHIS2 facility list or "
                f"the master facility list before anything is put on a map."
            )
        if excluded_stores:
            warnings.append(f"{excluded_stores} records were warehouses rather than facilities and were skipped.")
        if products:
            warnings.append(
                f"{len(products)} items imported without a packed volume, which mSupply does not "
                f"carry. The model is volumetric and the validator will say so."
            )

        return FetchResult(
            nodes=nodes,
            products=products,
            demand=demand,
            warnings=warnings,
            stats={"facilities": len(nodes), "products": len(products), "demand_rows": len(demand)},
        )
