---
name: layerlang-mvp
description: Generate, edit, render, and inspect deterministic SVG graphics with the Layerlang v0.3 relative-layer DSL, including rectangles, quoted single-line text, local PNG/JPEG images, encapsulated local refs, center anchoring, and source-mapped inspection reports. Use when the user mentions 图层语言、Layerlang、pw/ph primitives、比例色块、AI 绘图 DSL，or asks to create or locally modify supported wireframes, diagrams, posters, reusable components, abstract vector compositions, and layer-based graphics without relying on native image generation. Do not use it for expressions, clipping, multiline rich text, URL images, parameterized refs, cross-layer property access, absolute-pixel geometry, or photorealistic artwork; those are outside v0.3.
---

# Layerlang v0.3

使用随附渲染器把 Layerlang 源文本转换为 SVG 和图层 JSON。把语言规范视为语义权威，把本文件只视为使用流程。Layerlang 用于快速原型验证：agent 检查几何与组合关系，人通过渲染图反馈意图。

## 准备

1. 首次使用或语法不确定时，阅读 [references/layerlang_v0_3.md](references/layerlang_v0_3.md)。
2. 使用 [scripts/layerlang_mvp.py](scripts/layerlang_mvp.py)，不要为每次任务重新实现解析器。
3. 确保 Python 环境具有 Pillow；文字和图片会统一转换为内嵌 PNG。
4. 只使用当前规范，不要把“已废弃方案”或“未来脑洞”当成可用语法。

## 创建图形

1. 确定全局画布尺寸；用户未指定时使用 `1200×1200`。
2. 先铺大色块，再添加文字、图片、引用和局部色块。只有需要局部坐标参照时才创建父容器。
3. 普通色块使用 `fill`；文字前景使用 `color`。绘制顺序始终是 `fill → 当前内容或 ref 展开内容 → 自有子节点`。
4. 所有尺寸显式携带单位。使用 `pw/ph` 访问父尺寸，使用 `w/h` 仅在 `xo/yo` 中访问当前尺寸。
5. 对 text/image 使用 `a` 读取固有尺寸；省略 `w/h` 等价于 `a100`。需要等比缩放时显式给两个相同比例。
6. 渲染并检查 SVG、越界报告、源范围和必要的坐标命中结果。

示例：

```text
[
    w=pw100; h=ph100; fill=#ffffff
    |
    text="Hello world; [draft]";
    anchor=center; color=#202020
    |
    image="local image.png";
    x=pw1/4; y=ph1/4;
    w=a50; h=a50;
    fill=#f2c94c
]
```

## 使用文字与引号

- 只写单行文本。`size` 可省略，默认 `40`；显式提供时必须是正整数像素。
- 值以 `"` 开头时进入字符串模式，内部可写空格、制表符、`;、|、[、]`。
- 字符串只支持 `\"` 和 `\\`；未闭合、其他转义、原始换行和闭合后的非法后缀都会报错。
- 不使用空白代替 `;` 分隔属性。未加引号的旧写法保持不变。
- 先按 `size` 生成位图，再由 `w/h` 缩放。报告中的 `scale:[sx,sy]` 可直接区分原尺寸、等比和非等比缩放。

## 字体与图片

- 显式字体和图片的相对路径都相对当前定义文件目录解析，嵌套 ref 内也不例外。
- 默认字体依次查找 Linux 的 `NotoSansCJK-Regular.ttc`、Windows 的 `NotoSansSC-Regular.ttf` 和 `NotoSansSC-VF.ttf`；实际路径写入 `font_path`。
- 候选字体度量可能不同，需要跨环境复现时用 `font` 显式提供确定的字体文件。
- 图片只使用本地 PNG/JPEG，不使用 URL、data URI 或 SVG 图片。
- 需要透明背景时省略 `fill`；需要底色时保留它。

## 使用 anchor

`anchor=center` 严格等价于：

```text
x=pw1/2;y=ph1/2;xo=w-1/2;yo=h-1/2
```

它适用于所有 primitive，但不能与 `x、y、xo、yo` 中任何一个同时出现。`anchor=center;xo=w10` 是错误输入，不表示居中后偏移。

## 使用 ref 组件

`ref=card` 读取当前定义文件同目录的 `card.txt`。被引用文件是完整 Layerlang 文档，可以包含多个顶层 group、text/image 或下一层 ref。

- ref 必须显式提供 `w/h`，只允许 `pw/ph`；没有 `a` 固有尺寸。
- 实例最终矩形是被引用文档中所有顶层节点的 parent。每个实例独立求值，不共享几何状态。
- 调用方不能读取或修改组件内部属性，也不能在调用点传参数。
- ref 自身可以有 `fill` 和 `|` 子节点；顺序为 `fill → 组件展开内容 → 自有子节点`。
- 定义名不能包含路径分隔符或 `@`，不能是 `.` 或 `..`，也不能带 `.txt` 后缀。
- 图层路径使用 `@定义名` 标记展开边界，例如 `0@card/0/0`。
- 直接和间接循环、超过 32 层引用或超过 10000 个求值节点都会报错。

## 修改与溯源

1. 阅读源文本和图层报告，按结构路径定位实例，按 `source_span` 找到应编辑的源文件范围。
2. 同一组件的多个实例路径不同，但内部节点通常共享同一个 `source_span`；修改定义会影响所有实例。
3. `source_span` 使用半开区间，offset 从 0 开始，行列从 1 开始；它是只读溯源信息，不参与求值。
4. 只修改目标属性、primitive 或组件定义，不重排顶层 group、兄弟节点或 `|` 分段。
5. 重新渲染，检查目标矩形、相邻节点和所有相关展开路径。

## 排版

使用多行文本让修改像局部代码编辑。将类型头放在第一位，将 `|` 单独放在一行。推荐随后排列类型配置、anchor 或 `x、y、w、h、xo、yo`、`fill、color`。格式化时不重排 primitive，不无意义地改写等价数值形式。

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

确保 51 项自测全部通过。

## 绝对像素与复杂局部

需要绝对间距、像素级对齐或复杂局部绘制时，用 Python 等外部工具生成确定性 PNG，再通过 image 或 ref 叠回。不要用 `xo=w-48` 凑 48px；它表示当前宽度的 48%。Layerlang 负责快速原型布局与验证，外部图片内部无需几何化。

## 排错

- 根据错误中的文件、行列和节点路径修复输入，不要修改规范来迎合错误输入。
- 遇到裸数字时补上明确单位；牢记 `pw1` 是 1%，`pw1/1` 才是 100%。
- 普通色块不要使用旧版 `color`，改用 `fill`。
- 普通 primitive 和 ref 没有固有尺寸，不要使用 `a`；ref 必须显式写 `w/h`。
- 引号只在值首生效；不要发明单引号、`\n` 或其他转义。
- ref 报错时沿 `@定义名` 路径和引用链定位，不要通过暴露内部属性绕开封装。
- 用户要求表达式、裁剪、多行富文本、URL 图片、参数化 ref、跨层属性访问或绝对像素几何时，说明它们尚未进入 v0.3，不要发明临时语法。

## 视觉反馈

有图像查看能力时，同时检查渲染图与几何报告；没有视觉输入时，仍可根据精确矩形、绘制顺序、越界、源范围和命中结果完成确定性任务。不要把“画面看起来接近”当作几何正确性的替代证据，也不要把几何正确当作已经满足人的意图。
