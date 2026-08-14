"""Prepare the Revela mark for the ISP: crop to content, scale from
vector, and measure what each size would cost in fabric."""
import subprocess
import sys

import numpy as np
from PIL import Image

SVG = "/Users/serge/projects/revela/docs/Revela_Logo.svg"
BIG = 2000


def render_crop():
    subprocess.run(["rsvg-convert", "-w", str(BIG), "-h", str(BIG),
                    SVG, "-o", "big.png"], check=True)
    im = Image.open("big.png").convert("RGBA")
    a = np.array(im)
    ys, xs = np.where(a[..., 3] > 0)
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    return im.crop(box)


def design_palette(content, keep=5, merge=40):
    """The mark's OWN colours, read at full vector resolution.

    Counting colours at the target size is a trap: shrink to 128 px and
    the tile's antialiasing ring outnumbers the blue Bayer quadrant, so
    a by-count palette drops a brand colour and keeps a grey. At full
    resolution the flat areas dominate and the five design colours fall
    out cleanly -- so the palette is decided ONCE, here, and every size
    is quantised against it.
    """
    _, palette = quantise(content, keep=keep, merge=merge)
    return palette


def quantise(im, keep=8, merge=40, palette=None):
    """Nearest-palette index image + palette. Index 0 is transparent.

    Near-identical entries are MERGED before the top-N cut: a render
    carries several shades of the tile grey, and three of them would
    otherwise crowd out the dark slate the wordmark is drawn in.
    """
    a = np.array(im)
    rgb, alpha = a[..., :3].astype(int), a[..., 3]
    solid = alpha > 127
    if palette is not None:
        d = ((rgb[..., None, :] - palette[None, None, :, :]) ** 2).sum(-1)
        idx = (d.argmin(-1) + 1).astype(np.uint8)
        idx[~solid] = 0
        return idx, palette
    # the design's own colours, taken from the large render where
    # antialiasing has not yet blurred them
    cols, counts = np.unique(rgb[alpha > 250].reshape(-1, 3), axis=0,
                             return_counts=True)
    order = np.argsort(-counts)
    chosen, weight = [], []
    for i in order:
        c = cols[i]
        near = [k for k, p in enumerate(chosen)
                if ((p - c) ** 2).sum() < merge ** 2]
        if near:
            weight[near[0]] += counts[i]
            continue
        chosen.append(c.astype(int))
        weight.append(int(counts[i]))
    keep_idx = np.argsort(-np.array(weight))[:keep]
    palette = np.array([chosen[k] for k in keep_idx])
    d = ((rgb[..., None, :] - palette[None, None, :, :]) ** 2).sum(-1)
    idx = (d.argmin(-1) + 1).astype(np.uint8)
    idx[~solid] = 0
    return idx, palette


def main():
    content = render_crop()
    print(f"vector content: {content.size[0]}x{content.size[1]}\n")
    print(f"{'size':>9} {'opaque':>7} {'edge':>6}  {'bits/px':>7} "
          f"{'table':>9}  {'BRAM36':>7}")
    for side in (96, 128, 160, 192, 256):
        small = content.resize((side, side), Image.LANCZOS)
        idx, palette = quantise(small)
        n = len(palette)
        bits = max(1, (n + 1 - 1).bit_length())     # +1 for transparent
        a = np.array(small)
        edge = int(((a[..., 3] > 0) & (a[..., 3] < 255)).sum())
        table = side * side * bits
        print(f"{side:>4}x{side:<4} {int((a[...,3]>127).sum()):>7} "
              f"{edge:>6}  {bits:>7} {table/1024:>8.1f}K "
              f"{table/36864:>7.2f}")
        small.save(f"revela_{side}.png")
        np.save(f"revela_{side}_idx.npy", idx)
        np.save(f"revela_{side}_pal.npy", palette.astype(np.uint8))
        # a preview of exactly what the fabric would paint
        painted = np.zeros((side, side, 4), np.uint8)
        for i, c in enumerate(palette, start=1):
            painted[idx == i, :3] = c
            painted[idx == i, 3] = 255
        Image.fromarray(painted).save(f"revela_{side}_quantised.png")
    print("\npalette (most used first):")
    _, palette = quantise(content.resize((160, 160), Image.LANCZOS))
    for c in palette:
        print(f"   rgb{tuple(int(v) for v in c)}   10-bit "
              f"{tuple(int(v) << 2 for v in c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
