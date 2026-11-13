---
name: layerlang-mvp
description: Generate, edit, render, and inspect deterministic SVG graphics with the Layerlang v0.5 relative-layer DSL, including rectangles, quoted single-line text, local PNG/JPEG images, encapsulated local refs, composable axis anchoring, and resource/code inspection reports. Use when the user mentions 图层语言、Layerlang、100pw/50ph primitives、比例色块、AI 绘图 DSL，or asks to create or locally modify supported wireframes, diagrams, posters, reusable components, abstract vector compositions, and layer-based graphics without relying on native image generation. Do not use it for expressions, clipping, multiline rich text, URL images, parameterized refs, cross-layer property access, absolute-pixel geometry, user-defined sugar, or photorealistic artwork; those are outside v0.5.
---

# Layerlang v0.5

使用随附渲染器把 Layerlang 源文本转换为 SVG 和图层 JSON。把语言规范视为语义权威，把本文件只视为使用流程。Layerlang 用于快速原型验证：agent 检查几何与组合关系，人通过渲染图反馈意图。v0.5 只把尺寸文法迁移为“数字＋单位”的后缀写法；旧前缀直接报错。anchor、坐标求值和 `resource/code` 报告沿用 v0.4 语义。

## 准备

1. 首次使用或语法不确定时，阅读 [references/layerlang_v0_5.md](references/layerlang_v0_5.md)。
2. 使用 [scripts/layerlang_mvp.py](scripts/layerlang_mvp.py)，不要为每次任务重新实现解析器。
3. 确保 Python 环境具有 Pillow；文字和图片会统一转换为内嵌 PNG。
4. 只使用当前规范，不要把“已废弃方案”或“未来脑洞”当成可用语法。

## 创建图形

1. 确定全局画布尺寸；用户未指定时使用 `1200×1200`。
2. 先铺大色块，再添加文字、图片、引用和局部色块。只有需要局部坐标参照时才创建父容器。
3. 普通色块使用 `fill`；文字前景使用 `color`。绘制顺序始终是 `fill → 当前内容或 ref 展开内容 → 自有子节点`。
4. 所有尺寸使用“数字＋单位”，数值与单位之间不留空白，例如 `100pw、1/12ph、-1/2w、50a`。使用 `pw/ph` 访问父尺寸，使用 `w/h` 仅在 `xo/yo` 中访问当前尺寸；旧版 `pw100` 等前缀写法直接报错。
5. 对 text/image 使用 `a` 读取固有尺寸；省略 `w/h` 等价于 `100a`。需要等比缩放时显式给两个相同比例。
6. 渲染并检查 SVG、越界报告、源范围和必要的坐标命中结果。

示例：

```text
[
    w=100pw; h=100ph; fill=#ffffff
    |
    text="Hello world; [draft]";
    anchor=center; color=#202020
    |
    image="local image.png";
    x=1/4pw; y=1/4ph;
    w=50a; h=50a;
    fill=#f2c94c
]
```

## 使用文字与引号

- 只写单行文本。`size` 可省略，默认 `40`；显式提供时必须是正整数像素。
- 值以 `"` 开头时进入字符串模式，内部可写空格、制表符、`;、|、[、]`。
- 字符串只支持 `\"` 和 `\\`；未闭合、其他转义、原始换行和闭合后的非法后缀都会报错。
- 不使用空白代替 `;` 分隔属性。未加引号的旧写法保持不变。
- 先按 `size` 生成位图，再由 `w/h` 缩放。报告中的 `scale:[sx,sy]` 可直接区分原尺寸、等比和非等比缩放。
- 一行多色文字需要拆成多个 text 节点；节点之间不能读取前一段宽度，因此精确连续排布需要外部计算、近似比例或 PNG 素材。相同 font、size 和 y 的拆分文字会共享基线；跨字体或字号时，y 仍只定位位图顶端，不提供基线单位。

## 字体与图片

- 显式字体和图片的相对路径都相对当前定义文件目录解析，嵌套 ref 内也不例外。
- 默认字体依次查找 Linux 的 `NotoSansCJK-Regular.ttc`、Windows 的 `NotoSansSC-Regular.ttf` 和 `NotoSansSC-VF.ttf`；实际路径写入 `font_path`。
- 候选字体度量可能不同，需要跨环境复现时用 `font` 显式提供确定的字体文件。
- 图片只使用本地 PNG/JPEG，不使用 URL、data URI 或 SVG 图片。Windows 的 `C:/...` 和 `C:\...` 是本地绝对路径，不会被当成 URL；引号内使用反斜杠时仍需写成 `\\`。
- 需要透明背景时省略 `fill`；需要底色时保留它。

## 常用对齐与比例

右对齐和底对齐使用单轴 anchor：

```text
右边：anchor=right
底边：anchor=bottom
```

它们分别等价于 `x=100pw;xo=-100w` 与 `y=100ph;yo=-100h`，只占用对应轴，另一轴仍可手写。对齐相对**直接父矩形**，目标是 layer 矩形边缘；文字位图的字体度量不保证视觉墨迹也贴边。

需要给 anchor 留白时，先显式缩小或移动父容器，不要在 anchor 占用的轴上再写 `x/y/xo/yo`：

```text
[
    w=263/270pw; h=1/12ph; anchor=top-center
    |
    text="状态"; anchor=right
]
```

