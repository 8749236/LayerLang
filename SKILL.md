---
name: layerlang-mvp
description: Generate, edit, render, and inspect deterministic SVG graphics with the Layerlang v0.11 relative-layer DSL, including rectangles, quoted single-line text, local PNG/JPEG images, encapsulated local refs, composable axis anchoring, child-frame lining, defaultable ref declarations, local or explicitly shared style classes, and resource/code/parent_rect inspection reports and filtered CLI inspection. Use when the user mentions 图层语言、Layerlang、100pw/50ph primitives、比例色块、AI 绘图 DSL，or asks to create or locally modify supported wireframes, diagrams, posters, reusable components, abstract vector compositions, and layer-based graphics without relying on native image generation. Do not use it for expressions, clipping, multiline rich text, URL images, cross-layer property access, user-defined sugar, or photorealistic artwork; those are outside v0.11.
---

# Layerlang v0.11

使用随附渲染器把 Layerlang 源文本转换为 SVG 和图层 JSON。规范链接只是当前行为索引，不是开发门禁；探索时以可运行行为和真实反馈为准。Layerlang 用于快速原型验证：agent 检查几何与组合关系，人通过渲染图反馈意图。v0.11 加入 `use=theme.txt` 共享声明文件，常量、语义色和样式属性包可以被多个主文档及 ref 显式共享；嵌套转发与 inspect 行为保持不变。

## 准备

1. 首次使用或语法不确定时，阅读 [references/layerlang_v0_11.md](references/layerlang_v0_11.md)。
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

## 使用常量与绝对像素

需要直接表达固定像素时写 `px`，不要再把每个像素手工换算成画布分数：

```text
@margin=24px
@halfMargin=1/2@margin
@controlSize=30px

[
    x=@margin; y=@halfMargin;
    w=@controlSize; h=@controlSize;
    fill=#ffffff
]
```

- 声明放在文档开头，每行一条；名称以 ASCII 字母开头，后续可含数字、下划线或点，大小写敏感。
- 使用 `@name` 引用完整值，使用 `2@name` 或 `1/2@name` 取尺寸倍数；只能引用当前文件中**先前声明**的默认值。
- `px` 是当前画布的绝对坐标单位：`24px` 就是 24 个画布单位，不随父容器比例缩放。其他整数单位仍按百分比解释，例如 `24pw` 是父宽度的 24%。
- 声明可以保存文字、颜色或 `pw/ph/w/h/a/px` 尺寸，使用时按目标属性检查类型与单位权限；普通 primitive 和 ref 的 `w/h` 必须大于零。
- `@name` 不赋值表示必填项；`@name=value` 表示带默认值的可选项。根文档中的必填声明必须提供，ref 调用点则用 `set.name=value` 覆盖。
- ref 定义只读自己文件里的声明，不自动读取调用方环境；调用方可以用 `set.name=@name` 把当前值显式转发给嵌套 ref。没有隐式全局主题或通用算术。
- 相对关系仍优先用 `pw/ph/w/h`；明确需要固定尺寸、像素对齐或 UI 间距时使用 `px`，不要自动混写猜测意图。

## 使用共享声明文件

多个文件需要同一组语义色、默认值或样式时，把声明放进只含声明的 `.txt`，再显式引用完整文件名：

```text
# theme.txt
@accent=#58a6ff
@text.primary=#c9d1d9
.accent_text = color=@accent; size=8
```

```text
use=theme.txt

[
  text="BRANCH"; class=accent_text
]
```

- 一个文档最多一个 `use`；只接受当前目录的完整本地 `.txt` 文件名，不支持 URL、路径跳转或嵌套 use。
- 共享文件只能包含 `@` 和 `.class` 声明，不能包含 primitive；其中声明必须带默认值，不能放必填项。
- 本地与共享声明重名就报错，不设计覆盖优先级。本地尺寸声明可以引用共享默认值，例如 `@double=2@margin`。
- 修改共享文件会改变所有使用方的默认值；`set.` 仍只覆盖对应 ref 实例。嵌套组件继续用 `set.accent=@accent` 显式转发。
- 共享样式的 `class.code` 指向共享文件中的真实定义范围，不是使用方文件的同偏移位置。

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

