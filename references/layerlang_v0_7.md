# 图层语言（Layerlang）v0.7 行为说明

- 状态：按当前实现固化，供检索；不是开发流程门禁。
- 对应实现：`layerlang-mvp/scripts/layerlang_mvp.py`。
- 运行依赖：Python 与 Pillow。
- 验收基线：72 项自测全部通过。
- 相对 v0.6 的变化：加入 `ll/lr/lt/lb` 与 1／2／4 值 lining，只修正子级和 ref 展开的 parent；新增精确 `parent_rect` 报告。不包含内容参数、颜色 token 或通用表达式。

## 1. 当前模型

文档是一串按顺序执行的 primitive。每个 primitive 都有一个矩形，可以是普通色块、容器、文字、图片或本地引用。所有类型共享相同的位置、尺寸、offset、父子矩形和检查协议。

```text
[父 primitive | 子 primitive | 子 primitive]
```

父 primitive 先执行，子节点随后从左到右执行。所有子节点共享同一个父矩形。当前版本不允许多层嵌套括号；text/image/ref 同样可以作为父矩形。跨定义组合更深的树使用 ref。

语言优先保留可预测性和可检查性。不会因为对象离开父容器或全局画布而删除其几何信息，也不会自动保持图片或文字的宽高比。

Layerlang 主要服务于快速原型验证，而不是完整美术产出：agent 通过 display list 和 JSON 检查布局、实例与命中关系，人通过渲染图反馈意图，形成短闭环。使用 image 承载复杂或像素精确内容是设计允许的组合方式；语言要求可检查的是布局与组合关系，不要求外部图片内部也几何化。

## 2. 非目标

v0.7 明确不支持：

- 跨 layer 属性读取或写入、参数化 ref；
- 通用表达式、函数、条件、循环；
- 多层嵌套括号；
- 自动布局、选择器、继承、`z-index`；
- 裁剪、描边、渐变、旋转、透明度、圆角或圆形 primitive；
- 注释、小数、多行文本和富文本；
- 跨字体引擎一致性承诺；
- URL 图片、SVG 图片和远程 ref；
- 根据裸数字猜测默认单位；
- 除固定 anchor desugar 之外的布局推断；
- 用户自定义 sugar、多个 sugar 组合、anchor 后间距或通用宏系统。

## 3. 类型头与属性

### 3.1 类型头

`text`、`image` 或 `ref` 是类型头，必须是 primitive 的第一个属性。一个 primitive 最多有一个类型头。

```text
[w=100pw;h=100ph;fill=#ffffff]
[text="测试测试";size=40;color=#000000]
[text="hello world";anchor=center]
[image=test.png;w=100a;h=100a]
[ref=card;w=50pw;h=50ph]
```

没有类型头时是普通色块或容器。普通 primitive 和 ref 必须显式提供 `w、h`；text/image 可以省略，省略等价于 `100a`。

### 3.2 公共属性

| 属性 | 含义 |
|---|---|
| `x、y` | 相对父矩形的位置，默认零 |
| `w、h` | 当前 layer 的最终宽度与高度 |
| `xo、yo` | 求出尺寸与基础位置后的修正，默认零 |
| `anchor` | 可选固定布局糖；按轴 desugar 为位置属性 |
| `lining、ll、lr、lt、lb` | 可选子级 parent 修正；不改变当前节点外框 |
| `fill` | 当前 layer 的背景填充，省略时透明 |

所有类型按以下顺序绘制：

```text
fill 背景 → 当前内容或 ref 展开内容 → 自有子节点
```

普通矩形容器没有当前内容；ref 没有位图内容，其展开结果占据这一位置。

### 3.3 anchor 固定 desugar

anchor 是纯前端糖：只把字符串展开为后端已有的 `x/y/xo/yo`，不读取 parent、节点类型、兄弟节点或其他 layer，不做 I/O，也不参与坐标求值。后端求值器不需要知道 anchor 存在。

双轴值统一使用 `vertical-horizontal`：

```text
vertical   = null | top  | center | bottom
horizontal = null | left | center | right
anchor     = vertical "-" horizontal
```

这形成完整的 4×4 组合。非 null 轴的展开表为：