这里文字对齐的是内层容器的右边，不是画布右边。容器比父矩形少 `7/270pw`，居中后由左右两侧均分；如果只需要一侧留白，就用 `x/w` 明确容器的位置和宽度。具体像素边距仍需先换算为相对比例，anchor 本身不支持后间距。

整数 `N` 表示 `N/100`，分数 `a/b` 直接表示 `a/b`，不要混淆：

```text
5pw    = 5/100 = 5%
5/2pw  = 5/2   = 250%
1/40pw = 1/40  = 2.5%
```

元素异常飞出时，先检查 JSON 的 `outside.partial`、`outside.full` 和 `rect_px/rect_exact`，再修改输入；不要用百分比反复猜像素位置。

## 使用 anchor

anchor 只是纯 desugar，不是自动布局。双轴值按 `vertical-horizontal` 书写：

```text
vertical   = null | top  | center | bottom
horizontal = null | left | center | right
```

非 null 值的展开规则：

```text
left   → x=0pw;xo=0w
center → x=1/2pw;xo=-1/2w       （水平）
right  → x=100pw;xo=-100w

top    → y=0ph;yo=0h
center → y=1/2ph;yo=-1/2h       （垂直）
bottom → y=100ph;yo=-100h
```

`null` 表示不管这根轴，不生成属性，也不占用它；`top/left` 虽然展开为零，仍占用对应轴。`null-null` 是什么都不做的完整形式，日常通常直接省略 anchor。

短形式：

```text
left=null-left；right=null-right；top=top-null；bottom=bottom-null
center=center-center
```

推荐写法：双轴定位用 `top-right`，单边靠齐用 `right/bottom`，单边居中用 `null-center/center-null`，双轴居中用 `center`。

冲突由展开 key 决定。被 anchor 占用的属性不能再手写，另一根 null 轴可以：

```text
anchor=right;y=1/2ph;yo=-1/2h    合法，等价于 center-right
anchor=right;x=1/2pw             错误
anchor=top;y=1/2ph               错误
anchor=center;xo=10w             错误，不表示居中后偏移
anchor=null-null;x=1/2pw         合法，anchor 是空操作
```

属性顺序不影响冲突或求值。anchor 不支持后间距，也不是用户自定义 sugar 或多个 sugar 组合机制。

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

1. 阅读源文本和图层报告，按结构路径定位实例，按 `code` 找到应编辑的源码范围。
2. `resource` 是主内容资源：image 为图片路径，ref 为组件定义路径，rect/text 为 `null`；字体等辅助资源留在 `content_metadata`。
3. `code.file` 是源码 `.txt` 文件，`code.start/end` 是半开字符范围，不保存代码正文。ref 实例指向调用点，内部节点指向各自定义；重复实例路径不同，但内部节点通常共享同一个 `code`。
4. offset 从 0 开始，行列从 1 开始；offset 对应原始文本字符，不是 UTF-8 字节。CLI 保留 CRLF，可用 `raw_source[code.start.offset:code.end.offset]` 精确取出 primitive 属性文本。
5. 纯内存构建且未提供路径时，`code.file=null`，offset 和行列仍保留。只修改目标属性、primitive 或组件定义，不重排顶层 group、兄弟节点或 `|` 分段。
6. 重新渲染，检查目标矩形、相邻节点和所有相关展开路径。

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

CLI 的 stdout 和 stderr 固定使用 UTF-8，包括 Windows 管道输出；subprocess 调用方应按 UTF-8 解码，不要使用系统区域编码。

修改渲染器后运行：

```bash
python3 scripts/layerlang_mvp.py self-test
```

确保 59 项自测全部通过。

## 绝对像素与复杂局部

需要绝对间距、像素级对齐或复杂局部绘制时，用 Python 等外部工具生成确定性 PNG，再通过 image 或 ref 叠回。不要用 `xo=-48w` 凑 48px；它表示当前宽度的 48%。Layerlang 负责快速原型布局与验证，外部图片内部无需几何化。

## 排错

- 根据错误中的文件、行列和节点路径修复输入，不要修改规范来迎合错误输入。
- 遇到裸数字时补上明确后缀单位；牢记 `1pw` 是 1%，`1/1pw` 才是 100%。旧版 `pw1`、`ph1/12`、`w-1/2`、`h-1/2`、`a100` 不再接受，应分别改为 `1pw`、`1/12ph`、`-1/2w`、`-1/2h`、`100a`。
- 普通色块不要使用旧版 `color`，改用 `fill`。
- 普通 primitive 和 ref 没有固有尺寸，不要使用 `a`；ref 必须显式写 `w/h`。
- 引号只在值首生效；不要发明单引号、`\n` 或其他转义。
- ref 报错时沿 `@定义名` 路径和引用链定位，不要通过暴露内部属性绕开封装。
- 用户要求表达式、裁剪、多行富文本、URL 图片、参数化 ref、跨层属性访问、绝对像素几何、自定义 sugar 或 anchor 后间距时，说明它们尚未进入 v0.5，不要发明临时语法。

## 视觉反馈

有图像查看能力时，同时检查渲染图与几何报告；没有视觉输入时，仍可根据精确矩形、绘制顺序、越界、源范围和命中结果完成确定性任务。不要把“画面看起来接近”当作几何正确性的替代证据，也不要把几何正确当作已经满足人的意图。
