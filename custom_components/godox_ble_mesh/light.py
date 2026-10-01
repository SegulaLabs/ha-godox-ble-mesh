"""Light entities for ha Godox BLE Mesh.

Every entity here shares its config entry's one `GodoxMeshHub` connection --
turning on a light added fifth costs no extra Bluetooth connection, the same
way `godox_mesh.py on all` from the command line reaches every light through
whichever one it happened to connect to.

State is optimistic: the real Godox power/brightness/colour commands are
Telink vendor-opcode frames the light never acknowledges (confirmed against
the Godox app's own traffic logs -- see the README), so there is nothing to
read back after sending one. On startup only, this reads the *standard* mesh
Generic OnOff status (which lights do answer) to seed a sensible initial
on/off state; after that, each entity simply remembers what it last sent.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NODES, DOMAIN, MAX_KELVIN_DEFAULT, MIN_KELVIN_DEFAULT
from .hub import GodoxMeshError, GodoxMeshHub

_LOGGER = logging.getLogger(__name__)

DEFAULT_BRIGHTNESS_PCT = 100
DEFAULT_KELVIN = 4000


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: GodoxMeshHub = hass.data[DOMAIN][entry.entry_id]
    nodes = entry.options.get(CONF_NODES, [])
    async_add_entities(
        GodoxMeshLight(hub, entry.entry_id, node["name"], node["address"]) for node in nodes
    )


class GodoxMeshLight(LightEntity):
    """One Godox mesh node, controlled with the real Godox vendor protocol."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN_DEFAULT
    _attr_max_color_temp_kelvin = MAX_KELVIN_DEFAULT

    def __init__(self, hub: GodoxMeshHub, entry_id: str, name: str, address: int) -> None:
        self._hub = hub
        self._address = address
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}-{address:04x}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=name,
            manufacturer="Godox",
            model=f"Mesh node 0x{address:04X}",
        )
        self._attr_is_on: bool | None = None
        self._attr_brightness = round(DEFAULT_BRIGHTNESS_PCT * 255 / 100)
        self._attr_color_temp_kelvin = DEFAULT_KELVIN

    async def async_added_to_hass(self) -> None:
        try:
            state = await self._hub.async_read_onoff(self._address)
        except GodoxMeshError as err:
            _LOGGER.debug("could not read initial state for %s: %s", self.name, err)
            state = None
        if state is not None:
            self._attr_is_on = state
            self.async_write_ha_state()

    @property
    def _brightness_pct(self) -> float:
        return (self._attr_brightness or 255) * 100 / 255

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self._hub.async_send_power(self._address, True)
            if ATTR_BRIGHTNESS in kwargs:
                self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                self._attr_color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            if ATTR_BRIGHTNESS in kwargs or ATTR_COLOR_TEMP_KELVIN in kwargs:
                await self._hub.async_send_brightness_cct(
                    self._address, self._brightness_pct, self._attr_color_temp_kelvin
                )
        except GodoxMeshError as err:
            _LOGGER.error("could not turn on %s: %s", self.name, err)
            return
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._hub.async_send_power(self._address, False)
        except GodoxMeshError as err:
            _LOGGER.error("could not turn off %s: %s", self.name, err)
            return
        self._attr_is_on = False
        self.async_write_ha_state()