| 轴 | 值 | 展开属性 |
|---|---|---|
| 水平 | `left` | `x=0pw;xo=0w` |
| 水平 | `center` | `x=1/2pw;xo=-1/2w` |
| 水平 | `right` | `x=100pw;xo=-100w` |
| 垂直 | `top` | `y=0ph;yo=0h` |
| 垂直 | `center` | `y=1/2ph;yo=-1/2h` |
| 垂直 | `bottom` | `y=100ph;yo=-100h` |

`null` 表示“这颗糖不管这根轴”，展开为空属性集。它不是把坐标清零，也不占用该轴属性。`null-null` 因而等价于不写 anchor；没有手写位置属性时，未展开轴继续使用公共属性的默认零。

例如：

```text
null-center  = 只水平居中，允许手写 y/yo
center-null  = 只垂直居中，允许手写 x/xo
top-right    = 明确靠顶靠右，占用两轴
null-null    = 空操作，不占用任何位置属性
```

保留以下短形式别名：

```text
left   = null-left
right  = null-right
top    = top-null
bottom = bottom-null
center = center-center
```

因此 `right` 与 `top-right` 在没有手写垂直属性时几何相同，但冲突语义不同：`right` 允许继续手写 `y/yo`，`top-right` 不允许。

冲突直接由 desugar 的后端 key 决定。设 `expanded` 为 anchor 展开的完整属性字典，则 `expanded.keys()` 与输入显式属性名的交集必须为空，否则报错。即使 `left/top` 展开的是零值，其 `x/xo` 或 `y/yo` 仍必须进入冲突集合。未展开的轴可以手写，属性书写顺序不影响结果，也不存在后写覆盖先写。

```text
anchor=right;y=1/2ph;yo=-1/2h    合法，等价于 center-right
anchor=right;x=1/2pw             错误，水平 key 冲突
anchor=top;y=1/2ph               错误，垂直 key 冲突
anchor=center;xo=10w             错误，不表示居中后偏移
anchor=null-null;x=1/2pw         合法，anchor 是空操作
```

anchor 适用于所有 primitive 类型，offset 继续使用当前节点最终尺寸。重复 anchor、非法组合和上述 key 冲突均明确报错。当前不支持 anchor 后间距，也不把这套机制推广为用户自定义 sugar 或多个 sugar 组合。

为减少等价写法造成的输出漂移，推荐使用：双轴定位写 `top-right` 等形式，单边靠齐写 `right/bottom` 等短形式，单边居中写 `null-center/center-null`，双轴居中写 `center`；没有锚点时通常省略 anchor，`null-null` 主要供统一生成规则使用。

### 3.4 text 属性

| 属性 | 是否必需 | 含义 |
|---|---:|---|
| `text` | 是 | 单行文本内容，同时作为类型头 |
| `size` | 否 | 正整数像素字号，默认 `40` |
| `font` | 否 | 字体文件路径；`default` 或省略时使用默认字体解析链 |
| `color` | 否 | 文字前景色，默认 `#000000` |

显式提供的 `size` 仍必须是正整数像素；零、负数、分数和其他单位均报错。省略时相当于 `size=40`。

### 3.5 字体路径与默认解析链

显式 `font` 的相对路径相对**当前定义文件所在目录**解析，不再相对进程 CWD。嵌套 ref 中的字体因此相对该 ref 定义文件。绝对路径保持绝对路径。

`default` 按顺序查找以下本地文件，并使用第一个存在的文件：

1. Linux：`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`；
2. Windows：`%WINDIR%/Fonts/NotoSansSC-Regular.ttf`；
3. Windows：`%WINDIR%/Fonts/NotoSansSC-VF.ttf`。

实际选择的文件路径写入文字的 `font_path` 元数据。全部缺失时会报错并提示使用 `font` 显式指定。候选字体的度量可能不同，因此默认字体只保证当前环境可运行，不承诺跨平台逐像素一致；需要固定结果时应提供确定的字体文件。

### 3.6 image 属性

`image` 同时是类型头和资源路径。当前只接受本地 `.png、.jpg、.jpeg`。相对路径相对当前定义文件所在目录解析；在 ref 内使用时，相对该 ref 定义文件，而不是主文档。Windows 的 `C:/...` 和 `C:\...` 按本地绝对路径处理，驱动器号不被误判为 URI scheme；`C:relative.png` 仍不是受支持的绝对路径写法。URL、data URI、缺失文件和其他格式均报错。

