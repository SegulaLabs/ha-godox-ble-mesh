"""Config flow for Godox BLE Mesh (Direct).

One config entry = one Godox mesh network (the keys you already pulled out of
the Godox app's own database -- see this project's README for how). Lights are
added afterwards, by address only, through the options flow ("Configure" on
the entry) -- no device key, no pairing mode, no re-provisioning. Every light
added this way shares the single Bluetooth connection this entry's hub holds
open, the same way the standalone `godox_mesh.py` script's `on all` reaches
every light through whichever one it happened to connect to.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_APP_KEY,
    CONF_NETWORK_KEY,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_PROVISIONER_ADDRESS,
    DEFAULT_PROVISIONER_ADDRESS,
    DOMAIN,
    SEQUENCE_SAFETY_MARGIN,
)
from .hub import GodoxMeshError, GodoxMeshHub, HubSettings
from .meshcrypto import NetKey

_LOGGER = logging.getLogger(__name__)

_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _is_hex32(value: str) -> bool:
    return bool(_HEX32_RE.match(value))


def _mesh_keys_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    # Plain `str` here, not a vol.Match validator: Home Assistant's frontend
    # serializer (voluptuous_serialize) cannot turn a raw regex validator into
    # a form field and raises a 500 trying -- the hex-format check is done by
    # hand in async_step_user instead, after submission.
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NETWORK_KEY, default=defaults.get(CONF_NETWORK_KEY, "")): str,
            vol.Required(CONF_APP_KEY, default=defaults.get(CONF_APP_KEY, "")): str,
            vol.Required(
                CONF_PROVISIONER_ADDRESS,
                default=defaults.get(CONF_PROVISIONER_ADDRESS, DEFAULT_PROVISIONER_ADDRESS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=0x7FFF)),
        }
    )


class GodoxBleMeshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one Godox mesh network."""

    VERSION = 1

    def __init__(self) -> None:
        self._mesh_input: dict[str, Any] = {}
        self._found_sequence: int | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            network_key = user_input[CONF_NETWORK_KEY].strip().lower()
            app_key = user_input[CONF_APP_KEY].strip().lower()
            if not _is_hex32(network_key):
                errors[CONF_NETWORK_KEY] = "invalid_key"
            if not _is_hex32(app_key):
                errors[CONF_APP_KEY] = "invalid_key"
            if not errors:
                user_input = {**user_input, CONF_NETWORK_KEY: network_key, CONF_APP_KEY: app_key}
                network_id = NetKey(network_key).network_id.hex()
                await self.async_set_unique_id(network_id)
                self._abort_if_unique_id_configured()
                self._mesh_input = user_input
                return await self.async_step_probe()

        return self.async_show_form(
            step_id="user", data_schema=_mesh_keys_schema(user_input), errors=errors,
        )

    async def async_step_probe(self, _user_input: dict[str, Any] | None = None) -> Any:
        """Connect to a light and find the sequence number this network currently
        accepts, so nobody has to do this step by hand with a laptop script."""
        hub = GodoxMeshHub(
            self.hass,
            "setup-probe",
            HubSettings(
                network_key=self._mesh_input[CONF_NETWORK_KEY],
                app_key=self._mesh_input[CONF_APP_KEY],
                provisioner_address=self._mesh_input[CONF_PROVISIONER_ADDRESS],
            ),
        )
        try:
            found = await hub.async_probe_sequence_number()
        except GodoxMeshError as err:
            _LOGGER.warning("sequence probe failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=_mesh_keys_schema(self._mesh_input),
                errors={"base": "no_light_found"},
            )
        finally:
            await hub.async_shutdown()

        self._found_sequence = found + SEQUENCE_SAFETY_MARGIN
        return self.async_create_entry(
            title="Godox mesh",
            data={
                CONF_NETWORK_KEY: self._mesh_input[CONF_NETWORK_KEY],
                CONF_APP_KEY: self._mesh_input[CONF_APP_KEY],
                CONF_PROVISIONER_ADDRESS: self._mesh_input[CONF_PROVISIONER_ADDRESS],
            },
            options={"sequence_number": self._found_sequence, CONF_NODES: []},
            description_placeholders={"sequence_number": str(self._found_sequence)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GodoxBleMeshOptionsFlow:
        # No config_entry argument: GodoxBleMeshOptionsFlow takes none of its
        # own anymore -- the base OptionsFlow class populates its read-only
        # `config_entry` property itself once this flow is registered.
        return GodoxBleMeshOptionsFlow()


class GodoxBleMeshOptionsFlow(OptionsFlow):
    """Add or remove lights on this mesh -- by address only, no device key.

    No __init__/config_entry assignment here: newer Home Assistant versions
    already expose `config_entry` as a read-only property on OptionsFlow,
    populated by the framework itself -- assigning it ourselves raises
    `AttributeError: property 'config_entry' has no setter`.
    """

    async def async_step_init(self, _user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_menu(step_id="init", menu_options=["add_light", "remove_light", "reprobe"])

    async def async_step_add_light(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            nodes = list(self.config_entry.options.get(CONF_NODES, []))
            address = user_input[CONF_NODE_ADDRESS]
            if any(n["address"] == address for n in nodes):
                errors["base"] = "address_already_added"
            else:
                nodes.append({"name": user_input["name"], "address": address})
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, CONF_NODES: nodes}
                )

        return self.async_show_form(
            step_id="add_light",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required(CONF_NODE_ADDRESS): vol.All(vol.Coerce(int), vol.Range(min=1, max=0x7FFF)),
                }
            ),
            errors=errors,
            description_placeholders={
                "hint": "Mesh unicast address in decimal -- e.g. 0x0115 is 277. "
                        "Pull this from the Godox app's own export; see the project README."
            },
        )

    async def async_step_remove_light(self, user_input: dict[str, Any] | None = None) -> Any:
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        if not nodes:
            return self.async_abort(reason="no_lights_configured")
        if user_input is not None:
            address = int(user_input[CONF_NODE_ADDRESS])
            nodes = [n for n in nodes if n["address"] != address]
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_NODES: nodes}
            )
        return self.async_show_form(
            step_id="remove_light",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NODE_ADDRESS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=str(n["address"]), label=f"{n['name']} (0x{n['address']:04X})"
                                )
                                for n in nodes
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_reprobe(self, _user_input: dict[str, Any] | None = None) -> Any:
        """Re-run the sequence-number probe without re-entering the keys --
        use this if lights stop responding after heavy testing/experimentation
        has pushed this network's floor for our address higher (see README)."""
        hub = GodoxMeshHub(
            self.hass,
            self.config_entry.entry_id,
            HubSettings(
                network_key=self.config_entry.data[CONF_NETWORK_KEY],
                app_key=self.config_entry.data[CONF_APP_KEY],
                provisioner_address=self.config_entry.data[CONF_PROVISIONER_ADDRESS],
            ),
        )
        try:
            found = await hub.async_probe_sequence_number()
        except GodoxMeshError:
            return self.async_abort(reason="no_light_found")
        finally:
            await hub.async_shutdown()
        new_seq = found + SEQUENCE_SAFETY_MARGIN
        return self.async_create_entry(
            title="", data={**self.config_entry.options, "sequence_number": new_seq}
        )
