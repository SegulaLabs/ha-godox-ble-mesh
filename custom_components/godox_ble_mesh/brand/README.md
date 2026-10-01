# Brand assets

Home Assistant (and HACS, for a custom repo that includes an `icon.png` in
its root) render these as the badge beside "ha Godox BLE Mesh" on the
integration and device cards.

| file | size | used for |
|---|---|---|
| `icon.png` | 256x256 | the badge on integration and device cards |
| `icon@2x.png` | 512x512 | the same, on high-density displays |
| `logo.png` | 256x70 | wider contexts, such as a brand header |
| `logo@2x.png` | 512x140 | the same, on high-density displays |

## Source

The Godox wordmark, from Godox's own site
(`godox.com/static/upload/uploadfiles/logo.svg`), rendered to PNG. These
particular files are copied unchanged from
[binary-person/ha-godox-mesh](https://github.com/binary-person/ha-godox-mesh)
(MIT licensed), which did the original SVG-to-PNG rendering and square-icon
layout (the wordmark centred on a transparent background at 82% width, since
Godox publishes no separate square mark) — credit for that work belongs
there, not here.

To regenerate at other sizes from `logo.svg`:

```bash
rsvg-convert -w 512 -h 140 -o logo@2x.png logo.svg
```

## Trademark

"Godox" and the Godox logo are trademarks of Godox Photo Equipment Co., Ltd.
This project is **not affiliated with, endorsed by, or sponsored by** them.

The mark is used here to identify the hardware this integration controls —
the same basis on which Home Assistant carries logos for Hue, IKEA, Sonos,
LIFX and hundreds of other manufacturers whose integrations are likewise
unofficial. Users scan the integration list for the logo of the kit they own;
showing something else would be less honest, not more.
