# Godox BLE Mesh (Direct)

A Home Assistant custom integration that controls Godox Bluetooth Mesh lights
(TL60 and likely every other light on the same LK8620/Telink mesh radio)
**without removing them from the Godox phone app.**

This exists because the community integration,
[ha-godox-mesh](https://github.com/binary-person/ha-godox-mesh), turned out
not to support adding more than one already-Godox-app-paired light while
sharing a single Bluetooth connection — its "add to this mesh" feature only
provisions brand-new, factory-reset lights. Everything in this repo is built
on logic already hand-verified, packet-by-packet, against a real Godox TL60
mesh — see **[docs/HOW-THIS-WORKS.md](docs/HOW-THIS-WORKS.md)** for the full
reverse-engineering story, and the companion
**[`godox_mesh.py`](https://github.com/REPLACE_ME/godox-hack)** command-line
script this integration's protocol code is ported from.

> **This repo will contain your mesh network's keys once you configure it.**
> Treat your Home Assistant config (and any exported `.storage` backups) with
> the same care as a password.

## What makes this different from ha-godox-mesh

| | ha-godox-mesh | This integration |
|---|---|---|
| Adding a light already paired to the Godox app | Not supported (only factory-reset lights) | **The only supported path** — paste its mesh address, nothing else |
| Device key needed per light | Yes | **No** — every command this integration sends is AppKey-encrypted, never device-key-encrypted (see below) |
| Multiple lights, one Bluetooth connection | Only for lights HA itself provisioned | **Always** — one config entry = one connection, shared by every light under it |
| Sequence-number floor | You find it by hand (or guess) | **Found automatically** during setup, and again any time from Settings → the integration → Configure → "Re-find the sequence number" |
| Controls the light's actual output | N/A | Uses Godox's own vendor opcode (`0x0211F0`) — standard Bluetooth Mesh On/Off changes a status flag but does **not** change the light's output, confirmed on real hardware |

## Why no device key is needed

Every message type this integration actually sends is encrypted with the
network-wide **AppKey**, not a per-node **device key**:

- The Godox vendor power/brightness/colour-temperature commands (the ones
  that actually drive the light) — AppKey.
- The one-time standard "what's your on/off state" read used to seed a
  light's initial state on startup — AppKey.

A device key is only needed for Bluetooth Mesh **Config** messages (binding an
app key to a model, changing a node's own address, and so on) — operations
this integration never performs, because the lights are already fully
configured by the Godox app. That's why setup only ever asks for the network
key, the app key, and each light's mesh address.

## Install

### Via HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add this repo's URL,
   category **Integration**.
2. Install **Godox BLE Mesh (Direct)**, restart Home Assistant.

### Manually

Copy `custom_components/godox_ble_mesh/` into your Home Assistant's
`config/custom_components/` directory, then restart.

## Requirements

- A Bluetooth adapter, or an **ESPHome Bluetooth proxy** (`active: true`)
  within reach of the lights — the same requirement as ha-godox-mesh. A
  listen-only proxy cannot connect, only observe.
- Your mesh's **network key** and **app key**, and each light's **mesh unicast
  address**. If you don't have these yet, see
  [docs/HOW-THIS-WORKS.md](docs/HOW-THIS-WORKS.md) for how they were pulled
  out of the Godox iPhone app's own local database — the exact method used
  for the TL60s this was built against.

## Setup

**Settings → Devices & services → Add integration → Godox BLE Mesh (Direct)**

1. Paste the network key and app key.
2. Pick a **provisioner address** this network hasn't seen before — a number
   between 1 and 32767, in decimal. Anything not already used by the Godox
   app (`256` = `0x0100`) or another controller on the same mesh is fine;
   `1280` (`0x0500`) is a reasonable default if you have nothing else running.
3. Submit. The integration connects to whichever light it can reach and
   **automatically finds a working sequence number** — you do not need to run
   any script or guess a number by hand. If nothing answers, close the Godox
   app (and any other tool connected to the lights) and try again.

### Adding lights

Once the mesh entry exists: **that entry → Configure → Add a light**. Give it
a name and its mesh address in decimal (`0x0115` is `277` — a hex-to-decimal
converter or Python's `int("0115", 16)` gets you there). No device key, no
pairing mode, no effect on the Godox app. Repeat for each light — they all
share the one connection this entry already holds.

### If lights stop responding later

These lights use standard Bluetooth Mesh replay protection: they silently
drop any message whose sequence number is at or below one they've already
seen from your address. That floor only ever rises, and normal use raises it
slowly — but heavy testing, repeated reconnects, or another controller
sharing your provisioner address by mistake can push it up fast. If
everything that used to work suddenly goes silent with no error:

**That entry → Configure → "Re-find the sequence number"** — reconnects and
probes for the current floor automatically, the same way initial setup did.

## Current status

Built and verified against two lights on one Godox TL60 mesh — **Corner sofa**
and **Shelf** — controlling on/off, brightness and colour temperature. Not
yet exercised against other Godox mesh models; the vendor protocol is
expected to be identical (see `docs/HOW-THIS-WORKS.md`), but the
colour-temperature range (`2700`–`6500` K) is currently hard-coded to the
TL60's own range and not yet read from a per-model table the way ha-godox-mesh
does — a model-aware range table is a natural next step if other light models
get added.

## Project layout

```
custom_components/godox_ble_mesh/
  __init__.py       entry setup/teardown, wires the hub to the light platform
  hub.py            the shared connection: gateway selection, send/receive,
                    sequence-number probing -- ported from godox_mesh.py
  config_flow.py    setup wizard + the Configure menu (add/remove light, re-probe)
  light.py          one LightEntity per configured mesh address
  meshcrypto.py     Bluetooth Mesh crypto, identical to the standalone script's
  const.py          domain, option keys, defaults
docs/HOW-THIS-WORKS.md   the reverse-engineering writeup this is built on
```
