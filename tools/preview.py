#!/usr/bin/env python3
"""把若干纹理的 albedo / 法线光照模拟 / 高光参数并排放大，用于人工校验。"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = ROOT + "/work"

SAMPLES = [
    "stone", "cobblestone", "mossy_cobblestone", "bricks", "oak_planks",
    "iron_block", "iron_ore", "diamond_block", "glowstone", "grass_block_top",
    "deepslate", "sand", "glass", "water_still", "gold_ore", "acacia_log",
]


def load(p):
    with Image.open(p) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0


from shadelib import shade


def main():
    blk = W + "/pa/assets/minecraft/textures/block"
    vblk = W + "/vanilla/block"
    out = W + "/pbr/assets/minecraft/textures/block"
    tile, scale = 16, 6
    rows = len(SAMPLES)
    cell = tile * scale
    canvas = Image.new("RGB", (cell * 3 + 40, rows * (cell + 16) + 30), (24, 24, 28))
    d = ImageDraw.Draw(canvas)
    for i, name in enumerate(SAMPLES):
        alb_path = os.path.join(blk, name + ".png")
        if not os.path.exists(alb_path):
            alb_path = os.path.join(vblk, name + ".png")
        alb = load(alb_path)
        nrm = load(os.path.join(out, name + "_n.png"))
        spc = load(os.path.join(out, name + "_s.png"))
        h = min(alb.shape[0], tile)
        alb_s = alb[:h, :tile]
        nrm_s = nrm[:h, :tile]
        spc_s = spc[:h, :tile]

        shaded = shade(alb_s, nrm_s, spc_s)
        # 第三列可视化：白 = 粗糙 / 黑 = 光滑，金属区（绿通道 > 128）偏红
        rough = 1.0 - spc_s[..., 0]
        metal = np.clip((spc_s[..., 1] - 0.5) * 2.0, 0.0, 1.0)
        rough_vis = np.stack([np.clip(rough + 0.45 * metal, 0, 1), rough, rough], -1)
        for j, arr in enumerate([alb_s[..., :3], shaded, rough_vis]):
            img = Image.fromarray(np.round(np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGB")
            img = img.resize((cell, h * scale), Image.NEAREST)
            canvas.paste(img, (20 + j * cell, 20 + i * (cell + 16)))
        d.text((20, 6 + i * (cell + 16)), name, fill=(220, 220, 230))
    canvas.save(W + "/preview.png")
    print("saved", W + "/preview.png", canvas.size)


if __name__ == "__main__":
    sys.exit(main())