图片加载后应用 EXIF 方向并转换为 RGBA PNG，保留透明通道。

### 3.7 ref 属性

`ref` 同时是类型头和本地定义名：

```text
[ref=card;x=1/4pw;y=1/4ph;w=1/2pw;h=1/2ph]
```

上例读取当前定义文件同目录的 `card.txt`。定义名不接受路径分隔符，不能是 `.` 或 `..`，不能包含为展开路径保留的 `@`，也不能带 `.txt` 后缀。实现只读取本地文件，不访问网络。

被引用文件是完整 Layerlang 文档，可以包含多个顶层 group，也可以继续使用 text/image/ref。ref 实例必须显式提供 `w/h`，只允许 `pw/ph`；没有固有尺寸，不能省略，也不能使用 `a`。

调用方只能确定实例矩形、公共背景和实例子节点。调用方不能读取或修改被引用对象的内部属性，也不能通过调用点传入参数。

### 3.8 lining 子级坐标修正

lining 只改变子节点和 ref 展开内容收到的 parent，不改变当前节点的 `rect`、fill、text/image 内容、命中矩形、绘制顺序或兄弟节点。

底层属性为 `ll/lr/lt/lb`，分别对应左、右、上、下。`ll/lr` 接受 `pw/w/px`，`lt/lb` 接受 `ph/h/px`，也支持当前文件常量。`pw/ph` 读取当前节点的 parent，`w/h` 读取当前节点已经求出的外框。

固定简写按逗号分隔：

```text
lining=24px                  四边相同
lining=24px,48px             垂直 24px，水平 48px
lining=1px,2px,3px,4px       上、右、下、左
```

简写是纯 desugar，展开为四边 key。展开 key 与输入显式属性的交集必须为空，例如 `lining=24px;ll=1px` 冲突。零值同样占 key，属性顺序不影响结果。

设当前外框为 `rect=(x,y,w,h)`，子级实际收到的矩形为：

```text
parent_rect.x = x + ll
parent_rect.y = y + lt
parent_rect.w = w - ll - lr
parent_rect.h = h - lt - lb
```

正值内缩，负值外扩，不 clamp，也不推开兄弟节点。修正后的 `parent_rect.w/h` 必须严格大于零，否则在携带 lining 的节点处明确报错。ref 实例外框先按调用方 parent 求出，再用自身 lining 得到展开顶层节点的 parent。

## 4. 引号字符串与词法

### 4.1 进入字符串模式

属性值仍从 `=` 后的非空白位置开始。只有第一个字符是双引号 `"` 时，才进入字符串模式；未加引号的旧写法保持原样。

```text
text=hello              旧式未加引号值
text="hello world"      字符串模式，解码为 hello world
text=abc"def            不进入字符串模式，双引号只是普通字符
```

字符串模式属于属性词法层，不是在属性解析完成后用正则修补。

### 4.2 字符串内容

字符串内部可以包含空格、制表符、`;`、`|`、`[` 和 `]`，这些字符不再充当分隔符：

```text
text="hello world;|[draft]"
```

当前仍不支持多行文本，字符串中的原始换行报错。字符串外的空白继续只承担格式化作用，不能代替 `;` 分隔属性。

### 4.3 转义

字符串内只支持两种转义：

```text
\"    双引号
\\    反斜杠
```

例如：

```text
text="say \"hi\" \\ ok"
```

解码后的正文为：

```text
say "hi" \ ok
```

其他反斜杠转义均报错，不静默丢弃反斜杠。Windows 路径可以使用正斜杠，或在引号内正确转义反斜杠。

### 4.4 闭合与后续字符

遇到未转义的配对 `"` 后退出字符串模式。闭合引号之后只允许：

- 格式化空白；
- `;`；
- `|`；
- 当前 primitive 的 `]`。

闭合引号后直接拼接其他字符属于错误。未闭合字符串、非法转义和非法后缀都产生带源文本行列号的 `LangError`。

解析器保存解码后的属性值，同时保留原始源文本范围供报告中的 `code` 使用。渲染正文不包含外围双引号，转义只解码一次。

## 5. 尺寸、常量与绝对像素

### 5.1 直接尺寸

