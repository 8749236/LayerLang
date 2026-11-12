---
name: layerlang-mvp
description: Generate, edit, render, and inspect deterministic SVG graphics with the Layerlang v0.2 relative-layer DSL, including rectangles, single-line rasterized text, local PNG/JPEG images, and encapsulated local refs. Use when the user mentions 图层语言、Layerlang、pw/ph primitives、比例色块、AI 绘图 DSL，or asks to create or locally modify supported wireframes, diagrams, posters, reusable components, abstract vector compositions, and layer-based graphics without relying on native image generation. Do not use it for expressions, clipping, multiline rich text, URL images, parameterized refs, cross-layer property access, or photorealistic artwork; those are outside v0.2.
---

# Layerlang v0.2

使用随附渲染器把 Layerlang 源文本转换为 SVG 和图层 JSON。把语言规范视为语义权威，把本文件只视为使用流程。

## 准备

1. 首次使用或语法不确定时，阅读 [references/layerlang_v0_2.md](references/layerlang_v0_2.md)。
2. 使用 [scripts/layerlang_mvp.py](scripts/layerlang_mvp.py)，不要为每次任务重新实现解析器。
3. 确保 Python 环境具有 Pillow；文字和图片会统一转换为内嵌 PNG。
4. 只使用当前规范，不要把“已废弃方案”或“未来脑洞”当成可用语法。

## 创建图形

1. 确定全局画布尺寸；用户未指定时使用 `1200×1200`。
2. 先铺大色块，再添加文字、图片、引用和局部色块。只有需要局部坐标参照时才创建父容器。
3. 普通色块使用 `fill`；文字前景使用 `color`。绘制顺序始终是 `fill → 当前内容或 ref 展开内容 → 自有子节点`。
4. 所有尺寸显式携带单位。使用 `pw/ph` 访问父尺寸，使用 `w/h` 仅在 `xo/yo` 中访问当前尺寸。
5. 对 text/image 使用 `a` 读取固有尺寸；省略 `w/h` 等价于 `a100`。需要等比缩放时显式给两个相同比例。
6. 渲染并检查 SVG、越界报告和必要的坐标命中结果。

示例：

```text
[
    w=pw100; h=ph100; fill=#ffffff
    |
    text=居中测试; size=64; color=#202020;
    x=pw1/2; y=ph1/2;
    xo=w-1/2; yo=h-1/2
    |
    image=local.png;
    x=pw1/4; y=ph1/4;
    w=a50; h=a50;
    fill=#f2c94c
]
```

## 使用文字

- 只写单行文本，并显式提供正整数像素 `size`。
- 默认字体会依次查找 Linux 的 `NotoSansCJK-Regular.ttc`、Windows 的 `NotoSansSC-Regular.ttf` 和 `NotoSansSC-VF.ttf`；实际路径写入 `font_path`。候选字体度量可能不同，需要跨环境复现时用 `font` 显式提供确定的字体文件路径。
- 理解两阶段模型：先按 `size` 生成位图，再由 `w/h` 缩放。
- 当前字面值不能包含空白或 `;、|、]`；不要自行发明引号或 escape。

## 使用图片

- 只使用本地 PNG/JPEG；相对路径相对当前定义文件目录解析，嵌套 ref 内也不例外。
- 不使用 URL、data URI 或 SVG 图片。
- 使用 `a` 获取图片固有尺寸，使用 `w/h` 控制最终缩放。
- 需要透明背景时省略 `fill`；需要底色时保留它。

## 使用 ref 组件

`ref=card` 读取当前定义文件同目录的 `card.txt`。被引用文件是完整 Layerlang 文档，可以包含多个顶层 group、text/image 或下一层 ref。

```text
[ref=card;x=pw1/4;y=ph1/4;w=pw1/2;h=ph1/2;fill=#f2c94c]
```

- ref 必须显式提供 `w/h`，只允许 `pw/ph`；没有 `a` 固有尺寸。
- 实例最终矩形是被引用文档中所有顶层节点的 parent。每个实例独立求值，不共享几何状态。
- 类型头必须位于首位。调用方不能读取或修改组件内部属性，也不能在调用点传参数。
- ref 自身可以有 `fill` 和 `|` 子节点；顺序为 `fill → 组件展开内容 → 自有子节点`。
- 定义名不能包含路径分隔符，不能是 `.` 或 `..`，也不能带 `.txt` 后缀；不要发明远程引用。
- 图层路径使用 `@定义名` 标记展开边界，例如 `0@card/0/0`。修改组件后用这些路径检查实例几何。
- 直接和间接循环、超过 32 层引用或超过 10000 个展开节点都会报错。

## 修改已有图形

1. 阅读源文本和图层报告，按结构路径定位目标节点。
2. 修改组件定义时，检查所有实例；修改调用点时，只改变该实例矩形、背景或自有子节点。
3. 只修改目标属性或目标 primitive，保留无关区域的数值写法和绘制顺序。
4. 不重排顶层 group、兄弟节点或 `|` 分段；顺序就是覆盖语义。
5. 重新渲染，检查目标矩形、相邻节点和所有相关展开路径。

## 排版

使用多行文本让修改像局部代码编辑。将类型头放在第一位，将 `|` 单独放在一行。推荐随后排列类型配置和 `x、y、w、h、xo、yo、fill、color`。格式化时不重排 primitive，不无意义地改写等价数值形式。

## 渲染与检查

从技能目录运行：

```bash
python3 scripts/layerlang_mvp.py render 输入.txt \
  --width 1200 --height 1200 \
  --svg 输出.svg \
  --layers 图层.json
```

查询某个画布像素坐标上的 layer 堆叠：

```bash
python3 scripts/layerlang_mvp.py render 输入.txt \
  --svg 输出.svg --layers 图层.json \
  --hit 600 600
```

阅读 `outside.partial` 和 `outside.full` 定位越界对象。越界不是错误，不要自动 clamp，也不要添加裁剪。命中结果包含容器和 ref 实例，按从底到顶排列；几何命中不等于该点一定有不透明像素。

修改渲染器后运行：

```bash
python3 scripts/layerlang_mvp.py self-test
```

确保 39 项自测全部通过。

## 排错

- 根据错误中的行列号修复源文本，不要修改规范来迎合错误输入。
- 遇到裸数字时补上明确单位；牢记 `pw1` 是 1%，`pw1/1` 才是 100%。
- 普通色块不要使用旧版 `color`，改用 `fill`。
- 普通 primitive 和 ref 没有固有尺寸，不要使用 `a`；ref 必须显式写 `w/h`。
- ref 报错时沿 `@定义名` 路径和引用链定位，不要通过暴露内部属性绕开封装。
- 用户要求表达式、裁剪、多行富文本、URL 图片、参数化 ref 或跨层属性访问时，说明它们尚未进入 v0.2，不要发明临时语法。

## 视觉反馈

有图像查看能力时，同时检查渲染图与几何报告；没有视觉输入时，仍可根据精确矩形、绘制顺序、越界和命中结果完成确定性任务。不要把“画面看起来接近”当作几何正确性的替代证据。
