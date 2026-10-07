"""共享的近似 PBR 光栅化（仅用于预览/图标，不是游戏内渲染）。"""
import numpy as np

_L = np.array([0.42, 0.66, 0.62], dtype=np.float32)
_L /= np.linalg.norm(_L)
_H = _L + np.array([0.0, 0.0, 1.0], np.float32)
_H /= np.linalg.norm(_H)
# 天光近似：上方偏亮的冷色半球光，金属靠它才有可见颜色
_SKY = np.array([0.58, 0.66, 0.82], dtype=np.float32)


def shade(albedo, normal, spec):
    """漫反射 + Blinn 镜面 + 天光环境反射近似；参数由 labPBR 通道驱动。

    仅供预览图 / 图标使用，不代表游戏内光影的真实表现。
    """
    n = normal[..., :3] * 2.0 - 1.0
    # 贴图里的法线是 DirectX 约定（绿通道朝下），预览按 Y-up 打光，先翻回来
    n[..., 1] *= -1.0
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)

    diff = np.clip((n * _L).sum(axis=2), 0.0, 1.0)
    ndoth = np.clip((n * _H).sum(axis=2), 0.0, 1.0)

    smooth = spec[..., 0]
    metal = spec[..., 1]
    m = metal[..., None]

    # 半球天光：面朝上的部分更亮
    skyw = 0.5 + 0.5 * n[..., 1:2]
    sky = _SKY * (0.55 + 0.45 * skyw)

    # 金属没有漫反射，只反射环境；绝缘体走 albedo 漫反射
    base = albedo[..., :3] * (1.0 - m)
    # F0：绝缘体 0.04 的固定反射率，金属用自身颜色（所以金子反射是暖色）
    f0 = 0.04 * (1.0 - m) + albedo[..., :3] * m
    env = sky * f0

    fres = 0.06 + 0.94 * (1.0 - np.clip(n[..., 2:3], 0, 1)) ** 5

    specv = ndoth ** (4.0 + 220.0 * smooth ** 2)
    strength = 0.06 + 0.50 * smooth + 0.70 * metal
    tint = albedo[..., :3] * (1.0 - m) + m

    out = base * (0.24 + 0.86 * diff[..., None]) \
        + env * (0.20 + 0.50 * smooth[..., None] + 0.60 * m) * (0.62 + 0.90 * fres) \
        + specv[..., None] * strength[..., None] * tint * (0.45 + 0.55 * fres)
    return np.clip(out, 0.0, 1.0)