```text
dimension = number, reference | constant_reference ;
number    = 有符号整数或分数 ;
reference = "pw" | "ph" | "w" | "h" | "a" | "px" ;
constant_reference = [number], "@", constant_name ;
```

数值与单位之间不插入空白。除 `px` 外，整数 `N` 表示 `N/100`，分数 `a/b` 直接表示 `a/b`；`px` 的整数直接表示画布坐标单位，分数也直接表示该数值：

```text
100pw  = 父宽度的 100%
1/12ph = 父高度的 1/12
-1/2w = 当前宽度的 -1/2
50a   = 固有尺寸的 50%
24px  = 24 个画布坐标单位
```

`px` 使用当前画布的绝对坐标系，不读取 parent，也不随父容器比例缩放。相对关系仍由 `pw/ph/w/h/a` 表达；固定像素意图由 `px` 显式表达。

v0.4 及更早版本的 `pw100、ph1/12、w-1/2、h-1/2、a50` 等前缀写法全部非法，不保留双语法。

允许组合：

| 属性 | 普通 primitive / ref | text/image |
|---|---|---|
| `x` | `pw、px` | `pw、px` |
| `y` | `ph、px` | `ph、px` |
| `w` | `pw、px` | `pw、a、px` |
| `h` | `ph、px` | `ph、a、px` |
| `xo` | `pw、w、px` | `pw、w、px` |
| `yo` | `ph、h、px` | `ph、h、px` |

`a` 只允许用于具有固有尺寸的 text/image 的 `w、h`。普通色块、容器和 ref 没有 auto 尺寸。所有 `w、h` 的计算结果必须严格大于零。

### 5.2 文件内常量

常量声明只能位于文档开头，每行一条：

```text
@margin=24px
@halfMargin=1/2@margin
@controlSize=30px
```

名称以 ASCII 字母开头，后续可以包含 ASCII 字母和数字，大小写敏感。常量值可以是直接尺寸，也可以是 `@先前常量`、`2@先前常量`、`1/2@先前常量`。倍数直接相乘，不采用整数的百分比解释。

常量只能引用当前文件中已经声明的名称；重复声明、前向引用、未知引用和零分母均报错。声明顺序消除了循环引用。常量可以保存任意受支持的尺寸单位，使用时仍按目标属性检查单位权限。

常量按定义文件隔离：主文档与每个 ref 定义各自解析常量，调用方常量不会泄入 ref，ref 常量也不会反向泄漏。它不是跨层 accessor、颜色 token、参数系统或通用表达式。

源码 `code.offset` 始终相对包含常量头的完整原文件，CRLF 等原始换行继续保留。

## 6. 固有尺寸与缩放

### 6.1 text

文本先根据内容、字体、像素字号和前景色渲染为透明 PNG。固有矩形采用：

- 宽度：字体的 advance 宽度，向上取整；
- 高度：`ascent + descent` 行高。

随后由通用 `w、h` 对这个位图进行缩放。`size` 控制文字原始排版，`w、h` 控制最终 layer 尺寸。

### 6.2 image

图片的固有尺寸是方向校正后的像素宽高。加载后同样转换为 PNG，再由 `w、h` 缩放。

### 6.3 auto

```text
w=100a   固有宽度
w=50a    固有宽度的一半
h=1/2a   固有高度的一半
```

省略 text/image 的 `w` 或 `h` 等价于对应属性的 `100a`。各属性独立计算，不偷偷保持宽高比：

```text
w=50a            宽度减半，省略的 h 仍为固有高度
w=50a;h=50a      两个方向都减半
w=50pw;h=1/4ph   强制使用父矩形比例
```

SVG 中的内容使用 `preserveAspectRatio="none"`。

## 7. 坐标、求值与覆盖

全局画布左上角为原点，`x` 向右为正，`y` 向下为正。顶层 primitive 的 parent 是全局画布。

普通节点求值顺序：

1. 确定当前类型的固有尺寸（如果存在）；
2. 根据 parent 或固有尺寸计算当前 `w、h`；
3. 根据 parent 和 anchor desugar 计算基础 `x、y`；
4. 使用 parent 或当前尺寸计算 `xo、yo`；
5. 得到当前节点最终矩形；
6. 应用 lining 得到子级 `parent_rect`，并检查其宽高严格大于零；
7. 依次绘制 `fill`、当前内容或展开内容、自有子节点。

