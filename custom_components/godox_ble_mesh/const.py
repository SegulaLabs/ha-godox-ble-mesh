"""Constants for the Godox BLE Mesh (Direct) integration."""

DOMAIN = "godox_ble_mesh"

CONF_NETWORK_KEY = "network_key"
CONF_APP_KEY = "app_key"
CONF_PROVISIONER_ADDRESS = "provisioner_address"
CONF_NODE_ADDRESS = "node_address"
CONF_NODES = "nodes"  # list of {"name": str, "address": int}

DEFAULT_PROVISIONER_ADDRESS = 0x0500  # pick one not used by the Godox app (0x0100)
                                       # or any other controller on this network

# These lights silently drop messages whose sequence number is at or below one
# they've already seen (standard Bluetooth Mesh replay protection) -- there is
# no fixed "safe" number, since it only ever goes up with real traffic from a
# given source address. The config flow probes for the real current value
# automatically; this is only the very first rung of that search.
SEQUENCE_PROBE_START = 0x10
SEQUENCE_PROBE_STEPS = (
    0x40000, 0x50000, 0x60000, 0x80000, 0xA0000, 0xC0000,
    0x100000, 0x180000, 0x200000, 0x300000, 0x500000,
)
SEQUENCE_SAFETY_MARGIN = 500_000  # headroom added on top of whatever probing finds

MIN_KELVIN_DEFAULT = 2700
MAX_KELVIN_DEFAULT = 6500

STORAGE_VERSION = 1
