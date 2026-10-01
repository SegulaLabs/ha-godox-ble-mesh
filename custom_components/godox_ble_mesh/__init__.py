"""Godox BLE Mesh (Direct) -- control Godox Bluetooth Mesh lights without
re-provisioning them away from the Godox phone app.

One config entry is one mesh network and holds exactly one shared Bluetooth
connection (a `GodoxMeshHub`); every light added under it (options flow) is
reached through that same connection, the way Bluetooth Mesh is designed to
work. See the project README for the full story of how the keys were
extracted and why this exists instead of the community `ha-godox-mesh`.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_APP_KEY, CONF_NETWORK_KEY, CONF_PROVISIONER_ADDRESS, DOMAIN
from .hub import GodoxMeshHub, HubSettings

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["light"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = GodoxMeshHub(
        hass,
        entry.entry_id,
        HubSettings(
            network_key=entry.data[CONF_NETWORK_KEY],
            app_key=entry.data[CONF_APP_KEY],
            provisioner_address=entry.data[CONF_PROVISIONER_ADDRESS],
        ),
    )
    await hub.async_load()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hub: GodoxMeshHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options (lights added/removed, sequence re-probed) changed -- reload."""
    await hass.config_entries.async_reload(entry.entry_id)