子节点继承父节点应用 lining 后的矩形；未写 lining 时与父节点最终矩形相同。后绘制的内容覆盖先绘制的内容。内部几何使用精确分数，只在输出时转换为十进制。

实现不生成主动裁剪用的 `clipPath`。子节点和 ref 内部节点可以离开父矩形或全局画布，完整几何仍保留在报告中。

## 8. ref 展开与封装

ref 必须先求出实例最终矩形，再应用实例自身 lining，最后展开定义。被引用文档中的每个顶层 group 都以 lining 修正后的矩形作为 parent，按文档顺序独立求值。

同一个定义可以被多次引用。解析结果可以缓存，但节点几何、路径、源范围和子节点必须按实例重新创建。组件内部不能读取调用方属性，调用方也不能读取组件内部属性，因此 parent 在展开前始终确定。

嵌套 ref 使用当前定义文件目录解析下一层定义。每个展开边界在图层路径中用 `@定义名` 标记：

```text
0                         主文档中的 ref 实例
0@card/0                  card 的第一个顶层节点
0@card/0/0                card 根节点的第一个子节点
0@card/0@badge/0          card 内 badge 展开的第一个顶层节点
```

ref 自身可以拥有 `|` 子节点。完整顺序为：

```text
ref fill → ref 定义展开内容 → ref 自有子节点
```

实现提供三项确定性保护：

- 直接和间接引用循环报错，并给出引用链；
- 引用栈深度上限为 32；
- 一次场景求值创建的节点总数上限为 10000。

超过限制、定义缺失或定义解析失败均明确报错，不静默跳过组件。

## 9. SVG 与图层报告

`fill` 输出 SVG `<rect>`。text/image 内容输出带内嵌 PNG data URI 的 SVG `<image>`。每个输出元素带有 `data-layer` 和 `data-path`。ref 实例及其内部节点按展开顺序进入同一个 display list。

### 9.1 通用字段

JSON 报告包含：

- `spec_version`，当前为 `0.7`；
- 全局画布尺寸；
- 节点序号、路径与 `kind`；
- `fill、text`；
- `resource`：节点的主内容资源路径。image 为解析后的图片绝对路径，ref 为被引用定义文件绝对路径，rect/text 为 `null`；字体等辅助资源继续留在 `content_metadata`，不改变顶层 `resource`；
- `code`：当前 primitive 的源码文件与字符范围，详见 §9.2；
- `intrinsic` 固有尺寸；
- `content_metadata`；
- `scale`；
- `rect_px` 与 `rect_exact`；
- `parent_rect`：当前节点求值时实际收到的父矩形，字段为 `x/y/w/h` 的精确分数字符串；顶层为画布，普通子级和 ref 展开节点包含 lining 修正；
- `outside` 越界状态；
- 可选 `hit` 命中结果。

v0.7 在 `resource/code` 之外增加 `parent_rect`，用于直接检查 lining 和 ref 展开时的几何输入。

### 9.2 resource 与 code：资源产地和源码范围

`resource` 回答“内容从哪里加载”，`code` 回答“这个节点来自哪份源码、哪一段字符”。`code` 不保存代码正文，只保存位置。

例如 `main.txt` 包含：

```text
[ref=card;w=50pw;h=50ph]
```

该 ref 实例的报告片段为：

```json
{
  "resource": "/absolute/path/card.txt",
  "code": {
    "file": "/absolute/path/main.txt",
    "start": {"offset": 1, "line": 1, "column": 2},
    "end": {"offset": 23, "line": 1, "column": 24}
  }
}
```

其中 `resource` 指向要展开的组件，`code.file` 指向调用点所在源码。对原始 `main.txt` 做字符切片 `raw_source[1:23]`，得到 `ref=card;w=50pw;h=50ph`，不包含外围方括号。

`code` 的范围约定：

- 范围为半开区间 `[start,end)`；
- `offset` 是从零开始的原始文本字符偏移，不是 UTF-8 字节偏移；
- `line、column` 从一开始，CRLF 按一个换行计算；
- 范围从 primitive 第一个属性起，到最后一个属性值或末尾分号止，不包含外围 `[、|`、`]` 和尾随格式化空白；
- ref 实例的 `code.file` 指向调用点，ref 内部节点指向各自定义文件；重复实例的展开路径不同，但内部节点通常共享同一个 `code`；
- 通过 CLI 或提供 `source_path` 时，`code.file` 为对应文件绝对路径；纯内存构建且未提供路径时为 `null`，但偏移和行列仍保留；
- CLI 读取主文档和 ref 定义时保留 CRLF 等原始换行，不做 universal-newline 归一，因此 `raw_source[code.start.offset:code.end.offset]` 可以直接用于原文局部编辑。