需要给 anchor 留白时，在父节点上使用 lining，不要在 anchor 占用的轴上再写 `x/y/xo/yo`：

```text
@pad=24px

[
    w=263/270pw; h=1/12ph; anchor=top-center; lining=@pad
    |
    text="状态"; anchor=right
]
```

这里文字对齐的是 lining 修正后的父矩形右边，不是容器外框或画布右边。只需单边修正时写 `ll/lr/lt/lb`，例如 `lr=24px` 只缩右边。

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

## 使用 lining 调整子级 parent

lining 只改变子节点和 ref 展开内容收到的 parent，不改变当前节点的 `rect`、fill、text/image 内容、命中矩形或兄弟节点：

```text
@pad=24px

[
    w=480px; h=240px; lining=@pad; fill=#93c5fd
    |
    w=100pw; h=100ph; fill=#ffffff
]
```

上例外框仍是 `480×240`，子节点收到的是 `432×192` 的内部矩形。底层属性为 `ll/lr/lt/lb`，分别对应左、右、上、下；`ll/lr` 接受 `pw/w/px`，`lt/lb` 接受 `ph/h/px`，也支持当前文件常量。

固定简写按逗号分隔：

```text
lining=24px                  四边相同
lining=24px,48px             垂直 24px，水平 48px
lining=1px,2px,3px,4px       上、右、下、左
```

`lining` 展开哪些 key，就占用哪些 key；同名手写属性会冲突。例如 `lining=24px;ll=1px` 报错。正值内缩，负值外扩；不 clamp，也不推开兄弟节点。修正后的 `parent_rect.w/h` 必须严格大于零。

报告中的 `parent_rect` 是当前节点求值时实际收到的父矩形，使用精确分数字符串。顶层节点是画布，ref 实例使用调用方 parent，ref 展开节点和 `|` 子节点使用 lining 修正后的矩形。

## 使用 ref 组件

`ref=card` 读取当前定义文件同目录的 `card.txt`。被引用文件是完整 Layerlang 文档，可以包含多个顶层 group、text/image 或下一层 ref。

- ref 必须显式提供 `w/h`，允许 `pw/ph/px`；没有 `a` 固有尺寸。
- 实例最终矩形先应用自身 lining，再作为被引用文档中所有顶层节点的 parent；实例外框和自身内容不内缩。每个实例独立求值，不共享几何状态。
- 调用方不能任意读取或覆盖组件内部属性，只能通过 `set.name` 覆盖文件头中已声明的公共值，见下一节。
- ref 自身可以有 `fill` 和 `|` 子节点；顺序为 `fill → 组件展开内容 → 自有子节点`。
- 定义名不能包含路径分隔符或 `@`，不能是 `.` 或 `..`，也不能带 `.txt` 后缀。
- 图层路径使用 `@定义名` 标记展开边界，例如 `0@card/0/0`。
- 直接和间接循环、超过 32 层引用或超过 10000 个求值节点都会报错。

## 使用声明与 set. 覆盖

组件文件头用 `@name` 声明公共值。无默认值时必填，有默认值时可选；`set.` 是调用点的固定前缀，不是任意属性访问：

```text
# event_row.txt
@kind
@detail
@kind_color=#d2a8ff
@row_fill=#132238

[
    w=100pw; h=100ph; fill=@row_fill
    |
    text=@kind; size=16; x=24px; y=12px; color=@kind_color
    |
    text=@detail; size=18; x=240px; y=12px; color=#c9d1d9
]
```

调用点只覆盖需要的值：

```text
[
    ref=event_row;
    set.kind="BRANCH"; set.detail="true · 17/42";
    w=600px; h=64px
]
```

