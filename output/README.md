# ProgramArt PBR —— 基于 Programmer Art 的 labPBR 材质包

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
