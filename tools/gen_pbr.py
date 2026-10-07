#!/usr/bin/env python3
"""
ProgramArt PBR —— labPBR 贴图生成器

输入：
  --vanilla  26.3 原版 block 纹理目录（提供完整纹理名列表 + 新方块的 albedo）
  --pa       Programmer Art 的 block 纹理目录（旧方块优先用它做高度/颜色参考）
  --out      输出目录（会被写成 assets/minecraft/textures/block/）

输出（每个纹理名 X.png）：
  X_n.png  R/G = 切线空间法线（DirectX 约定，Y-，绿通道朝下）
           B   = 材质 AO（0 = 全遮蔽，255 = 无遮蔽）
           A   = POM 高度（0 = 25% 深度，255 = 与表面齐平）
  X_s.png  R = 感知平滑度 1-sqrt(roughness)
           G = F0（1~229 线性反射率）或硬编码金属 ID（230~255，255 = 用 albedo 当 F0）
           B = 0~64 孔隙率 / 65~255 次表面散射
           A = 自发光强度（0~254；255 会被光影忽略）
  X_e.png  OptiFine 发光贴图（RGB = 发光色，A = 强度）—— 不开光影也能发光

算法：
  高度 = 局部亮度对比（结构项：暗缝/暗纹 → 凹陷） + 可平铺 fBm 颗粒噪声
  法线 = 对高度做 wrap 卷积求梯度（帧与帧、图块与图块之间无接缝）
  材质 = 按纹理名规则分类，参数以「物理粗糙度」书写，输出时换算为 labPBR 的
        perceptual smoothness = 1 - sqrt(roughness)
  矿石 = 用「与主色距离」生成金属度遮罩，只让矿物颗粒变金属
"""

import argparse
import os
import shutil
import sys

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- 基础工具


def load_rgba(path):
    with Image.open(path) as im:
        im = im.convert("RGBA")
        return np.asarray(im, dtype=np.float32) / 255.0


def box_blur_wrap(a, passes=1, size=3):
    """循环边界的盒式模糊，保证贴图四方连续。"""
    r = size // 2
    ker = np.full((size, size), 1.0 / (size * size), dtype=np.float32)
    for _ in range(passes):
        acc = np.zeros_like(a)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                acc += np.roll(np.roll(a, dy, axis=0), dx, axis=1) * ker[dy + r, dx + r]
        a = acc
    return a


def value_noise(h, w, cells, rng):
    """可平铺的 value noise（网格索引取模，天然无缝）。"""
    g = rng.random((cells, cells)).astype(np.float32)
    ys = np.arange(h, dtype=np.float32) * cells / h
    xs = np.arange(w, dtype=np.float32) * cells / w
    y0 = np.floor(ys).astype(int) % cells
    y1 = (y0 + 1) % cells
    x0 = np.floor(xs).astype(int) % cells
    x1 = (x0 + 1) % cells
    fy = ys - np.floor(ys)
    fx = xs - np.floor(xs)
    fy = (fy * fy * (3.0 - 2.0 * fy))[:, None]
    fx = (fx * fx * (3.0 - 2.0 * fx))[None, :]
    top = g[y0[:, None], x0[None, :]] * (1.0 - fx) + g[y0[:, None], x1[None, :]] * fx
    bot = g[y1[:, None], x0[None, :]] * (1.0 - fx) + g[y1[:, None], x1[None, :]] * fx
    return top * (1.0 - fy) + bot * fy


def fbm(h, w, rng, base_cells=4, octaves=3):
    total = np.zeros((h, w), dtype=np.float32)
    amp, norm, cells = 1.0, 0.0, base_cells
    for _ in range(octaves):
        total += value_noise(h, w, max(2, int(cells)), rng) * amp
        norm += amp
        amp *= 0.5
        cells *= 2.0
    return total / norm


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def has(n, *kws):
    return any(k in n for k in kws)


def alpha_mask(a):
    """alpha 低于 10% 的像素视为不存在（贴空区域），其余按满值保留。

    不能用 alpha 做线性缩放：玻璃、冰这类半透明贴图的 alpha 只有 0.4~0.6，
    线性缩放会把它们的平滑度也一起压没。
    """
    return np.clip((a - 0.10) / 0.15, 0.0, 1.0)