- `set.kind`、`set.detail` 必须提供；`kind_color`、`row_fill` 省略时使用组件默认值。未知 `set.`、缺少必填或类型冲突都明确报错。
- 声明值可以是文字、颜色或尺寸。`@accent=#58a6ff` 可用于 `color/fill`，`@pad=24px` 可用于几何属性，`@label="hello"` 可用于 `text`。
- 声明名允许 ASCII 字母、数字、下划线和点，例如 `@text.primary`。点只是名称的一部分，不表示对象访问。
- 嵌套 ref 用 `set.accent=@accent` 显式转发当前文件中的值。转发在调用方环境解析，再传给下一层，不自动形成全局主题。
- `size/font/image/ref/anchor` 和 `lining=` 简写不接受声明引用。条件绘制、slots、repeat、通用表达式和多 class 层叠仍不属于当前版本。
- ref 实例的 `content_metadata.parameters` 保存本次 `set.` 覆盖及类型；默认值不重复记录。内部节点的 `code` 仍指向组件定义，实例 `code` 指向调用点。

## 使用样式属性包

文件头用 `.name` 声明样式，primitive 用 `class=name` 应用。样式只负责属性复用，不是选择器、节点宏或层叠系统：

```text
@accent=#58a6ff
.muted = color=#6e7681; font=consola.ttf; size=8
.accent_text = color=@accent; font=consola.ttf; size=8

[
    text="BRANCH"; class=accent_text
    |
    text="09"; class=muted
]
```

- 只允许 `color/fill/font/size` 四类样式属性。`x/y/w/h/anchor/lining/ref/text/image` 不允许进入样式，避免变成布局或节点宏。
- 一个 primitive 最多一个 class；样式不能引用其他样式，也没有继承、条件、repeat 或多 class 组合。
- 展开 key 与手写 key 冲突就报错，例如 `class=muted;color=#ffffff`，不做 CSS 式覆盖。
- 样式默认按文件隔离；需要共享时显式 `use=theme.txt`。没有 `set.class` 或任意跨文件覆盖。样式内可以引用 `@accent`，实例的 `set.accent` 会改变展开结果。
- 节点的 `code` 仍指向应用点；`content_metadata.class` 保存样式名和 `class.code`，用于定位文件头中的样式定义。
- `$` 继续保持 reserved，不参与当前样式语法。

## 修改与溯源

1. 阅读源文本和图层报告，按结构路径定位实例，按 `code` 找到应编辑的源码范围。
2. `resource` 是主内容资源：image 为图片路径，ref 为组件定义路径，rect/text 为 `null`；字体等辅助资源留在 `content_metadata`。
3. `code.file` 是源码 `.txt` 文件，`code.start/end` 是半开字符范围，不保存代码正文。ref 实例指向调用点，内部节点指向各自定义；重复实例路径不同，但内部节点通常共享同一个 `code`。`parent_rect` 则记录几何求值输入，lining 排查时同时阅读它与 `rect_px/rect_exact`。
4. offset 从 0 开始，行列从 1 开始；offset 对应原始文本字符，不是 UTF-8 字节。CLI 保留 CRLF，可用 `raw_source[code.start.offset:code.end.offset]` 精确取出 primitive 属性文本。
5. 纯内存构建且未提供路径时，`code.file=null`，offset 和行列仍保留。只修改目标属性、primitive 或组件定义，不重排顶层 group、兄弟节点或 `|` 分段。
6. 重新渲染，检查目标矩形、相邻节点和所有相关展开路径。

## 排版

使用多行文本让修改像局部代码编辑。将类型头放在第一位，将 `|` 单独放在一行。默认缩进两格；短 primitive 可合并成一行，长参数调用仍按属性拆行。推荐随后排列类型配置、anchor 或 `x、y、w、h、xo、yo`、`fill、color`。格式化时不重排 primitive，不无意义地改写等价数值形式。

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

