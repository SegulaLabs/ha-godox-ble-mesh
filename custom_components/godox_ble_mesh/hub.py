"""One shared Bluetooth Mesh connection for an entire Godox mesh network.

This is the Home Assistant port of the logic already proven working in this
project's standalone `godox_mesh.py` script: connect to *any one* proxy-capable
light on the mesh, and every other light on the same network is reachable
through that single connection by address -- that is the whole point of
Bluetooth Mesh. One `GodoxMeshHub` instance (one per config entry) is shared by
every light entity on that network, so N lights cost one Bluetooth connection,
not N.

Unlike the community `ha-godox-mesh` integration, this hub needs no device key
per light and no BLE Mesh *provisioning* step at all: every message this
project sends -- the standard Generic OnOff status read and the real Godox
vendor power/brightness/colour commands -- is encrypted with the network-wide
AppKey, never a per-node device key. Device keys are only needed for Config
messages (binding app keys, changing addresses), which this hub never sends.
That also means adding a light here never touches its pairing with the Godox
phone app.

Gateway selection (picking which physical light to connect through, and
failing over to another one if it drops) follows the same general approach as
the `ha-godox-mesh` project's `gateway.py` (MIT licensed, github.com/
binary-person/ha-godox-mesh): prefer the currently-connected node, otherwise
the strongest signal, and exclude a node that just failed to connect until it
advertises again. The implementation below is written from scratch for this
project, not copied, but the credit for the approach belongs there.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import SEQUENCE_PROBE_START, SEQUENCE_PROBE_STEPS, SEQUENCE_SAFETY_MARGIN, STORAGE_VERSION
from .meshcrypto import (
    AppKey,
    NetKey,
    cmac,
    decrypt_access,
    decrypt_network_pdu,
    encrypt_access,
    encrypt_network_pdu,
)

_LOGGER = logging.getLogger(__name__)

PROXY_SVC_UUID = "00001828-0000-1000-8000-00805f9b34fb"
DATA_IN_UUID = "00002add-0000-1000-8000-00805f9b34fb"
DATA_OUT_UUID = "00002ade-0000-1000-8000-00805f9b34fb"
PROV_OUT_UUID = "00002adc-0000-1000-8000-00805f9b34fb"

ALL_NODES = 0xFFFF
GODOX_OPCODE = bytes([0xF0, 0x11, 0x02])  # vendor opcode 0x0211F0, company id 0x0211

OPCODE_ONOFF_GET = bytes([0x82, 0x01])
OPCODE_ONOFF_STATUS = 0x8204

# A node that just failed to connect is excluded from selection until its
# *own* next advertisement proves it is reachable again.
RECONNECT_BACKOFF_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 20.0
PROXY_SETUP_TIMEOUT_SECONDS = 4.0


def godox_crc(data: bytes) -> int:
    """CRC-8/MAXIM, as used by every Godox vendor frame."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def godox_frame(cmd: int, data: bytes, end: int = 0xFF) -> bytes:
    """Pack an 8-byte Godox V2 vendor frame: cmd, 5 data bytes (0xFF padded), end, crc."""
    body = bytes([cmd]) + data + b"\xFF" * (5 - len(data)) + bytes([end])
    return body + bytes([godox_crc(body)])


def godox_power(on: bool) -> bytes:
    """Godox's polarity is reversed: 0x00 = ON, 0x01 = OFF."""
    return godox_frame(0xFE, bytes([0x00 if on else 0x01]))