def mat(rough, metal=0.0, por=0.30, sss=0.0, depth=10.0, grain=0.12,
        struct=0.55, emis=0.0, hscale=0.9, ore=False, f0=None):
    """粗糙度以物理值书写，输出时换算为 perceptual smoothness。

    metal > 0.5 视为金属（F0 用硬编码金属 ID）；否则是绝缘体，用 f0（线性反射率）。
    por / sss 均为 0~1 的比例。
    """
    d = dict(rough=rough, metal=metal, por=por, sss=sss, depth=depth,
             grain=grain, struct=struct, emis=emis, hscale=hscale,
             f0=f0, metal_id=None)
    if ore:
        d["ore"] = True
    return d


DEFAULT = mat(0.80)


# ---------------------------------------------------------------- 材质分类

# labPBR 硬编码金属 ID（绿通道 230~254）；255 表示未预定义金属，用 albedo 充当 F0
METAL_IRON, METAL_GOLD, METAL_COPPER, METAL_CUSTOM = 230, 231, 234, 255


def guess_metal_id(n):
    """给金属类方块挑一个 labPBR 预定义金属 ID。"""
    if has(n, "gold") or has(n, "bell"):
        return METAL_GOLD
    if has(n, "copper") or has(n, "lightning_rod"):
        return METAL_COPPER
    if has(n, "iron") or has(n, "anvil") or has(n, "hopper") or has(n, "cauldron") \
            or has(n, "chain") or has(n, "heavy_core") or has(n, "lodestone") \
            or has(n, "rail"):
        return METAL_IRON
    return METAL_CUSTOM


def ore_metal_id(n):
    """矿物颗粒的金属 ID；返回 0 表示该矿石其实没有金属光泽（煤、石英）。"""
    if has(n, "coal", "quartz"):
        return 0
    if has(n, "gold"):
        return METAL_GOLD
    if has(n, "iron"):
        return METAL_IRON
    if has(n, "copper"):
        return METAL_COPPER
    return METAL_CUSTOM


def guess_f0(n):
    """绝缘体的基础反射率 F0（线性，真实材料大多落在 0.02~0.08）。"""
    if has(n, "wool", "carpet", "bed", "banner", "leaves", "grass", "moss",
           "vine", "kelp", "lily", "coral", "flower"):
        return 0.02
    if has(n, "planks", "log", "wood", "bamboo", "hyphae", "door", "trapdoor",
           "sign", "fence", "barrel", "bookshelf", "crafting_table", "bamboo"):
        return 0.035
    if has(n, "glass", "ice", "polished", "smooth_"):
        return 0.05
    return 0.04


# labPBR 里孔隙率和次表面散射共用蓝通道，一张贴图只能二选一。
# 只有真正透光/散射的材质才走 SSS，其余一律给孔隙率（雨天湿润效果更实用）。
SSS_KEYWORDS = (
    "leaves", "sapling", "grass", "fern", "flower", "petal", "vine", "kelp",
    "seagrass", "moss", "coral", "lily", "ice", "snow", "slime", "honey_block",
    "melon", "egg", "candle", "amethyst_cluster", "amethyst_bud", "frogspawn",
    "azalea", "bush", "roots", "mushroom", "propagule", "dripleaf", "blossom",
    "pink_petals", "lilac", "peony", "sunflower", "torchflower", "wither_rose",
)


def classify(n):
    """按纹理名推断 PBR 参数。顺序 = 优先级，先命中先返回。"""
    raw = _classify(n)
    p = dict(raw)                     # 必须复制，否则会污染 DEFAULT
    p["_default"] = raw is DEFAULT
    if p["f0"] is None:
        p["f0"] = guess_f0(n)
    if p["metal"] > 0.5:
        p["metal_id"] = guess_metal_id(n)
    if p.get("ore"):
        p["ore_id"] = ore_metal_id(n)
    if not has(n, *SSS_KEYWORDS):
        p["sss"] = 0.0
    p["por"] = min(max(p["por"], 0.0), 1.0)
    p["sss"] = min(max(p["sss"], 0.0), 1.0)
    return p


