#!/usr/bin/env python3
"""组装并打包 ProgramArt PBR 材质包（图标 / pack.mcmeta / zip）。"""
import argparse
import json
import os
import shutil
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = ROOT + "/work"
PBR = W + "/pbr"
PA_BLOCK = W + "/pa/assets/minecraft/textures/block"
VAN_BLOCK = W + "/vanilla/block"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shadelib import shade       # noqa: E402

# pack.mcmeta 版本声明：
#   1.20 ~ 1.21.8  -> supported_formats（1.21.9 起会被拒绝，必须分开发）
#   1.21.9 ~ 26.3  -> min_format / max_format
#   pack_format: 1.20 = 15，1.21.9 = 69，26.3 = 97.1（取整数 97）
MCMETA_LEGACY = {"pack_format": 15, "supported_formats": [15, 64]}
MCMETA_MODERN = {"pack_format": 97, "min_format": [69, 0], "max_format": [97, 1]}

DESC_LEGACY = ("\u00a76ProgramArt \u00a7fPBR\u00a7r\n"
               "\u00a77Programmer Art \u00b7 labPBR 1.3 \u00b7 \u00a7f1.20 \u2013 1.21.8\n"
               "\u00a78\u9700 Iris + \u652f\u6301 labPBR \u7684\u5149\u5f71")
DESC_MODERN = ("\u00a76ProgramArt \u00a7fPBR\u00a7r\n"
               "\u00a77Programmer Art \u00b7 labPBR 1.3 \u00b7 \u00a7f1.21.9 \u2013 26.3\n"
               "\u00a78\u9700 Iris + \u652f\u6301 labPBR \u7684\u5149\u5f71")
DESC_FULL = ("\u00a76ProgramArt \u00a7fPBR \u00a78[Full]\u00a7r\n"
             "\u00a77Programmer Art \u00b7 labPBR 1.3 \u00b7 \u00a7f1.20 \u2013 26.3\n"
             "\u00a78\u81ea\u5e26 albedo\uff0c\u65e0\u9700\u53e0\u5b98\u65b9 Programmer Art")

# 声明材质格式，光影靠这个宏选择 labPBR 解码路径
TEXTURE_PROPS = "format=lab-pbr/1.3\n"


def load(path, folder):
    with Image.open(os.path.join(folder, path)) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0


