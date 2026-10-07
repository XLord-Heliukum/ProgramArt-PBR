#!/usr/bin/env python3
"""从 Minecraft 安装中提取 ProgramArt PBR 所需的全部素材。

产出：
  work/pa/assets/minecraft/textures/block/*.png    Programmer Art（占位/AO/金属参考）
  work/vanilla/block/*.png                         指定版本的原版方块纹理（补全新方块）
  work/vanilla/block/*.png.mcmeta                  动画纹理的帧描述

为什么 Programmer Art 不在 jar 里：它是 Mojang 通过 asset index 单独下发的资源包对象，
key 为 minecraft/resourcepacks/programmer_art.zip，文件落在 assets/objects/<hash前2位>/<hash>。
客户端 jar 里只有当前版本的原版纹理，两者不可互相替代。

用法：
  python3 tools/fetch_sources.py                     # 自动选本机已安装的最新版本
  python3 tools/fetch_sources.py --version 26.3
  python3 tools/fetch_sources.py --mc /path/to/.minecraft --version 1.20.1
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = ROOT + "/work"
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def default_mc_dir():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/minecraft")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), ".minecraft")
    return os.path.expanduser("~/.minecraft")


def http_json(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ProgramArt-PBR/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    return json.loads(data)


def http_file(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "ProgramArt-PBR/1.0"})
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(dest)


def pick_local_version(mc):
    """本机已下载的版本里挑一个 release 的最新 mise 号（按目录名粗略排序不可靠，用 json 的 releaseTime）。"""
    vdir = os.path.join(mc, "versions")
    best, best_t = None, ""
    if not os.path.isdir(vdir):
        return None
    for name in sorted(os.listdir(vdir)):
        p = os.path.join(vdir, name, name + ".json")
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        # 快照/预发布跳过：它们同样能用，但正式版的纹理列表才是发布目标
        if d.get("type") != "release":
            continue
        t = d.get("releaseTime", "")
        if t > best_t:
            best, best_t = name, t
    return best


def load_version_json(mc, version, explicit_ver):
    """优先本机版本 json；没有就从 Mojang manifest 拉取。"""
    local = os.path.join(mc, "versions", version, version + ".json")
    if os.path.exists(local):
        return json.load(open(local, encoding="utf-8"))
    if not explicit_ver:
        return None
    print(f"  本机没有 {version} 的版本 json，从 Mojang manifest 拉取")
    man = http_json(MANIFEST)
    for v in man["versions"]:
        if v["id"] == version:
            return http_json(v["url"], os.path.join(W, f"{version}.json"))
    raise SystemExit(f"版本 {version} 不在 Mojang manifest 里")


def get_programmer_art(mc, vjson):
    """取出 Programmer Art 资源包并解压。"""
    dest_dir = os.path.join(W, "pa")
    if os.path.isdir(dest_dir) and os.path.isdir(dest_dir + "/assets/minecraft/textures/block"):
        print(f"  Programmer Art 已存在，跳过：{dest_dir}")
        return dest_dir

    ai = vjson.get("assetIndex")
    if not ai:
        raise SystemExit("版本 json 里没有 assetIndex")
    idx_path = os.path.join(mc, "assets", "indexes", ai["id"] + ".json")
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path, encoding="utf-8"))
    else:
        print(f"  本地缺 asset index {ai['id']}，下载")
        idx = http_json(ai["url"], idx_path)

    key = "minecraft/resourcepacks/programmer_art.zip"
    obj = idx["objects"].get(key)
    if not obj:
        raise SystemExit(f"asset index 里没有 {key}（该版本可能移除了 Programmer Art）")

    h = obj["hash"]
    cached = os.path.join(mc, "assets", "objects", h[:2], h)
    if os.path.exists(cached) and os.path.getsize(cached) == obj["size"]:
        src = cached
    else:
        print("  本地缓存里没有 Programmer Art，从 Mojang 下载")
        src = os.path.join(W, "objects", h[:2], h)
        http_file(f"https://resources.download.minecraft.net/{h[:2]}/{h}", src)

    zpath = os.path.join(W, "programmer_art.zip")
    shutil.copyfile(src, zpath)
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest_dir)
    n = len([f for f in os.listdir(dest_dir + "/assets/minecraft/textures/block")
             if f.endswith(".png")])
    print(f"  Programmer Art：{n} 个方块纹理  ({dest_dir})")
    return dest_dir


def get_client_jar(mc, vjson, version):
    """保证客户端 jar 就位，返回路径。"""
    dest = os.path.join(W, f"{version}-client.jar")
    local = os.path.join(mc, "versions", version, f"{version}.jar")
    url = vjson["downloads"]["client"]["url"]
    size = vjson["downloads"]["client"]["size"]

    if os.path.exists(local) and os.path.getsize(local) == size:
        if os.path.abspath(local) != os.path.abspath(dest):
            shutil.copyfile(local, dest)
        return dest
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return dest
    print(f"  下载客户端 jar（{size / 1048576:.1f} MB）")
    http_file(url, dest)
    return dest


def extract_block_textures(jar):
    """从客户端 jar 抽出 block 纹理与其 .mcmeta。"""
    dst = os.path.join(W, "vanilla")
    prefix = "assets/minecraft/textures/block/"
    npng = nmcm = 0
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            if not name.startswith(prefix):
                continue
            if not (name.endswith(".png") or name.endswith(".png.mcmeta")):
                continue
            out = os.path.join(dst, name[len("assets/minecraft/textures/"):])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                if name.endswith(".png"):
                    npng += 1
                else:
                    nmcm += 1
                continue
            with z.open(name) as fi, open(out, "wb") as fo:
                fo.write(fi.read())
            if name.endswith(".png"):
                npng += 1
            else:
                nmcm += 1
    print(f"  原版 block 纹理：{npng} 个 png + {nmcm} 个 mcmeta  ({dst}/block)")
    return dst


def main():
    ap = argparse.ArgumentParser(description="抓取 ProgramArt PBR 的输入素材")
    ap.add_argument("--mc", default=default_mc_dir(), help="Minecraft 数据目录")
    ap.add_argument("--version", default=None, help="游戏版本，默认本机最新 release")
    ap.add_argument("--no-pa", action="store_true", help="跳过 Programmer Art（只取原版纹理）")
    args = ap.parse_args()

    mc = os.path.expanduser(args.mc)
    if not os.path.isdir(mc):
        raise SystemExit(f"找不到 Minecraft 目录：{mc}\n用 --mc 指定。")

    version = args.version
    explicit = version is not None
    if not version:
        version = pick_local_version(mc)
        if not version:
            raise SystemExit("没能在本机 versions/ 里找到 release 版本，用 --version 指定")
    print(f"目标版本：{version}")

    os.makedirs(W, exist_ok=True)
    vjson = load_version_json(mc, version, explicit)
    if vjson is None:
        raise SystemExit(f"本机没有 {version} 的版本 json")

    if not args.no_pa:
        get_programmer_art(mc, vjson)
    jar = get_client_jar(mc, vjson, version)
    extract_block_textures(jar)
    print("素材就绪，下一步：python3 tools/gen_pbr.py "
          "--vanilla work/vanilla/block --pa work/pa/assets/minecraft/textures/block "
          "--out work/pbr/assets/minecraft/textures/block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