位置路径回答“这个实例在展开树里的哪里”，`code` 回答“应该打开哪个文件、修改哪一段”。它只读、不参与求值，也不构成 namespace/accessor 语法。

### 9.3 scale

text/image 每层额外给出固定长度的双轴数组：

```json
{
  "scale": [0.5, 1.0]
}
```

计算方式：

```text
sx = 最终 rect.w / 固有宽度
sy = 最终 rect.h / 固有高度
scale = [sx,sy]
```

调用方可以直接区分：

| 数值状态 | 含义 |
|---|---|
| `[1,1]` | 原始固有尺寸 |
| `[s,s]` 且 `s≠1` | 等比缩放 |
| `[sx,sy]` 且 `sx≠sy` | 非等比缩放 |

字段始终是双轴数组，不在等比时退化成标量，也不另设 `scaled/stretched` 派生布尔值。比较和计算在实现内部使用精确分数；JSON 数值用于快速检查，若调用方需要精确相等判断，可由 `rect_exact` 和 `intrinsic` 重算，不依赖十进制浮点比较。普通矩形和 ref 没有固有内容尺寸，`scale` 为 `null`。

命中使用半开区间，按从底到顶返回所有几何候选，包括容器和 ref 实例。矩形命中不证明该点存在不透明像素。

## 10. 绝对像素与复杂局部

固定间距、像素级对齐和明确像素坐标使用 `px` 或文件内常量。相对比例与绝对像素是不同的显式意图：需要随 parent 保真关系时使用比例，需要固定画布坐标时使用 `px`，不要把 `xo=-48w` 误当成固定像素。

复杂局部绘制或像素级素材仍可使用 Python 等外部工具生成确定性 PNG，再用 image/ref 叠回。外部图片内部无需几何化；`px` 的加入不要求把复杂素材改写成 primitive。

## 11. 错误处理

解析和求值必须明确失败，不得静默修复。至少检查：

- 空文档、缺失 `]`、空 `|` 分段；
- 未知或不属于当前类型的属性、重复属性；
- 类型头不在第一位、同时出现多个类型头；
- 普通 primitive 或 ref 缺少 `w/h`；
- 非法 `size`、非法 anchor 或 anchor 与位置属性冲突；
- 非法 lining、lining 展开冲突，以及修正后非正的 parent 宽高；
- 裸数字、旧版前缀单位、非法单位、非法分数、零分母；
- 重复、前向或未知的常量引用，以及跨文件读取常量；
- 非正 `w/h`；
- 非法颜色；
- 未闭合字符串、非法转义、闭合引号后的非法后缀和字符串原始换行；
- 当前版本不支持的嵌套括号；
- 缺失字体、URL 图片、缺失图片和不支持的格式；
- 非法 ref 定义名、缺失定义、引用循环、深度或节点数超限。

解析错误包含对应源文本行号和列号；通过文件构建时同时给出源文件路径。字体、图片等求值或资源错误还必须携带当前节点路径和该节点的 `code` 源码范围，因为失败节点不会进入最终图层报告。CLI 应以源文件、行列和节点路径呈现这些错误，而不是只打印裸 `ValueError`。引用循环继续给出完整引用链。

CLI 的 stdout 和 stderr 固定使用 UTF-8，包括 Windows 上被 subprocess 管道接走的情况；调用方必须按 UTF-8 解码，而不是使用系统区域编码。SVG 和 JSON 文件同样使用 UTF-8。

越界不是错误，不自动 clamp。

## 12. 格式化约束

常量声明每行一条且只能位于文档开头；除此之外，字符串外的空白和换行不改变语义。字符串内的空格与制表符属于值内容。除类型头必须位于首位外，其他属性顺序不影响结果，但 anchor 冲突规则不变。

修改文档时：