def _classify(n):

    # --- 液体 / 传送门 ---
    if n.startswith("water"):
        return mat(0.03, por=0.0, sss=0.15, depth=5.0, grain=0.06,
                   struct=0.25, hscale=0.4, f0=0.02)
    if n.startswith("lava") or n.startswith("fire") or n.startswith("campfire_fire"):
        return mat(0.62, depth=9.0, grain=0.28, struct=0.85, emis=1.0, hscale=1.0)
    if has(n, "nether_portal", "end_portal", "end_gateway"):
        return mat(0.15, sss=0.30, depth=6.0, grain=0.10, struct=0.40,
                   emis=0.55, hscale=0.5)

    # --- 强发光体 ---
    if has(n, "glowstone", "shroomlight", "sea_lantern", "froglight",
           "redstone_lamp", "jack_o_lantern", "end_rod", "beacon"):
        return mat(0.72, por=0.05, sss=0.30, depth=9.0, grain=0.16,
                   struct=0.55, emis=0.95, hscale=0.8)
    if has(n, "_torch", "torch_") or n.endswith("_torch") or has(n, "candle_lit"):
        return mat(0.75, por=0.10, sss=0.10, depth=8.0, grain=0.14,
                   struct=0.60, emis=0.95, hscale=0.8)
    if has(n, "lantern") and not has(n, "_lit"):
        return mat(0.35, metal=0.75, por=0.05, depth=12.0, grain=0.10,
                   struct=0.70, emis=0.85, hscale=0.9)
    if has(n, "magma"):
        return mat(0.70, por=0.10, depth=10.0, grain=0.22, struct=0.70,
                   emis=0.80, hscale=0.9)
    if has(n, "respawn_anchor"):
        return mat(0.70, por=0.10, sss=0.10, depth=10.0, grain=0.16,
                   struct=0.60, emis=0.70, hscale=0.9)
    if has(n, "crying_obsidian"):
        return mat(0.25, por=0.05, sss=0.10, depth=8.0, grain=0.10,
                   struct=0.45, emis=0.40, hscale=0.6)
    if has(n, "amethyst_cluster", "amethyst_bud"):
        return mat(0.18, por=0.05, sss=0.45, depth=14.0, grain=0.14,
                   struct=0.80, emis=0.35, hscale=1.0)
    if has(n, "glow_lichen", "glow_berries"):
        return mat(0.85, por=0.70, sss=0.60, depth=12.0, grain=0.16,
                   struct=0.55, emis=0.55, hscale=0.8)
    if has(n, "_lit", "_on", "_powered") and not has(n, "oxidized"):
        return mat(0.70, por=0.15, sss=0.10, depth=9.0, grain=0.14,
                   struct=0.60, emis=0.70, hscale=0.8)

    # --- 矿石：底层石 + 矿物颗粒金属遮罩（须在铜类之前）---
    if n.endswith("_ore") or has(n, "nether_gold", "nether_quartz"):
        return mat(0.85, por=0.30, depth=12.0, grain=0.12, struct=0.65,
                   hscale=1.0, ore=True)

    # --- 粗金属原矿块：整体仍是矿块，靠金属 ID + albedo 着色 ---
    if has(n, "raw_copper", "raw_iron", "raw_gold"):
        return mat(0.45, metal=1.0, por=0.05, depth=13.0, grain=0.16,
                   struct=0.70, hscale=1.0)
    if has(n, "ancient_debris"):
        return mat(0.72, metal=0.85, por=0.10, depth=13.0, grain=0.16,
                   struct=0.70, hscale=1.0)

    # --- 金属块（含铜的氧化阶段）---
    if has(n, "iron_block", "gold_block", "diamond_block", "emerald_block",
           "redstone_block", "lapis_block", "netherite_block",
           "copper_block", "iron_bars", "iron_door", "iron_trapdoor",
           "anvil", "hopper", "cauldron", "chain", "bell", "heavy_core",
           "iron_chain", "copper_chain", "copper_bars", "copper_grate",
           "copper_door", "copper_trapdoor", "chiseled_copper", "cut_copper",
           "lightning_rod", "copper_bulb"):
        rough = 0.32
        if has(n, "oxidized"):
            rough = 0.78
        elif has(n, "weathered"):
            rough = 0.62
        elif has(n, "exposed"):
            rough = 0.48
        if has(n, "gold_block", "diamond_block", "emerald_block", "netherite_block"):
            rough = 0.22
        return mat(rough, metal=1.0, por=0.02, depth=13.0, grain=0.08,
                   struct=0.75, hscale=0.9)
    # 煤块不是金属，只是比较光滑的脆性矿物
    if has(n, "coal_block"):
        return mat(0.70, por=0.15, sss=0.0, depth=11.0, grain=0.20,
                   struct=0.75, f0=0.05, hscale=0.9)
    if has(n, "oxidized", "weathered", "exposed", "waxed"):
        rough = 0.78
        if has(n, "weathered"):
            rough = 0.62
        elif has(n, "exposed"):
            rough = 0.48
        return mat(rough, metal=1.0, por=0.02, depth=13.0, grain=0.09,
                   struct=0.75, hscale=0.9)
    if has(n, "spawner"):
        return mat(0.55, metal=0.85, por=0.05, depth=14.0, grain=0.10,
                   struct=0.80, hscale=1.0)

    # --- 玻璃 / 冰 / 黏性块 ---
    if has(n, "glass"):
        return mat(0.06, sss=0.15, depth=4.0, grain=0.03, struct=0.30, hscale=0.3)
    if has(n, "ice"):
        return mat(0.14, por=0.05, sss=0.55, depth=6.0, grain=0.10,
                   struct=0.45, hscale=0.5)
    if has(n, "slime", "honey_block", "honeycomb_block"):
        return mat(0.08, por=0.10, sss=0.70, depth=6.0, grain=0.08,
                   struct=0.40, hscale=0.5)
    if has(n, "snow"):
        return mat(0.90, por=0.85, sss=0.60, depth=10.0, grain=0.20,
                   struct=0.45, hscale=0.9)

    # --- 羊毛 / 地毯 / 床 ---
    if has(n, "wool", "carpet", "banner") or (has(n, "bed") and not has(n, "bedrock")):
        return mat(0.98, por=1.00, sss=0.25, depth=14.0, grain=0.30,
                   struct=0.30, hscale=0.7)

    # --- 抛光 / 平滑 / 致密装饰石 ---
    if has(n, "polished", "smooth_", "quartz", "purpur", "prismarine",
           "obsidian", "chiseled") and not has(n, "sandstone", "blackstone_bricks"):
        return mat(0.40, por=0.10, sss=0.05, depth=8.0, grain=0.07,
                   struct=0.55, hscale=0.6)

    # --- 草木 / 叶 / 作物 / 花 / 珊瑚 ---
    if has(n, "leaves", "sapling", "grass", "fern", "flower", "tulip", "orchid",
           "daisy", "bush", "roots", "vine", "kelp", "cactus", "moss", "mushroom",
           "sprouts", "dripleaf", "lily", "coral", "stem", "blossom", "propagule",
           "wheat", "carrots", "potatoes", "beetroots", "nether_wart", "cocoa",
           "melon_stem", "pumpkin_stem", "pitcher", "torchflower", "wither_rose",
           "sweet_berry", "glow_lichen", "lily_pad", "poppy", "dandelion",
           "blue_orchid", "allium", "azure_bluet", "cornflower", "leaf_litter",
           "hanging_moss", "eyeblossom"):
        return mat(0.92, por=0.90, sss=0.75, depth=12.0, grain=0.18,
                   struct=0.55, hscale=0.9)

    # --- 木 / 竹 / 菌丝木 ---
    if has(n, "planks", "log", "_stem", "wood", "door", "trapdoor", "shelf",
           "sign", "fence", "barrel", "bookshelf", "crafting_table", "podium",
           "bamboo", "crimson", "warped", "hyphae", "_table", "lectern",
           "note_block", "jukebox", "beehive", "bee_nest", "creaking",
           "chorus_plant", "chorus_flower", "pale_oak"):
        return mat(0.78, por=0.50, sss=0.10, depth=9.0, grain=0.14,
                   struct=0.60, hscale=0.8)

    # --- 土 / 沙 / 砾 / 泥 ---
    if has(n, "dirt", "sand", "gravel", "clay", "mud", "podzol", "mycelium",
           "coarse_dirt", "soul_sand", "soul_soil", "ash", "concrete_powder",
           "hay"):
        return mat(0.95, por=0.85, sss=0.10, depth=11.0, grain=0.26,
                   struct=0.45, hscale=1.0)

    # --- 石 / 砖 / 混凝土 / 陶瓦 / 下界 ---
    if has(n, "stone", "cobble", "deepslate", "tuff", "andesite", "diorite",
           "granite", "blackstone", "basalt", "sandstone", "terracotta",
           "concrete", "brick", "nether_bricks", "end_stone", "calcite",
           "netherrack", "cinnabar", "sulfur", "resin", "bedrock", "bone_block",
           "honeycomb", "shulker", "sponge", "dripstone", "sculk",
           "magma_block", "cobweb", "amethyst_block", "budding_amethyst"):
        return mat(0.85, por=0.35, sss=0.05, depth=10.0, grain=0.13,
                   struct=0.60, hscale=0.9)

    # --- 补漏（放在最后，避免抢占更具体的分类）---
    if has(n, "torch", "soul_fire", "soul_campfire", "sea_pickle", "glow_item_frame"):
        return mat(0.75, por=0.10, sss=0.25, depth=8.0, grain=0.14,
                   struct=0.60, emis=0.85, hscale=0.8)
    if has(n, "rail"):
        return mat(0.35, metal=0.90, por=0.05, depth=13.0, grain=0.10,
                   struct=0.75, hscale=0.9)
    if has(n, "vault", "brewing_stand", "conduit", "tripwire_hook"):
        return mat(0.35, metal=0.70, por=0.05, depth=13.0, grain=0.10,
                   struct=0.75, hscale=0.9)
    if has(n, "furnace", "smoker", "grindstone", "observer", "dispenser",
           "dropper", "piston", "comparator", "repeater", "daylight_detector",
           "target", "lever", "lodestone", "structure_block", "jigsaw",
           "blast_furnace", "command_block", "crafter", "test_block",
           "test_instance_block"):
        return mat(0.62, por=0.25, sss=0.05, depth=11.0, grain=0.12,
                   struct=0.60, hscale=0.9)
    if has(n, "scaffolding", "ladder", "composter", "item_frame", "loom",
           "tripwire"):
        return mat(0.78, por=0.50, sss=0.10, depth=9.0, grain=0.14,
                   struct=0.60, hscale=0.8)
    if has(n, "candle"):
        return mat(0.60, por=0.15, sss=0.55, depth=9.0, grain=0.12,
                   struct=0.55, hscale=0.8)
    if has(n, "farmland", "dirt_path", "tnt"):
        return mat(0.95, por=0.85, sss=0.10, depth=11.0, grain=0.24,
                   struct=0.45, hscale=1.0)
    if has(n, "cake", "melon", "pumpkin", "egg", "frogspawn", "dried_ghast"):
        return mat(0.70, por=0.55, sss=0.40, depth=10.0, grain=0.16,
                   struct=0.55, hscale=0.8)
    if has(n, "azalea", "sugar_cane", "lilac", "peony", "pink_petals", "shrub",
           "rose_bush", "sunflower"):
        return mat(0.92, por=0.90, sss=0.75, depth=12.0, grain=0.18,
                   struct=0.55, hscale=0.9)

    return DEFAULT