def godox_cct(brightness_pct: float, kelvin: int) -> bytes:
    """Brightness (0-100) and colour temperature. The TL60 only honours whole
    percents and 100 K steps, but the tenths field and gm=50 (no tint) are
    still sent for models that do use them."""
    pct = int(max(0, min(100, round(brightness_pct))))
    tenths = max(0, min(9, round((brightness_pct - pct) * 10)))
    return godox_frame(0xF0, bytes([pct, (kelvin // 100) & 0xFF, 50, 0, 0]), end=tenths)


@dataclass
class HubSettings:
    network_key: str
    app_key: str
    provisioner_address: int


class GodoxMeshError(Exception):
    """Raised when the hub cannot reach any light on this mesh right now."""


class GodoxMeshHub:
    """Shared connection, sequence counter and send/receive pipeline for one mesh."""

    def __init__(self, hass: HomeAssistant, entry_id: str, settings: HubSettings) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.nk = NetKey(settings.network_key)
        self.ak = AppKey(settings.app_key)
        self.src = settings.provisioner_address

        self._store: Store = Store(hass, STORAGE_VERSION, f"godox_ble_mesh_{entry_id}")
        self._seq = 0x100
        self._seq_lock = asyncio.Lock()

        self._client: BleakClient | None = None
        self._gateway_address: str | None = None
        self._connect_lock = asyncio.Lock()
        self._fail_time: dict[str, float] = {}

        self.iv = 0
        self._iv_known = False
        self._rx = bytearray()
        self._beacon_event = asyncio.Event()
        self._proxy_event = asyncio.Event()
        self._reply_waiters: dict[int, asyncio.Queue] = {}

    # -- lifecycle --------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._seq = data.get("sequence_number", self._seq)
            self.iv = data.get("iv_index", self.iv)
        _LOGGER.debug("godox_ble_mesh[%s]: loaded sequence_number=%s", self.entry_id, self._seq)

    async def async_shutdown(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001 - best-effort on unload
                pass
            self._client = None

    async def _async_save(self) -> None:
        await self._store.async_save({"sequence_number": self._seq, "iv_index": self.iv})

    async def _next_seq(self) -> int:
        async with self._seq_lock:
            seq = self._seq
            self._seq += 1
            # Persist every 32 numbers rather than every message -- a crash
            # loses at most that many, which is harmless since the floor only
            # needs to keep increasing, never hit an exact value.
            if self._seq % 32 == 0:
                await self._async_save()
            return seq

    # -- gateway selection & connection ------------------------------------

    def _candidate_addresses(self) -> list[str]:
        """Every BLE address currently advertising this mesh's Network ID, best signal first."""
        now = time.time()
        matches: list[tuple[int, str]] = []
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            data = info.service_data.get(PROXY_SVC_UUID)
            if not data or len(data) < 9 or data[0] != 0x00:
                continue  # not a Network-ID proxy advert (could be Node Identity, which we can't match)
            if data[1:9] != self.nk.network_id:
                continue
            fail_at = self._fail_time.get(info.address)
            if fail_at is not None and info.time <= fail_at:
                continue  # excluded until it advertises again after the failure
            matches.append((info.rssi, info.address))
        matches.sort(key=lambda m: -m[0])
        ordered = [addr for _rssi, addr in matches]
        # Stickiness: stay on the current gateway rather than hopping on signal noise.
        if self._gateway_address in ordered:
            ordered.remove(self._gateway_address)
            ordered.insert(0, self._gateway_address)
        return ordered

    async def async_ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return
            candidates = self._candidate_addresses()
            if not candidates:
                raise GodoxMeshError("No light on this mesh is currently reachable")

            last_error: Exception | None = None
            for address in candidates:
                ble_device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
                if ble_device is None:
                    continue
                try:
                    client = await establish_connection(
                        BleakClientWithServiceCache, ble_device, f"godox-ble-mesh-{self.entry_id}",
                        max_attempts=1,
                    )
                except Exception as err:  # noqa: BLE001 - any backend failure means "try the next light"
                    _LOGGER.debug("godox_ble_mesh: connect to %s failed: %s", address, err)
                    self._fail_time[address] = time.time()
                    last_error = err
                    continue

                self._client = client
                self._gateway_address = address
                self._rx = bytearray()
                self._beacon_event.clear()
                self._proxy_event.clear()

                await client.start_notify(DATA_OUT_UUID, self._on_notify)
                try:
                    await client.start_notify(PROV_OUT_UUID, self._on_notify)
                except Exception:  # noqa: BLE001 - some lights don't exposed this char via every proxy path
                    pass

                if not self._iv_known:
                    try:
                        await asyncio.wait_for(self._beacon_event.wait(), PROXY_SETUP_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        _LOGGER.debug(
                            "godox_ble_mesh: no network beacon from %s; assuming IV index %s",
                            address, self.iv,
                        )
                await self._send_beacon()
                await asyncio.sleep(0.2)
                await self._setup_proxy_filter()
                try:
                    await asyncio.wait_for(self._proxy_event.wait(), PROXY_SETUP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "godox_ble_mesh: %s did not answer the proxy setup message "
                        "(if this persists, the sequence number may need re-probing)",
                        address,
                    )
                _LOGGER.info("godox_ble_mesh[%s]: connected through %s", self.entry_id, address)
                return

            raise GodoxMeshError(f"Could not connect to any light on this mesh: {last_error}")

    def _disconnected_callback(self, _client: BleakClient) -> None:
        _LOGGER.debug("godox_ble_mesh[%s]: gateway %s disconnected", self.entry_id, self._gateway_address)
        self._client = None

    # -- low-level send -----------------------------------------------------

    async def _write_raw(self, data: bytes) -> None:
        assert self._client is not None
        await self._client.write_gatt_char(DATA_IN_UUID, data, response=False)

    async def _write_proxy_pdu(self, pdu_type: int, pdu: bytes, chunk: int = 20) -> None:
        room = chunk - 1
        if len(pdu) <= room:
            await self._write_raw(bytes([pdu_type]) + pdu)
            return
        parts = [pdu[i:i + room] for i in range(0, len(pdu), room)]
        for i, part in enumerate(parts):
            sar = 1 if i == 0 else (3 if i == len(parts) - 1 else 2)
            await self._write_raw(bytes([(sar << 6) | pdu_type]) + part)

    async def _send_beacon(self) -> None:
        body = bytes([0x00]) + self.nk.network_id + self.iv.to_bytes(4, "big")
        await self._write_proxy_pdu(1, b"\x01" + body + cmac(self.nk.beacon_key, body)[:8])

    async def _proxy_config(self, payload: bytes) -> None:
        seq = await self._next_seq()
        pdu = encrypt_network_pdu(self.nk, self.iv, 1, 0, seq, self.src, 0x0000, payload, proxy=True)
        await self._write_proxy_pdu(2, pdu)

    async def _setup_proxy_filter(self) -> None:
        await self._proxy_config(bytes([0x00, 0x00]))  # accept-list filter type
        await asyncio.sleep(0.2)
        await self._proxy_config(bytes([0x01]) + self.src.to_bytes(2, "big") + ALL_NODES.to_bytes(2, "big"))

    async def _send_access(self, dst: int, access: bytes, ttl: int = 7) -> None:
        seq = await self._next_seq()
        upper = encrypt_access(self.ak.key, False, seq, self.src, dst, self.iv, access)
        pdu = encrypt_network_pdu(self.nk, self.iv, 0, ttl, seq, self.src, dst,
                                  bytes([0x40 | self.ak.aid]) + upper)
        await self._write_proxy_pdu(0, pdu)

    # -- receive --------------------------------------------------------

    def _on_notify(self, _sender, data: bytearray) -> None:
        if not data:
            return
        sar, typ = data[0] >> 6, data[0] & 0x3F
        if sar == 0:
            self._handle_pdu(typ, bytes(data[1:]))
        elif sar == 1:
            self._rx = bytearray(data[1:])
        elif sar == 2:
            self._rx += data[1:]
        else:
            self._rx += data[1:]
            self._handle_pdu(typ, bytes(self._rx))

    def _handle_pdu(self, typ: int, pdu: bytes) -> None:
        if typ == 1:  # network beacon
            from .meshcrypto import parse_secure_beacon
            result = parse_secure_beacon(self.nk, pdu)
            if result:
                iv, _flags = result
                self.iv = iv
                self._iv_known = True
                self._beacon_event.set()
            return
        proxy = typ == 2
        decrypted = decrypt_network_pdu(self.nk, [self.iv, self.iv - 1, self.iv + 1], pdu, proxy=proxy)
        if decrypted is None:
            return
        if proxy:
            if decrypted["transport"][0] == 0x03:  # Filter Status
                self._proxy_event.set()
            return
        if decrypted["ctl"]:
            return
        t = decrypted["transport"]
        if t[0] & 0x80:  # segmented access message -- not handled, these commands never need it
            return
        if not (t[0] & 0x40):  # device-key encrypted -- we never expect these
            return
        access = decrypt_access(self.ak.key, False, decrypted["seq"], decrypted["src"],
                                decrypted["dst"], decrypted["iv"], t[1:])
        if access is None:
            return
        opcode, params = self._split_opcode(access)
        queue = self._reply_waiters.get(opcode)
        if queue is not None:
            queue.put_nowait((decrypted["src"], params))

    @staticmethod
    def _split_opcode(access: bytes) -> tuple[int, bytes]:
        if access[0] & 0x80 == 0:
            return access[0], access[1:]
        if access[0] & 0x40 == 0:
            return int.from_bytes(access[:2], "big"), access[2:]
        return int.from_bytes(access[:3], "big"), access[3:]

    # -- public API used by light.py ---------------------------------------

    async def async_send_power(self, node_address: int, on: bool) -> None:
        await self.async_ensure_connected()
        await self._send_access(node_address, GODOX_OPCODE + godox_power(on))

    async def async_send_brightness_cct(self, node_address: int, brightness_pct: float, kelvin: int) -> None:
        await self.async_ensure_connected()
        await self._send_access(node_address, GODOX_OPCODE + godox_cct(brightness_pct, kelvin))

    async def async_read_onoff(self, node_address: int, timeout: float = 2.0) -> bool | None:
        """Best-effort read of the standard (non-Godox) on/off state, used only
        to seed a light's initial state on startup -- not polled continuously,
        since the Godox vendor commands that actually drive the LED are
        unacknowledged and have no standard counterpart to read back."""
        await self.async_ensure_connected()
        queue: asyncio.Queue = asyncio.Queue()
        self._reply_waiters[OPCODE_ONOFF_STATUS] = queue
        try:
            await self._send_access(node_address, OPCODE_ONOFF_GET)
            while True:
                try:
                    src, params = await asyncio.wait_for(queue.get(), timeout)
                except asyncio.TimeoutError:
                    return None
                if src == node_address and params:
                    return bool(params[0])
        finally:
            self._reply_waiters.pop(OPCODE_ONOFF_STATUS, None)

    async def async_probe_sequence_number(self) -> int:
        """Find the lowest sequence number this network currently accepts from
        our address, by sending the (harmless, non-mesh-wide) proxy setup
        message with increasing numbers until a connected light answers.

        Used once, during setup, so the person configuring this integration
        never has to do this by hand the way earlier debugging in this project
        required.
        """
        await self.async_ensure_connected()
        for seq in (SEQUENCE_PROBE_START, *SEQUENCE_PROBE_STEPS):
            self._seq = seq
            self._proxy_event.clear()
            await self._proxy_config(bytes([0x00, 0x00]))
            try:
                await asyncio.wait_for(self._proxy_event.wait(), 2.5)
                found = self._seq  # the value actually used for the successful message
                await self._async_save()
                return found
            except asyncio.TimeoutError:
                continue
        raise GodoxMeshError(
            "No light answered at any sequence number up to "
            f"{SEQUENCE_PROBE_STEPS[-1]:,} -- check the network/app key and that a light is reachable"
        )
