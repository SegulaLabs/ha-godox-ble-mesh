# How this works, and how it was found

This documents the full reverse-engineering process behind both this Home
Assistant integration and its sibling, the standalone
[`godox_mesh.py`](https://github.com/REPLACE_ME/godox-hack) command-line
script, which this integration's protocol code is ported from line-for-line.
If something here stops working after a Godox app update or a light's
firmware update, this is the place to start.

## 1. Getting the mesh network's keys out of the Godox app

Godox's lights use standard Bluetooth Mesh (the Telink `LK8620` radio chip),
not a proprietary lock-in. The phone app stores the entire mesh
configuration — network key, app key, every light's device key and address —
**in plain text** inside its own local SQLite database.

1. Make a full backup of the iPhone (Finder → select the phone → "Back up all
   data to this Mac", unencrypted is fine since only app data is needed, not
   Keychain).
2. Extract the Godox app's data folder from that backup with a free backup
   browser (iBackup Viewer, or iMazing's free browsing mode). Look for
   `GodoxLight.db`.
3. Open it (any SQLite tool, or Python's `sqlite3` module) and read the
   `Project` table's `meshJson` column for your active project (the one your
   lights are actually in — check `deviceNum` and the node names). It's a
   standard Bluetooth Mesh "CDB" JSON document:
   ```json
   {
     "netKeys": [{"key": "...", "index": 0}],
     "appKeys": [{"key": "...", "index": 0, "boundNetKey": 0}],
     "nodes": [
       {"name": "Corner sofa", "unicastAddress": "0115",
        "deviceKey": "...", "macAddress": "A4C138CB2A86"}
       ...
     ]
   }
   ```

That's the entire secret. No special permissions, jailbreak, or app
vulnerability involved — it's just sitting in a file the app already uses.

**This integration only needs the `netKeys[0].key` and `appKeys[0].key`
fields, plus each light's `unicastAddress`.** Device keys are not used (see
below).

## 2. Why no device key is needed

A Bluetooth Mesh node's traffic is encrypted at two layers:

- **Network layer**: the Network Key (shared by the whole mesh).
- **Upper transport layer**: either the **App Key** (shared by every node
  that has it bound to a model) or a node's own **Device Key** (unique per
  node, used only for Configuration messages like binding an app key or
  changing an address).

Every message this project sends falls into the AppKey category:
- The **standard Generic OnOff status read**, used once to seed a light's
  initial on/off state.
- The **real Godox vendor commands** (power, brightness, colour temperature) —
  see below.

Nothing here ever sends a Configuration message, because the lights are
already fully configured by the Godox app (app key bound to every model that
needs it, addresses already assigned). So the device key — which the
community `ha-godox-mesh` integration requires for its provisioning flow — is
simply never touched.

## 3. Standard Bluetooth Mesh commands don't control the light

Sending a standard Generic OnOff Set, or Light Lightness/CTL Set, **is
accepted and acknowledged** — the light replies with the new status, as if it
worked — **but the LED output does not change.** Confirmed on real hardware:
toggling standard OnOff repeatedly while watching the physical light showed no
effect, while the status readback dutifully flipped ON/OFF each time.