- 只修改目标 primitive 或目标组件定义；
- 不重排顶层 group、兄弟节点或 `|` 分段；
- 不无意义地重写其他区域的数值形式；
- 推荐属性顺序为类型头、类型配置、anchor 或 `x、y、w、h、xo、yo`、`fill、color`；
- 修改共享定义后，检查所有实例的展开路径、源范围与矩形。

## 13. 验收基线

当前 72 项自测由十二组构成：

- 15 项矩形回归：既有几何、顺序、越界、命中和错误处理，并逐一拒绝 `pw/ph/w/h/a` 旧前缀写法；
- 11 项文字测试：固有尺寸、显式缩放、默认字号、填充顺序和错误处理；
- 2 项字体测试：默认候选回退，以及显式字体相对当前定义文件解析；
- 7 项图片测试：PNG/JPEG、固有尺寸、透明度、错误处理，以及 Windows 绝对路径与 URI scheme 的区分；
- 5 项引号测试：空格和分隔符、两种转义、带空格资源路径、未闭合行列、非法转义与闭合后缀；
- 8 项 ref 测试：实例、嵌套、循环与保护机制；
- 5 项 anchor 测试：16 个组合的最终矩形、5 个短形式别名、未占用轴手写与顺序无关、完整 key 冲突、非法和重复 anchor；
- 4 项报告测试：主文档与 ref 的 `code`、主资源与旧字段移除、双轴 `scale`，以及 CRLF 主文档和 ref 的原始 offset 切片；
- 6 项常量测试：直接 `px`、派生倍数、ref 文件隔离、非法声明与引用、单位权限，以及常量头下的 CRLF 源码范围；
- 7 项 lining 测试：直接四边、1／2／4 值展开、ref 与自有子级共享 parent、负值外扩、非正 parent 拒绝、常量与 `px`，以及冲突和非法输入；
- 1 项 CLI 测试：强制遗留区域编码时，管道 stdout/stderr 仍按 UTF-8 输出；
- 1 项错误上下文测试：嵌套 ref 内的资源失败包含 `code`、定义文件、行列和完整节点路径。

修改实现后运行：

```bash
python3 scripts/layerlang_mvp.py self-test
```

以上测试必须在修改实现后继续全部通过；新增语义应先修改规范与验收用例，再调整实现。

## 14. 已废弃或替换

- 普通色块的 `color`：由 `fill` 替代；`color` 现在专属于文字前景色；
- 裸数字默认单位：继续禁止；
- `ow/oh`：由当前 `w/h` 引用替代；
- `aspect`：不引入，使用显式 `w/h`；
- `text.font` 命名空间：类型头已提供上下文，直接使用 `font`；
- `parent.w@100` 通用访问语法：当前没有需求，保留封闭尺寸文法；
- 自动保持宽高比：与高效手动挡目标不符；
- ref 内部属性访问与调用点参数：破坏封装和 parent 确定性，不进入当前版本；
- 相对 CWD 的字体路径：由相对当前定义文件目录替代；
- text 强制显式 `size`：由明确的默认 `40` 替代；
- 用比例 offset 模拟绝对像素：v0.6 由显式 `px` 与文件内常量承接固定像素意图，复杂素材仍可用外部 PNG；
- 只支持 `anchor=center` 及其全局位置属性互斥：由完整按轴 desugar 和展开 key 冲突替代；
- 报告字段 `source/source_span`：由 `resource/code` 直接替代，不保留双 schema；
- 尺寸前缀文法：由 v0.5 的数字＋单位后缀文法整体替代，旧写法直接报错，不保留双语法；
- 通用 namespace/accessor：当前不引入。`pw/ph/w/h/a/px` 继续是封闭尺寸单位，类型头继续承担固定内容配置，报告字段只读且不参与求值；只有表达式、组件参数或跨层约束出现真实需求时才另行评审。

## 15. 未来脑洞（非规范）

- alpha、圆角、圆形等视觉 primitive，需另行评估是否值得增加语言表面积；
- 比例字号与更丰富的排版；
- 更多本地或受控图片来源；
- 保守的自动格式化器；
- 若未来出现真实需求，再单独评审组件参数，不预先开放任意属性访问。

未来扩展应继续守住：parent 在子级求值前确定，当前 layer 不读取子级或 ref 内部属性，跨层坐标访问保持受控；优先增强可检查报告，而不是只增加“能画什么”。
