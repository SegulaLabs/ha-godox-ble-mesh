"""Parse the Godox app's own mesh export so nobody configuring this
integration has to copy hex keys or convert addresses by hand.

The export is the Bluetooth Mesh "CDB" JSON document already described in
docs/HOW-THIS-WORKS.md -- the same `meshJson` blob pulled out of the Godox
app's own `GodoxLight.db`. Pasting the whole thing here does everything the
manual "Network key" / "App key" / per-light "Mesh address" fields used to
require by hand.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# Bluetooth SIG company id Godox registers its lights under. The phone app's
# own provisioner entry in this same JSON has no "cid" at all, and any other
# vendor's mesh device sharing the network would carry a different one -- so
# this is what actually separates "a Godox light" from noise in the export,
# not the node's name (which is just whatever the installer typed in-app).
GODOX_COMPANY_ID = "0211"


@dataclass
class DiscoveredNode:
    name: str
    address: int


@dataclass
class MeshImport:
    network_key: str
    app_key: str
    nodes: list[DiscoveredNode]


class MeshImportError(Exception):
    """The pasted text isn't a usable Godox mesh export."""


def parse_mesh_export(text: str) -> MeshImport:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise MeshImportError("not_json") from err

    try:
        network_key = data["netKeys"][0]["key"].strip().lower()
        app_key = data["appKeys"][0]["key"].strip().lower()
    except (KeyError, IndexError, TypeError, AttributeError) as err:
        raise MeshImportError("missing_keys") from err

    if len(network_key) != 32 or len(app_key) != 32:
        raise MeshImportError("missing_keys")

    nodes: list[DiscoveredNode] = []
    for node in data.get("nodes", []):
        if str(node.get("cid", "")).upper() != GODOX_COMPANY_ID:
            continue  # the app's own provisioner entry, or some other vendor's node
        try:
            address = int(node["unicastAddress"], 16)
        except (KeyError, TypeError, ValueError):
            continue
        name = node.get("name") or f"Godox light 0x{address:04X}"
        nodes.append(DiscoveredNode(name=name, address=address))

    if not nodes:
        raise MeshImportError("no_lights_found")

    return MeshImport(network_key=network_key, app_key=app_key, nodes=nodes)