使用 inspect 先取摘要，再按需要过滤，不要默认阅读整份大型 JSON：

```bash
# 只看节点、类型、参数实例和越界摘要
python3 scripts/layerlang_mvp.py inspect 输入.txt --width 1200 --height 1200

# 只看越界节点
python3 scripts/layerlang_mvp.py inspect 输入.txt --outside

# 按文字、类型或子树过滤
python3 scripts/layerlang_mvp.py inspect 输入.txt --text "BRANCH"
python3 scripts/layerlang_mvp.py inspect 输入.txt --kind ref
python3 scripts/layerlang_mvp.py inspect 输入.txt --path "0@card"
python3 scripts/layerlang_mvp.py inspect 输入.txt --at 600 600
```

无过滤时 `layers` 为空，只输出摘要；有过滤时返回匹配图层的完整报告。`--path` 包含指定实例及其展开子树，`--outside` 合并 partial/full，`--at x y` 按从底到顶返回坐标命中的图层。生成器产物的 `code` 仍指向生成的 `.txt`，不自动指向生成器源码。

CLI 的 stdout 和 stderr 固定使用 UTF-8，包括 Windows 管道输出；subprocess 调用方应按 UTF-8 解码，不要使用系统区域编码。

修改渲染器后运行：

```bash
python3 scripts/layerlang_mvp.py self-test
```

确保 98 项自测全部通过。

## 绝对像素与复杂局部

固定间距、像素级对齐和明确像素坐标使用 `px` 或文件内常量；复杂局部绘制、像素级素材仍可用 Python 等外部工具生成确定性 PNG，再通过 image 或 ref 叠回。相对比例与绝对像素是不同的显式意图，不要互相换算后猜意图。Layerlang 负责快速原型布局与验证，外部图片内部无需几何化。

## 什么时候不要这样用

- 需要组件随实例缩放、保持父子关系时，优先 `pw/ph/w/h`；不要为了省去比例思考，把可复用组件内部全部改成 `px`。固定画布、像素稿和明确间距则可以直接使用 `px`。
- 需要整体移动的独立模块才拆 ref；不要给每个小 primitive 都创建一个一次性文件。结构相同但内容变化时使用参数，固定内容复用继续使用静态 ref。
- 不要为假想未来需求叠加默认值、条件、表达式或自定义宏。当前目标仍是可检查的快速原型，而不是通用 UI 框架。
- 如果修改持续依赖生成器，先检查是否缺少组件边界、参数或 inspect 使用，不要把生成器当成默认 source of truth。

## 排错

- 根据错误中的文件、行列和节点路径修复输入，不要修改规范来迎合错误输入。
- 遇到裸数字时补上明确后缀单位；牢记 `1pw` 是 1%，`1/1pw` 才是 100%。旧版 `pw1`、`ph1/12`、`w-1/2`、`h-1/2`、`a100` 不再接受，应分别改为 `1pw`、`1/12ph`、`-1/2w`、`-1/2h`、`100a`。
- 普通色块不要使用旧版 `color`，改用 `fill`。
- 普通 primitive 和 ref 没有固有尺寸，不要使用 `a`；ref 必须显式写 `w/h`。
- 引号只在值首生效；不要发明单引号、`\n` 或其他转义。
- ref 报错时沿 `@定义名` 路径和引用链定位，不要通过暴露内部属性绕开封装。
- 用户要求表达式、裁剪、多行富文本、URL 图片、跨层属性访问、自定义 sugar 或 anchor 后间距时，说明它们尚未进入 v0.11，不要发明临时语法。

## 视觉反馈

有图像查看能力时，同时检查渲染图与几何报告；没有视觉输入时，仍可根据精确矩形、绘制顺序、越界、源范围和命中结果完成确定性任务。不要把“画面看起来接近”当作几何正确性的替代证据，也不要把几何正确当作已经满足人的意图。
