<div align="center">

# 💡 ha Godox BLE Mesh

### Control Godox Bluetooth Mesh lights from Home Assistant — no device key, one shared connection, never leaves the Godox app.

[![License: MIT](https://img.shields.io/badge/license-MIT-c8f169?style=flat-square)](LICENSE)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant ≥ 2024.1](https://img.shields.io/badge/Home%20Assistant-%E2%89%A52024.1-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)](custom_components/godox_ble_mesh/manifest.json)
[![Bluetooth Mesh](https://img.shields.io/badge/Bluetooth-Mesh-0082FC?style=flat-square&logo=bluetooth&logoColor=white)](docs/HOW-THIS-WORKS.md)

<img src="custom_components/godox_ble_mesh/brand/logo.png" alt="Godox" width="256">

**[Why this exists](#-what-makes-this-different-from-ha-godox-mesh)** · **[Install](#-install)** · **[Setup](#-setup)** · **[Scope](#-scope-godox-tl60-first)** · **[How it works](docs/HOW-THIS-WORKS.md)**

</div>

---

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

> [!IMPORTANT]
> **This repo will contain your mesh network's keys once you configure it.**
> Treat your Home Assistant config (and any exported `.storage` backups) with
> the same care as a password.

<details>
<summary>📋 Table of contents</summary>

- [What makes this different from ha-godox-mesh](#-what-makes-this-different-from-ha-godox-mesh)
- [Why no device key is needed](#-why-no-device-key-is-needed)
- [Install](#-install)
- [Requirements](#-requirements)
- [Setup](#-setup)
- [Scope: Godox TL60 first](#-scope-godox-tl60-first)
- [Project layout](#%EF%B8%8F-project-layout)
- [Docs](#-docs)
- [Trademarks and affiliation](#%EF%B8%8F-trademarks-and-affiliation)
- [License](#-license)

</details>

## 🧭 What makes this different from ha-godox-mesh

| | ha-godox-mesh | This integration |
|---|---|---|
| Adding a light already paired to the Godox app | Not supported (only factory-reset lights) | **The only supported path** — paste its mesh address, nothing else |
| Setup | Manual keys + manual per-light provisioning | **Paste the Godox app's own export once** — keys, light names and addresses are all picked up automatically (a manual-entry path still exists if you don't have the export handy) |
| Device key needed per light | Yes | **No** — every command this integration sends is AppKey-encrypted, never device-key-encrypted (see below) |
| Multiple lights, one Bluetooth connection | Only for lights HA itself provisioned | **Always** — one config entry = one connection, shared by every light under it |
| Sequence-number floor | You find it by hand (or guess) | **Found automatically** during setup, and again any time from Settings → the integration → Configure → "Re-find the sequence number" |
| Controls the light's actual output | N/A | Uses Godox's own vendor opcode (`0x0211F0`) — standard Bluetooth Mesh On/Off changes a status flag but does **not** change the light's output, confirmed on real hardware |

## 🔐 Why no device key is needed

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

## 📦 Install

### Via HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add this repo's URL,
   category **Integration**.
2. Install **ha Godox BLE Mesh**, restart Home Assistant.

### Manually

Copy `custom_components/godox_ble_mesh/` into your Home Assistant's
`config/custom_components/` directory, then restart.

## ✅ Requirements

- A Bluetooth adapter, or an **ESPHome Bluetooth proxy** (`active: true`)
  within reach of the lights — the same requirement as ha-godox-mesh. A
  listen-only proxy cannot connect, only observe.
- Your mesh's **network key** and **app key**, and each light's **mesh unicast
  address**. If you don't have these yet, see
  [docs/HOW-THIS-WORKS.md](docs/HOW-THIS-WORKS.md) for how they were pulled
  out of the Godox iPhone app's own local database — the exact method used
  for the TL60s this was built against.

## 🚀 Setup

**Settings → Devices & services → Add integration → ha Godox BLE Mesh**

### The easy way: import

1. Choose **"Import from the Godox app (recommended)"**.
2. Paste the whole mesh export (the `meshJson` blob — see
   [docs/HOW-THIS-WORKS.md](docs/HOW-THIS-WORKS.md) for how to get it out of
   the Godox app's own database; this is still a one-time step, there's no
   way around getting the keys out of the app itself).
3. Pick which of the Godox lights found in the export to add now — everything
   is pre-selected, so most people just hit submit. No hex, no decimal
   conversion, no typing addresses.
4. Submit. The integration connects to whichever light it can reach and
   **automatically finds a working sequence number** — you do not need to run
   any script or guess a number by hand. If nothing answers, close the Godox
   app (and any other tool connected to the lights) and try again.

Only entries whose Bluetooth SIG company id is Godox's own (`0211`) are
picked up from the export — this is what tells a real Godox light apart from
the app's own provisioner entry in the same file, and would also skip any
other vendor's device sharing the same mesh.

### The manual way

If you don't have the export handy, choose **"Enter network key and app key
manually"** instead, paste just the two keys, pick a provisioner address (see
below), and add lights afterwards one at a time via **Configure → Add a
light** (name + mesh address in decimal — `0x0115` is `277`).

A **provisioner address** is a number between 1 and 32767 this network hasn't
seen before — anything not already used by the Godox app (`256` = `0x0100`)
or another controller on the same mesh is fine; `1024` (`0x0400`) is this
project's own convention for "the Home Assistant instance" if you have
nothing else running.

### Adding a brand-new light (buying another one later)

> [!IMPORTANT]
> **This is the one thing that works differently from ha-godox-mesh**, and
> it's worth understanding before you buy your next light.
>
> ha-godox-mesh can *provision* a factory-reset light itself, straight from
> Home Assistant — that's its whole "add to this mesh" feature. **This
> integration never provisions anything.** It only ever talks to lights the
> Godox app has already paired and assigned an address to. So a new light's
> setup is always a two-step round trip:
>
> 1. **Pair the new light in the Godox app first**, exactly as you always
>    would, on your phone. This is what assigns it a mesh address and binds
>    the network/app keys to it — nothing else can do that step.
> 2. **Re-export the mesh JSON** (Finder → the phone → Files tab → the Godox
>    app's folder, same one-time manual step described above) and bring it
>    into Home Assistant.
>
> The network key and app key themselves **do not change** when you add a
> light — only the export's `nodes` list grows by one. You're not redoing
> setup, just picking up the one new entry.

With a fresh export in hand: **that entry → Configure → "Add lights from the
Godox app export"** — paste it in, and only lights not already configured
here are offered (so pasting the same export again, with one new light added
in the Godox app, shows just that one). Or **Configure → "Add a light
manually"** for a single light by name and decimal address, if you already
know it. Either way: no device key, no pairing mode, no effect on the Godox
app — every light added shares the one connection this entry already holds.

### If lights stop responding later

These lights use standard Bluetooth Mesh replay protection: they silently
drop any message whose sequence number is at or below one they've already
seen from your address. That floor only ever rises, and normal use raises it
slowly — but heavy testing, repeated reconnects, or another controller
sharing your provisioner address by mistake can push it up fast. If
everything that used to work suddenly goes silent with no error:

**That entry → Configure → "Re-find the sequence number"** — reconnects and
probes for the current floor automatically, the same way initial setup did.

## 🧩 Scope: Godox TL60 first

This project was built and is verified against a mesh of **Godox TL60**
units, controlling on/off, brightness and colour temperature. The import
feature accepts any node whose Bluetooth SIG company id is `0211` (Godox's
own), since that's the only reliable "is this a Godox light" signal available
without a device key — it does not currently distinguish *which* Godox
model a given node is.

In practice this means: other Godox mesh lights sharing the same vendor
opcode (`0x0211F0`) and command format will likely respond to on/off and
brightness/CCT commands too, since the protocol itself is vendor-wide, not
TL60-specific (see `docs/HOW-THIS-WORKS.md`). What is **not** yet handled is
anything that varies *by model* — the colour-temperature range
(`2700`–`6500` K) is currently hard-coded to the TL60's own range rather than
read from a per-model table the way ha-godox-mesh does, so a different Godox
light with a wider or narrower range, or extra controls (tint, effects), may
report its capabilities wrong even if basic control works. A model-aware
capabilities table is a natural next step if non-TL60 hardware gets tested.

## 🗂️ Project layout

```
custom_components/godox_ble_mesh/
  __init__.py       entry setup/teardown, wires the hub to the light platform
  hub.py            the shared connection: gateway selection, send/receive,
                    sequence-number probing -- ported from godox_mesh.py
  config_flow.py    setup wizard (import or manual) + the Configure menu (add/remove light, re-probe)
  meshimport.py     parses the Godox app's mesh export for the import path
  light.py          one LightEntity per configured mesh address
  meshcrypto.py     Bluetooth Mesh crypto, identical to the standalone script's
  const.py          domain, option keys, defaults
  brand/            integration icon/logo -- see brand/README.md for source + trademark note
docs/HOW-THIS-WORKS.md   the reverse-engineering writeup this is built on
```

## 📚 Docs

[HOW-THIS-WORKS](docs/HOW-THIS-WORKS.md) — the full reverse-engineering
story: getting keys out of the Godox app, why no device key is needed, the
vendor protocol, the sequence-number floor, the import/discovery feature, why
a shared connection works, and what didn't work along the way.

## 🏷️ Trademarks and affiliation

"Godox" and the Godox logo (used as this integration's icon — see
[`custom_components/godox_ble_mesh/brand/`](custom_components/godox_ble_mesh/brand/README.md)
for where it came from) are trademarks of Godox Photo Equipment Co., Ltd.
This is an independent, unofficial project, **not affiliated with, endorsed
by, or sponsored by** Godox.

## 📄 License

[MIT](LICENSE) for the code. The Godox brand assets under `brand/` are used
under the trademark terms above, not this license.