def make_icon(out_path):
    """左：Programmer Art 原图；右：同样的方块经法线/高光渲染后的效果。"""
    names = ["oak_planks", "bricks", "iron_block", "cobblestone"]
    alb_grid = np.zeros((32, 32, 4), dtype=np.float32)
    lit_grid = np.zeros((32, 32, 3), dtype=np.float32)
    for i, n in enumerate(names):
        r, c = divmod(i, 2)
        alb = load(n + ".png", PA_BLOCK)
        nrm = load(n + "_n.png", PBR + "/assets/minecraft/textures/block")
        spc = load(n + "_s.png", PBR + "/assets/minecraft/textures/block")
        alb_grid[r * 16:(r + 1) * 16, c * 16:(c + 1) * 16] = alb
        lit_grid[r * 16:(r + 1) * 16, c * 16:(c + 1) * 16] = shade(alb, nrm, spc)

    icon = Image.new("RGB", (64, 64), (18, 18, 22))
    d = ImageDraw.Draw(icon)

    def to_img(a):
        rgb = np.round(np.clip(a[..., :3], 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(rgb).resize((32, 32), Image.NEAREST)

    icon.paste(to_img(alb_grid), (0, 16))
    icon.paste(to_img(lit_grid), (32, 16))
    d.line([(31, 14), (31, 49)], fill=(70, 70, 84))
    d.line([(32, 14), (32, 49)], fill=(120, 120, 140))
    d.rectangle([0, 15, 63, 48], outline=(90, 90, 105))
    for i in range(14):
        v = int(26 + i * 3)
        d.line([(i * 5, i), (63 - i, i)], fill=(v, v + 4, v + 14))
    for i in range(14):
        v = int(48 - i * 3)
        d.line([(0, 63 - i), (63, 63 - i)], fill=(max(v, 16), max(v, 16), max(v, 22)))
    icon.save(out_path)
    print("icon ->", out_path)


def write_pack(root, mcmeta, icon_path):
    """写入 pack.mcmeta / texture.properties / pack.png / 全部 PBR 贴图。"""
    os.makedirs(root + "/assets/minecraft/textures", exist_ok=True)
    with open(root + "/pack.mcmeta", "w", encoding="utf-8") as f:
        json.dump({"pack": mcmeta}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(root + "/assets/minecraft/textures/texture.properties", "w",
              encoding="utf-8") as f:
        f.write(TEXTURE_PROPS)
    if icon_path:
        Image.open(icon_path).save(root + "/pack.png")

    src = PBR + "/assets/minecraft/textures/block"
    dst = root + "/assets/minecraft/textures/block"
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(src)):
        if f.endswith(("_n.png", "_s.png", "_e.png")) or ".png.mcmeta" in f:
            with open(os.path.join(src, f), "rb") as fi, open(os.path.join(dst, f), "wb") as fo:
                fo.write(fi.read())
            n += 1
    return n


def add_albedo(root):
    """Full 版：补上最终使用的 albedo（Programmer Art 优先，其余用原版）。"""
    dst = root + "/assets/minecraft/textures/block"
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(VAN_BLOCK)):
        if not f.endswith(".png"):
            continue
        src = os.path.join(PA_BLOCK, f)
        if not os.path.exists(src):
            src = os.path.join(VAN_BLOCK, f)
        with open(src, "rb") as fi, open(os.path.join(dst, f), "wb") as fo:
            fo.write(fi.read())
        n += 1
        mcm = os.path.join(VAN_BLOCK, f + ".mcmeta")
        if os.path.exists(mcm):
            with open(mcm, "rb") as fi, open(os.path.join(dst, f + ".mcmeta"), "wb") as fo:
                fo.write(fi.read())
    return n


README = """# ProgramArt PBR —— 基于 Programmer Art 的 labPBR 材质包

用 Minecraft Java 版官方 **Programmer Art**（经典 16x16 老纹理）作为 albedo，
为 **1.20 ~ 26.3** 的方块补上程序化生成的 labPBR 1.3 物理材质贴图。

## 怎么用

1. 装好 **Iris**（或 OptiFine + 支持 labPBR 的光影包），例如
   Complementary / BSL / Photon / MakeUp UltraFast / Noble。
2. 把 zip 放进 `resourcepacks` 并启用。
   - **Add-on 版**（文件名里没有 Full）只含 `_n` / `_s` / `_e` 贴图，
     需要先启用官方 **Programmer Art** 资源包，再把本包叠在它**上面**。
   - **Full 版**自带完整 albedo，可以独立使用，不用叠官方包。
3. 光影设置里打开 PBR / 法线贴图 / 视差（POM）相关选项。

## 四个版本怎么选

| 包 | 适用版本 | 说明 |
| --- | --- | --- |
| `...-mc1.20-1.21.8.zip` | 1.20 ~ 1.21.8 | Add-on，用 `supported_formats` 声明 |
| `...-mc1.21.9-26.3.zip` | 1.21.9 ~ 26.3 | Add-on，用 `min_format` / `max_format` 声明 |
| `...-Full-mc1.20-1.21.8.zip` | 1.20 ~ 1.21.8 | 自带 albedo |
| `...-Full-mc1.21.9-26.3.zip` | 1.21.9 ~ 26.3 | 自带 albedo |

之所以要分成两代：1.21.9 起 `supported_formats` 被移除、只认 `min_format` /
`max_format`，反过来老版本又不认识新字段，所以没法用一个 pack.mcmeta 通吃。

## 贴图说明（labPBR 1.3）

`_n.png` 法线 / 环境光遮蔽 / 视差高度

| 通道 | 含义 |
| --- | --- |
| R | 法线 X（向右） |
| G | 法线 Y（DirectX 约定：绿通道朝下） |
| B | 材质 AO（0 = 全遮蔽，255 = 无遮蔽） |
| A | POM 高度（0 = 25% 深度，255 = 与表面齐平） |

`_s.png` 高光 / 材质属性

| 通道 | 含义 |
| --- | --- |
| R | 感知平滑度 `1 - sqrt(粗糙度)` |
| G | F0（1~229 线性反射率）或硬编码金属 ID（230~255，255 = 用 albedo 当 F0） |
| B | 孔隙率（0~64）或次表面散射（65~255） |
| A | 自发光强度（0~254，255 会被光影忽略） |

`_e.png` 是给 OptiFine 的发光贴图，不开光影时发光方块也能亮起来。

## 材质设定要点

- 粗糙度按物理值书写，输出时换算成 labPBR 的感知平滑度。
- 金属块走硬编码金属 ID：铁 230 / 金 231 / 铜 234，其余用 255（albedo 当 F0）。
- 矿石只让**矿物颗粒**变金属，底板石头仍是绝缘体。
- 煤块不是金属；氧化铜仍保留金属 ID，只提高粗糙度。
- 孔隙率与次表面散射共用蓝通道，本包只在真正透光的材质（树叶、冰、雪、
  史莱姆、蜜块等）上用 SSS，其余一律给孔隙率。
- 玻璃、冰这类半透明贴图只按 alpha 做硬阈值遮罩，不会把平滑度一起压没。

## 生成方式

全部贴图由 `tools/gen_pbr.py` 从 albedo 程序化生成：
局部亮度对比 -> 高度场 -> 梯度法线（循环边界，无缝）+ fBm 颗粒噪声，
再按纹理名规则匹配材质参数。不是手绘，也不是搬运别的包的素材。

## 兼容提示

包内 `assets/minecraft/textures/texture.properties` 声明了 `format=lab-pbr/1.3`。
请使用支持 labPBR 的光影包；不支持的光影会把法线蓝通道当 Z 读，表现会不对。
"""


def zip_dir(root, zip_path):
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _, files in os.walk(root):
            for f in sorted(files):
                full = os.path.join(base, f)
                z.write(full, os.path.relpath(full, root))
                count += 1
    return count


# 模式 -> (目录名, zip 名, mcmeta 模板, 说明文字, 是否带 albedo)
MODES = {
    "legacy":       ("ProgramArt-PBR-1.20-1.21.8", "ProgramArt-PBR-mc1.20-1.21.8.zip",
                     MCMETA_LEGACY, DESC_LEGACY, False),
    "modern":       ("ProgramArt-PBR-1.21.9-26.3", "ProgramArt-PBR-mc1.21.9-26.3.zip",
                     MCMETA_MODERN, DESC_MODERN, False),
    "legacy-full":  ("ProgramArt-PBR-Full-1.20-1.21.8",
                     "ProgramArt-PBR-Full-mc1.20-1.21.8.zip",
                     MCMETA_LEGACY, DESC_FULL, True),
    "modern-full":  ("ProgramArt-PBR-Full-1.21.9-26.3",
                     "ProgramArt-PBR-Full-mc1.21.9-26.3.zip",
                     MCMETA_MODERN, DESC_FULL, True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=sorted(MODES))
    ap.add_argument("--no-icon", action="store_true")
    args = ap.parse_args()

    name, zip_name, meta_tpl, desc, full = MODES[args.mode]
    out_root = ROOT + "/output"
    os.makedirs(out_root, exist_ok=True)

    icon = W + "/pack_icon.png"
    if not args.no_icon:
        make_icon(icon)

    root = W + "/build/" + name
    if os.path.exists(root):
        shutil.rmtree(root)

    mcmeta = dict(meta_tpl)
    mcmeta["description"] = desc

    n_pbr = write_pack(root, mcmeta, icon)
    n_alb = add_albedo(root) if full else 0
    with open(root + "/README.md", "w", encoding="utf-8") as f:
        f.write(README)

    zip_path = out_root + "/" + zip_name
    n = zip_dir(root, zip_path)
    size = os.path.getsize(zip_path)
    print(f"{zip_name}: {n} 个文件（PBR {n_pbr} / albedo {n_alb}）, {size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