The light's actual output is driven by a separate controller that only
listens to **Godox's own vendor model**, opcode `0x0211F0` (company ID
`0x0211`, Godox's Bluetooth SIG member ID). The frame format and byte
meanings below were cross-checked against two independent sources: the Godox
iPhone app's own debug logs (hundreds of real captured commands), and the
open-source [ha-godox-mesh](https://github.com/binary-person/ha-godox-mesh)
project's protocol module — both agree exactly.

### Frame format

An 8-byte payload, sent unacknowledged, AppKey-encrypted:

```
byte 0:   command byte (0xFE = power, 0xF0 = brightness/CCT, ...)
byte 1-5: up to 5 data bytes, 0xFF-padded if unused
byte 6:   "end byte" -- often 0xFF, or a secondary data field (see CCT below)
byte 7:   CRC-8/MAXIM (Dallas, reflected polynomial 0x31) over bytes 0-6
```

**Power** (`cmd=0xFE`): data byte 0 is `0x00` for ON, `0x01` for OFF —
reversed from what you'd expect. Verified against a real captured frame:
`fe01ffffffffff48` is a logged OFF command.

**Brightness + colour temperature** (`cmd=0xF0`):
```
data[0] = brightness percent, 0-100
data[1] = kelvin / 100            (so 5600 K -> 56 = 0x38)
data[2] = green/magenta tint + 50 (0x32 = 50 = no tint, for models with no tint control)
data[3] = 0x00
data[4] = 0x00
end byte = brightness tenths (0-9) -- ignored by models whose resolution is whole percents
```
Example: 75 % at 5600 K → `f04b38320000003c`.

### The TL60's actual resolution

Despite accepting a tenths field, the TL60 only honours **whole percent**
brightness steps (its catalogue `luminance`/`intensityUint` value is `100`),
and colour temperature only in **100 K steps** across its **2700–6500 K**
range. Sending finer values is harmless — just rounded away — but there is no
point commanding more precision than that.

## 4. The sequence-number floor (the part that actually caused problems)

Every mesh message carries a monotonically increasing **sequence number**,
tied to its source address. This is **standard Bluetooth Mesh replay
protection**: each node remembers the highest sequence number it has seen
from each source address, and silently drops anything at or below that —
no error, no reply, nothing.

This caused real, repeated confusion during development, because the
symptom is indistinguishable from a dead connection, a wrong key, or a wrong
address: **the light just goes quiet.**

Key things learned the hard way:

- **The floor only ever increases**, and only for the specific source address
  that generated the traffic. A brand-new, never-used address starts low
  (accepted a command at sequence ~18 in one real test).
- **It rises fast under heavy use.** One address went from a comfortable
  `1,000,000` to needing `1,048,576` within about a day of repeated
  setup/retry/reconnect cycles from a misconfigured integration attempt.
- **Two separate controllers must never share one address.** If they do,
  each tracks its own local counter with no coordination, and whichever one
  restarts with a lower number than the other has already pushed the mesh to
  looks exactly like a replay attack to every light — indistinguishable from
  the "floor rose" case above, but caused by a bookkeeping collision instead.
- **There is no way to query a light for "what sequence number do you expect
  next."** The only way to find the current floor is to try increasing
  numbers until one is accepted.

This integration **automates that search** (`hub.py`'s
`async_probe_sequence_number`): on setup, and on demand via Configure →
"Re-find the sequence number," it sends the harmless proxy-filter-setup
message with increasing sequence numbers until a light answers, then adds a
large safety margin and saves the result. Nobody configuring this integration
should ever need to run a separate script to find this number by hand — that
manual process is exactly what this feature replaces.

## 5. Automatic setup: parsing the export instead of hand-typing it

The config flow's "Import from the Godox app" path (`meshimport.py`) just
parses the same JSON document described in §1 directly — no manual field
re-typing, no hex-to-decimal conversion for addresses. The only judgment call
it makes is **which entries in `nodes[]` are actual Godox lights**: the app's
own provisioner entry (itself a node in that same list, named something like
"Telink iOS provisioner node") has no `cid` field at all, while every real
light we've seen carries `"cid": "0211"` — Godox's Bluetooth SIG company id.
Filtering on that field, not the node's name (which is just whatever text the
installer typed in-app), is what separates "a Godox light" from the
provisioner's own bookkeeping entry or another vendor's node that happens to
share the mesh. See the README's "Scope: Godox TL60 first" section for what
this does and doesn't tell you about *which* Godox light model each node is.

## 6. Why a shared connection works at all

Bluetooth Mesh is, as the name says, a mesh: a client opens one ordinary GATT
connection to *any* node that supports the Proxy feature, and that node
relays messages between the connection and the rest of the mesh's radio
network. Which physical light you happen to be connected to is irrelevant to
which light you can address — this was directly verified by connecting to
one light and successfully commanding a different one through it, with
nothing in between.

That's the entire basis for this integration's design: **one config entry,
one connection, as many lights as you add** — each one only needs its mesh
address, because the connection itself doesn't care who it's talking to
address-wise; the mesh handles delivery.

A light only accepts **one Bluetooth connection at a time** to its own radio,
though — so if the Godox app, another Home Assistant instance, or the
`godox_mesh.py` script is actively connected to the specific light this
integration happens to pick as its gateway, connecting will fail or hang
until that other connection is closed. This integration automatically picks
a different light as gateway if its current one drops or fails, the same way
`godox_mesh.py`'s `discover`/`on`/`off` commands do.

## 7. What didn't work, and why (for anyone re-deriving this later)

| Tried | Result | Why |
|---|---|---|
| nRF Mesh app import of the exported keys | "Proxy does not know the Network Key" | Low sequence numbers on import — see §4 |
| Standard Generic OnOff / Lightness / CTL Set | Status flips, LED doesn't | See §3 |
| ha-godox-mesh "Configure → add to this mesh" for an already-provisioned light | `No PDU received within 10.0s`, every time | That flow runs real BLE Mesh *provisioning* (factory-reset only) — see §2 for why this integration avoids needing that at all |
| Two ha-godox-mesh entries, same provisioner address | One worked, the other silently failed after the first | Sequence-number collision — see §4 |
| Guessing a "safe" fixed sequence number | Worked once, failed days later | The floor moves — see §4; this integration probes instead of guessing |

## Credits

- Protocol and crypto: independently reverse-engineered from the Godox
  iPhone app's own debug logs, cross-checked against
  [binary-person/ha-godox-mesh](https://github.com/binary-person/ha-godox-mesh)
  (MIT licensed), which arrived at the same vendor opcode and frame layout
  by its own route.
- Gateway-selection *approach* (prefer the current connection, fail over on
  disconnect, exclude a node that just failed until it re-advertises) follows
  the same general design as that project's `gateway.py`; this integration's
  implementation is written independently.