# 破坏动画 / 调试用纹理不参与 PBR
SKIP_PREFIXES = ("destroy_stage_", "destroy_oit_stage_", "debug")

# POM 高度下限：对应约 9% 的方块深度（255 = 与表面齐平）
HEIGHT_MIN = 160.0


# ---------------------------------------------------------------- 贴图生成


def height_map(rgba, p, rng):
    """由 albedo 推导高度场（0..1）。"""
    r, g, b, a = rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3]
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    opaque = alpha_mask(a)

    h, w = luma.shape
    structure = luma - box_blur_wrap(luma, passes=2)       # 暗缝/暗纹 → 凹陷
    grain = fbm(h, w, rng, base_cells=max(2, h // 4), octaves=3) - 0.5

    height = p["struct"] * structure + p["grain"] * grain
    height *= opaque
    height = box_blur_wrap(height, passes=1)

    lo, hi = height.min(), height.max()
    if hi - lo > 1e-6:
        height = (height - lo) / (hi - lo)
    else:
        height = np.full_like(height, 0.5)
    return height


def normal_from_height(height, depth, hscale=1.0):
    """法线纹理：R/G = 法线 XY（DirectX Y-），B = 材质 AO，A = POM 高度。"""
    gx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.5
    gy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.5
    nx = -gx * depth
    ny = -gy * depth              # DirectX 约定：绿通道朝下，故取负
    nz = np.ones_like(nx)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny = nx * inv, ny * inv

    # 材质 AO：低于邻域平均高度的地方（砖缝、石缝）压暗环境光
    cavity = box_blur_wrap(height, passes=2) - height
    cavity = np.clip(cavity, 0.0, None)
    cmax = cavity.max()
    if cmax > 1e-6:
        cavity /= cmax
    ao = 1.0 - 0.60 * smoothstep(0.06, 0.90, cavity)

    out = np.zeros(height.shape + (4,), dtype=np.float32)
    out[..., 0] = nx * 0.5 + 0.5
    out[..., 1] = ny * 0.5 + 0.5
    out[..., 2] = np.clip(ao, 1.0 / 255.0, 1.0)
    # 高度：255 = 与方块表面齐平、0 = 25% 深度。这里只用到较浅的一段，
    # 免得开启 POM 后整块纹理看起来"内陷"。
    lo, hi = float(height.min()), float(height.max())
    hh = (height - lo) / (hi - lo) if hi - lo > 1e-6 else np.full_like(height, 0.5)
    out[..., 3] = np.clip((HEIGHT_MIN + (255.0 - HEIGHT_MIN) * hh * hscale)
                          / 255.0, 1.0 / 255.0, 1.0)
    return out


def ore_mask(rgba):
    """与主色差异大的像素 = 矿物颗粒。"""
    rgb, a = rgba[..., :3], rgba[..., 3]
    px = rgb[a > 0.4]
    if px.size == 0:
        return np.zeros(rgb.shape[:2], dtype=np.float32)
    q = np.round(px * 12.0).astype(int)
    keys, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
    bg = (keys[counts.argmax()] / 12.0).astype(np.float32)
    dist = np.linalg.norm(rgb - bg[None, None, :], axis=2) / 1.7320508
    return smoothstep(0.10, 0.22, dist)


def specular_map(rgba, p, mask_ore):
    """labPBR 1.3 高光贴图。

    R = 感知平滑度 1-sqrt(roughness)
    G = F0（1~229 线性反射率）/ 硬编码金属 ID（230~255，255 = albedo 充当 F0）
    B = 0~64 孔隙率 / 65~255 次表面散射（两者共用同一通道）
    A = 自发光强度（0~254，255 会被光影忽略，所以最高只写 254）
    """
    h, w = rgba.shape[:2]
    a = rgba[..., 3]
    opaque = alpha_mask(a)

    rough = np.full((h, w), p["rough"], dtype=np.float32)

    if p["metal"] > 0.5:
        # 金属整张用硬编码金属 ID，albedo 只拿来着色，不参与漫反射
        f0 = np.full((h, w), float(p["metal_id"]), dtype=np.float32)
        luma = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
        rough = np.clip(rough * (1.25 - 0.55 * luma), 0.05, 1.0)   # 亮部更光、暗部更哑
    else:
        f0 = np.full((h, w), p["f0"] * 255.0, dtype=np.float32)

    if p.get("ore") and mask_ore is not None:
        m = mask_ore
        oid = p.get("ore_id", METAL_CUSTOM)
        if oid == 0:
            # 煤 / 石英：有光泽但不是金属，用偏高一点的绝缘体 F0
            rough = rough * (1 - m) + 0.34 * m
            f0 = f0 * (1 - m) + 0.08 * 255.0 * m
        else:
            rough = rough * (1 - m) + 0.28 * m
            f0 = f0 * (1 - m) + float(oid) * m

    out = np.zeros((h, w, 4), dtype=np.float32)
    out[..., 0] = 1.0 - np.sqrt(np.clip(rough, 0.0, 1.0))
    out[..., 1] = np.clip(f0, 1.0, 255.0) / 255.0

    if p["sss"] >= 0.03:
        out[..., 2] = (65.0 + p["sss"] * 190.0) / 255.0   # 次表面散射区间
    else:
        out[..., 2] = (p["por"] * 64.0) / 255.0           # 孔隙率区间

    out[..., 3] = min(p["emis"], 0.995)                   # 0~254，永不写 255

    # 透明像素：平滑度/孔隙率/自发光归零，F0 退回绝缘体默认值
    op = opaque
    out[..., 0] *= op
    out[..., 1] = out[..., 1] * op + 0.04 * (1.0 - op)
    out[..., 2] *= op
    out[..., 3] *= op
    return out


def emissive_map(rgba, p):
    """OptiFine 发光贴图：不发光处为纯黑，发光处为发光色，alpha 恒为 1。"""
    rgb = rgba[..., :3]
    luma = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    weight = smoothstep(0.28, 0.72, luma)
    if p["emis"] >= 1.0:
        weight = np.maximum(weight, 0.9)
    out = rgb * (p["emis"] * weight)[..., None]
    alpha = np.ones(rgb.shape[:2], dtype=np.float32)
    return np.clip(np.concatenate([out, alpha[..., None]], axis=2), 0.0, 1.0)


def save_png(arr, path):
    img = Image.fromarray(np.clip(np.round(arr * 255.0), 0, 255).astype(np.uint8), mode="RGBA")
    img.save(path, optimize=True)


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", required=True)
    ap.add_argument("--pa", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--albedo-out", default=None)
    args = ap.parse_args()

    van_dir, pa_dir, out_dir = args.vanilla, args.pa, args.out
    os.makedirs(out_dir, exist_ok=True)
    if args.albedo_out:
        os.makedirs(args.albedo_out, exist_ok=True)

    names = [f[:-4] for f in sorted(os.listdir(van_dir)) if f.endswith(".png")]
    skipped = [n for n in names if n.startswith(SKIP_PREFIXES)]
    names = [n for n in names if not n.startswith(SKIP_PREFIXES)]
    print(f"纹理总数: {len(names)}（跳过破坏动画/调试 {len(skipped)}）")

    stats = {"n": 0, "e": 0, "pa": 0}
    fallback = []
    for idx, name in enumerate(names):
        van_path = os.path.join(van_dir, name + ".png")
        pa_path = os.path.join(pa_dir, name + ".png")
        src = pa_path if os.path.exists(pa_path) else van_path
        if src is pa_path:
            stats["pa"] += 1

        rgba = load_rgba(src)
        p = classify(name)
        if p.get("_default"):
            fallback.append(name)
        rng = np.random.default_rng(abs(hash(name)) % (2**32))

        height = height_map(rgba, p, rng)
        nrm = normal_from_height(height, p["depth"], hscale=p["hscale"])
        m_ore = ore_mask(rgba) if p.get("ore") else None
        spec = specular_map(rgba, p, m_ore)

        save_png(nrm, os.path.join(out_dir, name + "_n.png"))
        save_png(spec, os.path.join(out_dir, name + "_s.png"))
        stats["n"] += 1

        if p["emis"] > 0.0:
            save_png(emissive_map(rgba, p), os.path.join(out_dir, name + "_e.png"))
            stats["e"] += 1

        # 动画纹理：法线 / 高光跟随帧动画
        src_mcm = van_path + ".mcmeta"
        if os.path.exists(src_mcm):
            for suffix in ("_n", "_s"):
                shutil.copyfile(src_mcm, os.path.join(out_dir, name + suffix + ".png.mcmeta"))
            if p["emis"] > 0.0:
                shutil.copyfile(src_mcm, os.path.join(out_dir, name + "_e.png.mcmeta"))

        if args.albedo_out:
            shutil.copyfile(src, os.path.join(args.albedo_out, name + ".png"))
            if os.path.exists(src_mcm):
                shutil.copyfile(src_mcm, os.path.join(args.albedo_out, name + ".png.mcmeta"))

        if (idx + 1) % 300 == 0:
            print(f"  ... {idx + 1}/{len(names)}")

    print(f"完成: 法线 {stats['n']} / 自发光 {stats['e']} / 取自 Programmer Art {stats['pa']}")
    print(f"未命中分类（走默认材质）: {len(fallback)}")
    if fallback:
        print("  " + " ".join(fallback))
    return 0


if __name__ == "__main__":
    sys.exit(main())
