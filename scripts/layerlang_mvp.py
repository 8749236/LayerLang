#!/usr/bin/env python3
"""Layerlang v0.2：矩形、文字、本地图片、本地引用与图层检查。

依赖：Python 标准库与 Pillow。
  python3 layerlang_mvp.py self-test
  python3 layerlang_mvp.py render 输入.txt --svg 输出.svg --layers 图层.json
  python3 layerlang_mvp.py render 输入.txt --svg 输出.svg --layers 图层.json --hit 600 600

文字和本地 PNG/JPEG 先转换为透明 PNG，再由通用 w/h 缩放。
ref=name 展开当前定义文件同目录的 name.txt，每个实例独立求值。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

SPEC_VERSION = "0.2"
HERE = Path(__file__).resolve().parent


class LangError(Exception):
    def __init__(self, message: str, source: str = "", position: int = 0):
        self.message = message
        self.position = max(0, min(position, len(source)))
        self.line = source.count("\n", 0, self.position) + 1
        previous_newline = source.rfind("\n", 0, self.position)
        self.column = self.position - previous_newline
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"第 {self.line} 行，第 {self.column} 列：{self.message}"


@dataclass
class Attr:
    name: str
    value: str
    position: int


@dataclass
class NodeSpec:
    attrs: dict[str, Attr]
    position: int


@dataclass
class Group:
    base: NodeSpec
    children: list[NodeSpec]


@dataclass(frozen=True)
class Rect:
    x: Fraction
    y: Fraction
    w: Fraction
    h: Fraction

    @property
    def right(self) -> Fraction:
        return self.x + self.w

    @property
    def bottom(self) -> Fraction:
        return self.y + self.h


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")



class Parser:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0

    def error(self, message: str, position: Optional[int] = None) -> LangError:
        return LangError(message, self.source, self.pos if position is None else position)

    def skip_space(self) -> None:
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def parse(self) -> list[Group]:
        groups: list[Group] = []
        self.skip_space()
        while self.pos < len(self.source):
            if self.source[self.pos] != "[":
                raise self.error("顶层只能出现由 [ ] 包裹的 primitive")
            groups.append(self.parse_group())
            self.skip_space()
        if not groups:
            raise self.error("文档为空", 0)
        return groups

    def parse_group(self) -> Group:
        start = self.pos
        self.pos += 1  # [
        segments: list[NodeSpec] = []
        while True:
            self.skip_space()
            if self.pos >= len(self.source):
                raise self.error("缺少结束的 ]", start)
            if self.source[self.pos] in "|]":
                raise self.error("存在空的 primitive 或空的 | 分段")
            segments.append(self.parse_segment())
            self.skip_space()
            if self.pos >= len(self.source):
                raise self.error("缺少结束的 ]", start)
            marker = self.source[self.pos]
            if marker == "|":
                self.pos += 1
                continue
            if marker == "]":
                self.pos += 1
                break
            raise self.error("预期 ;、| 或 ]")
        return Group(segments[0], segments[1:])

    def parse_segment(self) -> NodeSpec:
        segment_start = self.pos
        attrs: dict[str, Attr] = {}
        while True:
            self.skip_space()
            key_start = self.pos
            match = re.match(r"[a-z]+", self.source[self.pos:])
            if not match:
                raise self.error("预期属性名；当前版本不支持嵌套括号")
            name = match.group(0)
            self.pos += len(name)
            self.skip_space()
            if self.pos >= len(self.source) or self.source[self.pos] != "=":
                raise self.error(f"属性 {name} 后缺少 =", key_start)
            self.pos += 1
            self.skip_space()
            value_start = self.pos
            while self.pos < len(self.source):
                char = self.source[self.pos]
                if char.isspace() or char in ";|]":
                    break
                if char == "[":
                    raise self.error("当前版本不支持嵌套括号")
                self.pos += 1
            value = self.source[value_start:self.pos]
            if not value:
                raise self.error(f"属性 {name} 缺少值", value_start)
            if name in attrs:
                raise self.error(f"属性 {name} 重复", key_start)
            attrs[name] = Attr(name, value, value_start)
            self.skip_space()
            if self.pos < len(self.source) and self.source[self.pos] == ";":
                self.pos += 1
                self.skip_space()
                if self.pos < len(self.source) and self.source[self.pos] in "|]":
                    break
                continue
            break
        return NodeSpec(attrs, segment_start)


def parse(source: str) -> list[Group]:
    return Parser(source).parse()






def exact(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def number(value: Fraction) -> int | float:
    return value.numerator if value.denominator == 1 else float(value)


def decimal_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    with localcontext() as context:
        context.prec = 32
        rendered = format(Decimal(value.numerator) / Decimal(value.denominator), ".12f")
    return rendered.rstrip("0").rstrip(".")


def outside_status(rect: Rect, canvas: Rect) -> str:
    if rect.right <= 0 or rect.bottom <= 0 or rect.x >= canvas.w or rect.y >= canvas.h:
        return "full"
    if rect.x < 0 or rect.y < 0 or rect.right > canvas.w or rect.bottom > canvas.h:
        return "partial"
    return "inside"






LINUX_DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
DIMENSION_RE = re.compile(r"^(a|pw|ph|w|h)(-?\d+(?:/\d+)?)$")
COMMON_ATTRS = {"x", "y", "w", "h", "xo", "yo", "fill"}
TEXT_ATTRS = {"text", "font", "size", "color"}
IMAGE_ATTRS = {"image"}
REF_ATTRS = {"ref"}
MAX_REF_DEPTH = 32
MAX_EXPANDED_NODES = 10000


@dataclass(frozen=True)
class Dimension:
    unit: str
    scale: Fraction


@dataclass
class Node:
    rect: object
    path: str
    fill: Optional[str]
    kind: str
    text: Optional[str] = None
    source: Optional[str] = None
    png: Optional[bytes] = None
    intrinsic: Optional[tuple[int, int]] = None
    metadata: dict[str, object] = field(default_factory=dict)
    expansion: list["Node"] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)


@dataclass
class Scene:
    width: int
    height: int
    roots: list[Node]
    layers: list[Node]

    @property
    def canvas_rect(self):
        return Rect(Fraction(0), Fraction(0), Fraction(self.width), Fraction(self.height))


@dataclass
class RefContext:
    resource_dir: Path
    cache: dict[Path, tuple[str, list[Group]]] = field(default_factory=dict)
    stack: list[Path] = field(default_factory=list)
    created_nodes: int = 0


def parse_dimension(attr, source: str, allowed: set[str]) -> Dimension:
    match = DIMENSION_RE.fullmatch(attr.value)
    if not match:
        raise LangError(f"{attr.name} 必须使用 单位+数字，例如 pw100、ph1/12、a50、w-1/2；收到 {attr.value!r}", source, attr.position)
    unit, number = match.groups()
    if unit not in allowed:
        raise LangError(f"{attr.name} 不允许单位 {unit}；允许：{sorted(allowed)}", source, attr.position)
    if "/" in number:
        numerator, denominator = number.split("/", 1)
        if int(denominator) == 0:
            raise LangError("分数分母不能为零", source, attr.position)
        scale = Fraction(int(numerator), int(denominator))
    else:
        scale = Fraction(int(number), 100)
    return Dimension(unit, scale)


def validate_color(attr, source: str) -> str:
    if not COLOR_RE.fullmatch(attr.value):
        raise LangError("颜色只接受 #RRGGBB 六位十六进制颜色", source, attr.position)
    return attr.value.lower()


def default_font_candidates() -> tuple[Path, ...]:
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    return (
        Path(LINUX_DEFAULT_FONT),
        windows_fonts / "NotoSansSC-Regular.ttf",
        windows_fonts / "NotoSansSC-VF.ttf",
    )


def resolve_default_font(candidates: Optional[tuple[Path, ...]] = None) -> str:
    candidates = default_font_candidates() if candidates is None else candidates
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    attempted = "、".join(str(path) for path in candidates)
    raise ValueError(f"没有找到默认字体；请用 font=字体文件路径 显式指定。已尝试：{attempted}")


def rasterize_text(text: str, font_path: str, size: int, color: str) -> tuple[bytes, tuple[int, int], dict[str, object]]:
    path = resolve_default_font() if font_path == "default" else font_path
    try:
        font = ImageFont.truetype(path, size=size)
    except OSError as error:
        raise ValueError(f"无法加载固定字体 {path!r}：{error}") from error
    ascent, descent = font.getmetrics()
    width = max(1, math.ceil(font.getlength(text)))
    height = ascent + descent
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((0, ascent), text, font=font, fill=color, anchor="ls")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    metadata = {
        "font_path": path,
        "size_px": size,
        "ascent": ascent,
        "descent": descent,
        "advance_width": width,
        "line_height": height,
        "foreground": color,
    }
    return buffer.getvalue(), (width, height), metadata


def load_image(source_name: str, resource_dir: Path) -> tuple[bytes, tuple[int, int], dict[str, object], str]:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source_name):
        raise ValueError("image 只接受本地文件，不接受 URL 或 data URI")
    path = Path(source_name)
    if not path.is_absolute():
        path = resource_dir / path
    path = path.resolve()
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("image 只接受 .png、.jpg、.jpeg")
    try:
        with Image.open(path) as opened:
            if opened.format not in {"PNG", "JPEG"}:
                raise ValueError("图片内容必须是 PNG 或 JPEG")
            source_format = opened.format
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            image.load()
    except OSError as error:
        raise ValueError(f"无法读取图片 {str(path)!r}：{error}") from error
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("图片固有尺寸必须大于零")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    metadata = {"source_path": str(path), "source_format": source_format}
    return buffer.getvalue(), (width, height), metadata, str(path)


def validate_common(spec, source: str, kind: str) -> dict[str, object]:
    attrs = spec.attrs
    type_attrs = TEXT_ATTRS if kind == "text" else IMAGE_ATTRS if kind == "image" else REF_ATTRS if kind == "ref" else set()
    allowed = COMMON_ATTRS | type_attrs
    for attr in attrs.values():
        if attr.name not in allowed:
            raise LangError(f"未知属性 {attr.name}，或当前 primitive 类型不允许该属性", source, attr.position)
    fill = validate_color(attrs["fill"], source) if "fill" in attrs else None
    dimensions: dict[str, Dimension] = {}
    for name in ("x", "xo"):
        if name in attrs:
            dimensions[name] = parse_dimension(attrs[name], source, {"pw"} if name == "x" else {"pw", "w"})
    for name in ("y", "yo"):
        if name in attrs:
            dimensions[name] = parse_dimension(attrs[name], source, {"ph"} if name == "y" else {"ph", "h"})
    has_intrinsic = kind in {"text", "image"}
    width_units = {"pw", "a"} if has_intrinsic else {"pw"}
    height_units = {"ph", "a"} if has_intrinsic else {"ph"}
    if has_intrinsic:
        dimensions["w"] = parse_dimension(attrs["w"], source, width_units) if "w" in attrs else Dimension("a", Fraction(1))
        dimensions["h"] = parse_dimension(attrs["h"], source, height_units) if "h" in attrs else Dimension("a", Fraction(1))
    else:
        for required in ("w", "h"):
            if required not in attrs:
                label = "ref" if kind == "ref" else "普通"
                suffix = "；ref 没有 auto 尺寸" if kind == "ref" else ""
                raise LangError(f"{label} primitive 缺少 {required}{suffix}", source, spec.position)
        dimensions["w"] = parse_dimension(attrs["w"], source, width_units)
        dimensions["h"] = parse_dimension(attrs["h"], source, height_units)
    return {"fill": fill, "dimensions": dimensions}


def evaluate_node(spec, parent, path: str, source: str, context: RefContext) -> Node:
    context.created_nodes += 1
    if context.created_nodes > MAX_EXPANDED_NODES:
        raise ValueError(f"展开节点数超过上限 {MAX_EXPANDED_NODES}")
    names = list(spec.attrs)
    heads = [name for name in ("text", "image", "ref") if name in spec.attrs]
    if len(heads) > 1:
        raise LangError("一个 primitive 只能有一个类型头", source, spec.position)
    kind = heads[0] if heads else "rect"
    if heads and names[0] != heads[0]:
        raise LangError(f"{kind} 是类型头，必须是第一个属性", source, spec.attrs[kind].position)
    common = validate_common(spec, source, kind)
    dimensions = common["dimensions"]
    current_dir = context.stack[-1].parent if context.stack else context.resource_dir

    text = None
    source_name = None
    png = None
    intrinsic = None
    metadata: dict[str, object] = {}
    if kind == "text":
        if "size" not in spec.attrs:
            raise LangError("text 要求显式 size", source, spec.position)
        size_attr = spec.attrs["size"]
        if not re.fullmatch(r"\d+", size_attr.value) or int(size_attr.value) <= 0:
            raise LangError("size 必须是正整数像素", source, size_attr.position)
        foreground = validate_color(spec.attrs["color"], source) if "color" in spec.attrs else "#000000"
        font_path = spec.attrs["font"].value if "font" in spec.attrs else "default"
        text = spec.attrs["text"].value
        png, intrinsic, metadata = rasterize_text(text, font_path, int(size_attr.value), foreground)
    elif kind == "image":
        source_name = spec.attrs["image"].value
        png, intrinsic, metadata, source_name = load_image(source_name, current_dir)

    def basis(dimension: Dimension, own_w: Fraction, own_h: Fraction) -> Fraction:
        if dimension.unit == "pw":
            return parent.w
        if dimension.unit == "ph":
            return parent.h
        if dimension.unit == "w":
            return own_w
        if dimension.unit == "h":
            return own_h
        raise AssertionError("auto 尺寸需要分别提供基准")

    natural_w = Fraction(intrinsic[0]) if intrinsic else Fraction(0)
    natural_h = Fraction(intrinsic[1]) if intrinsic else Fraction(0)
    w_dim = dimensions["w"]
    h_dim = dimensions["h"]
    width = w_dim.scale * (natural_w if w_dim.unit == "a" else basis(w_dim, Fraction(0), Fraction(0)))
    height = h_dim.scale * (natural_h if h_dim.unit == "a" else basis(h_dim, Fraction(0), Fraction(0)))
    if width <= 0 or height <= 0:
        raise LangError("w、h 的计算结果必须大于零", source, spec.position)

    def displacement(name: str) -> Fraction:
        dimension = dimensions.get(name)
        return Fraction(0) if dimension is None else dimension.scale * basis(dimension, width, height)

    x = parent.x + displacement("x") + displacement("xo")
    y = parent.y + displacement("y") + displacement("yo")
    rect = Rect(x, y, width, height)
    node = Node(rect=rect, path=path, fill=common["fill"], kind=kind, text=text, source=source_name, png=png, intrinsic=intrinsic, metadata=metadata)
    if kind == "ref":
        ref_name = spec.attrs["ref"].value
        if not ref_name or ref_name in {".", ".."} or "/" in ref_name or "\\" in ref_name or ref_name.endswith(".txt"):
            raise LangError("ref 只接受不带路径和 .txt 后缀的定义名", source, spec.attrs["ref"].position)
        target = (current_dir / f"{ref_name}.txt").resolve()
        if target in context.stack:
            chain = " → ".join(path.name for path in [*context.stack, target])
            raise ValueError(f"ref 引用循环：{chain}")
        if len(context.stack) >= MAX_REF_DEPTH:
            raise ValueError(f"ref 引用深度超过上限 {MAX_REF_DEPTH}")
        if target not in context.cache:
            try:
                definition_source = target.read_text(encoding="utf-8")
            except OSError as error:
                raise ValueError(f"无法读取 ref 定义 {str(target)!r}：{error}") from error
            context.cache[target] = (definition_source, parse(definition_source))
        definition_source, groups = context.cache[target]
        context.stack.append(target)
        try:
            node.expansion = evaluate_groups(groups, rect, f"{path}@{ref_name}", definition_source, context)
        finally:
            context.stack.pop()
        node.source = str(target)
        node.metadata = {"ref_path": str(target), "expanded_roots": len(node.expansion)}
    return node


def evaluate_groups(groups: list[Group], parent: Rect, prefix: str, source: str, context: RefContext) -> list[Node]:
    roots: list[Node] = []
    for root_index, group in enumerate(groups):
        root_path = f"{prefix}/{root_index}" if prefix else str(root_index)
        root = evaluate_node(group.base, parent, root_path, source, context)
        root.children = [
            evaluate_node(child, root.rect, f"{root_path}/{index}", source, context)
            for index, child in enumerate(group.children)
        ]
        roots.append(root)
    return roots


def flatten(nodes: list[Node]) -> list[Node]:
    result: list[Node] = []
    def visit(node: Node) -> None:
        result.append(node)
        for expanded in node.expansion:
            visit(expanded)
        for child in node.children:
            visit(child)
    for node in nodes:
        visit(node)
    return result


def build_scene(source: str, width: int = 1200, height: int = 1200, resource_dir: Optional[Path] = None, source_path: Optional[Path] = None) -> Scene:
    resource_dir = HERE if resource_dir is None else resource_dir
    stack = [source_path.resolve()] if source_path is not None else []
    context = RefContext(resource_dir=resource_dir, stack=stack)
    canvas = Rect(Fraction(0), Fraction(0), Fraction(width), Fraction(height))
    roots = evaluate_groups(parse(source), canvas, "", source, context)
    return Scene(width, height, roots, flatten(roots))


def render_svg(scene: Scene) -> str:
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{scene.width}" height="{scene.height}" viewBox="0 0 {scene.width} {scene.height}">',
        "  <!-- Layerlang v0.2: deterministic layers; content is scaled without preserving aspect ratio. -->",
    ]
    for index, node in enumerate(scene.layers):
        rect = node.rect
        geometry = (
            f'x="{decimal_text(rect.x)}" y="{decimal_text(rect.y)}" '
            f'width="{decimal_text(rect.w)}" height="{decimal_text(rect.h)}"'
        )
        if node.fill:
            lines.append(f'  <rect data-layer="{index}" data-path="{node.path}" {geometry} fill="{node.fill}"/>')
        if node.kind in {"text", "image"}:
            assert node.png is not None
            encoded = base64.b64encode(node.png).decode("ascii")
            lines.append(
                f'  <image data-layer="{index}" data-path="{node.path}" {geometry} '
                f'preserveAspectRatio="none" href="data:image/png;base64,{encoded}" xlink:href="data:image/png;base64,{encoded}"/>'
            )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def hit(scene: Scene, x: int | float | str, y: int | float | str) -> list[Node]:
    px, py = Fraction(str(x)), Fraction(str(y))
    return [node for node in scene.layers if node.rect.x <= px < node.rect.right and node.rect.y <= py < node.rect.bottom]


def scene_report(scene: Scene, hit_point: Optional[tuple[str, str]] = None) -> dict[str, object]:
    layers = []
    outside = {"partial": [], "full": []}
    for index, node in enumerate(scene.layers):
        status = outside_status(node.rect, scene.canvas_rect)
        if status != "inside":
            outside[status].append(node.path)
        layers.append({
            "index": index,
            "path": node.path,
            "kind": node.kind,
            "fill": node.fill,
            "text": node.text,
            "source": node.source,
            "intrinsic": list(node.intrinsic) if node.intrinsic else None,
            "content_metadata": node.metadata or None,
            "rect_px": {key: number(value) for key, value in {
                "x": node.rect.x, "y": node.rect.y, "w": node.rect.w, "h": node.rect.h,
            }.items()},
            "rect_exact": {key: exact(value) for key, value in {
                "x": node.rect.x, "y": node.rect.y, "w": node.rect.w, "h": node.rect.h,
            }.items()},
            "outside": status,
        })
    report = {"canvas": {"width": scene.width, "height": scene.height}, "spec_version": SPEC_VERSION, "layers": layers, "outside": outside}
    if hit_point is not None:
        matches = hit(scene, hit_point[0], hit_point[1])
        report["hit"] = {"x": hit_point[0], "y": hit_point[1], "paths_bottom_to_top": [node.path for node in matches]}
    return report



class RectTests(unittest.TestCase):
    def test_01_full_black_canvas(self) -> None:
        scene = build_scene("[w=pw100;h=ph100;fill=#000000]", 1200, 630)
        self.assertEqual(len(scene.layers), 1)
        self.assertEqual(scene.layers[0].rect, Rect(Fraction(0), Fraction(0), Fraction(1200), Fraction(630)))
        self.assertIn('fill="#000000"', render_svg(scene))

    def test_02_siblings_share_parent_and_draw_left_to_right(self) -> None:
        source = "[w=pw100;h=ph100|w=pw1/2;h=ph100;fill=#ff0000|x=pw1/4;w=pw1/2;h=ph100;fill=#0000ff]"
        scene = build_scene(source)
        self.assertEqual([node.path for node in scene.layers], ["0", "0/0", "0/1"])
        self.assertEqual(scene.layers[1].rect.x, 0)
        self.assertEqual(scene.layers[2].rect.x, 300)
        svg = render_svg(scene)
        self.assertLess(svg.index("#ff0000"), svg.index("#0000ff"))

    def test_03_own_size_offsets_center_a_block(self) -> None:
        source = "[w=pw100;h=ph100|x=pw1/2;y=ph1/2;w=pw1/4;h=ph1/4;xo=w-1/2;yo=h-1/2;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[1].rect, Rect(Fraction(450), Fraction(450), Fraction(300), Fraction(300)))

    def test_04_children_are_not_clipped_by_parent(self) -> None:
        source = "[x=pw1/4;y=ph1/4;w=pw1/4;h=ph1/4|x=pw1/1;w=pw1/2;h=ph1/2;fill=#ff0000]"
        scene = build_scene(source)
        child = scene.layers[1]
        self.assertEqual(child.rect, Rect(Fraction(600), Fraction(300), Fraction(150), Fraction(150)))
        self.assertEqual(outside_status(child.rect, scene.canvas_rect), "inside")
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_05_fully_outside_layer_is_reported(self) -> None:
        source = "[w=pw100;h=ph100|x=pw2/1;w=pw1/4;h=ph1/4;fill=#ff0000]"
        scene = build_scene(source)
        report = scene_report(scene)
        self.assertEqual(report["outside"], {"partial": [], "full": ["0/0"]})
        self.assertIn('x="2400"', render_svg(scene))

    def test_fraction_and_percent_have_distinct_exact_rules(self) -> None:
        scene = build_scene(
            "[w=pw100;h=ph100|w=pw1/12;h=ph100;fill=#ff0000|w=pw1;h=ph1/2;fill=#000000|w=pw50;h=ph100;fill=#ffffff]"
        )
        self.assertEqual(scene.layers[0].rect.w, 1200)
        self.assertEqual(scene.layers[1].rect.w, 100)
        self.assertEqual(scene.layers[2].rect.w, 12)
        self.assertEqual(scene.layers[2].rect.h, 600)
        self.assertEqual(scene.layers[3].rect.w, 600)

    def test_parent_final_offset_rect_is_child_reference(self) -> None:
        source = "[x=pw1/4;w=pw1/2;h=ph100;xo=pw-1/12|w=pw100;h=ph100;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[0].rect.x, 200)
        self.assertEqual(scene.layers[1].rect.x, 200)

    def test_hit_returns_bottom_to_top_including_containers(self) -> None:
        source = "[w=pw100;h=ph100|w=pw100;h=ph100;fill=#ffffff|x=pw1/4;y=ph1/4;w=pw1/2;h=ph1/2;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual([node.path for node in hit(scene, 600, 600)], ["0", "0/0", "0/1"])
        self.assertEqual([node.path for node in hit(scene, 10, 10)], ["0", "0/0"])
        self.assertEqual(hit(scene, -1, 10), [])

    def test_prettify_does_not_change_semantics(self) -> None:
        compact = "[w=pw100;h=ph100|x=pw1/2;y=ph1/2;w=pw1/4;h=ph1/4;xo=w-1/2;yo=h-1/2;fill=#000000]"
        pretty = """[
            w=pw100; h=ph100
            |
            x=pw1/2; y=ph1/2;
            w=pw1/4; h=ph1/4;
            xo=w-1/2; yo=h-1/2;
            fill=#000000;
        ]"""
        self.assertEqual(render_svg(build_scene(compact)), render_svg(build_scene(pretty)))

    def test_multiple_roots_keep_document_order(self) -> None:
        source = "[w=pw100;h=ph100;fill=#ffffff][w=pw1/2;h=ph100;fill=#000000]"
        svg = render_svg(build_scene(source))
        self.assertLess(svg.index("#ffffff"), svg.index("#000000"))

    def test_rejects_bare_numbers(self) -> None:
        with self.assertRaisesRegex(LangError, "单位\\+数字"):
            build_scene("[w=100;h=ph100]")

    def test_rejects_current_size_unit_in_width(self) -> None:
        with self.assertRaisesRegex(LangError, "不允许单位 w"):
            build_scene("[w=w100;h=ph100]")

    def test_rejects_unknown_duplicate_and_nonpositive(self) -> None:
        with self.assertRaisesRegex(LangError, "未知属性"):
            build_scene("[w=pw100;h=ph100;aspect=1]")
        with self.assertRaisesRegex(LangError, "重复"):
            build_scene("[w=pw100;w=pw50;h=ph100]")
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[w=pw0;h=ph100]")

    def test_rejects_bad_structure_and_color(self) -> None:
        with self.assertRaisesRegex(LangError, "空的"):
            build_scene("[w=pw100;h=ph100|]")
        with self.assertRaisesRegex(LangError, "六位"):
            build_scene("[w=pw100;h=ph100;fill=#000]")
        with self.assertRaisesRegex(LangError, "嵌套括号"):
            build_scene("[w=pw100;h=ph100[w=pw50;h=ph50]]")



class TextTests(unittest.TestCase):
    def test_auto_dimensions_come_from_text_bitmap(self) -> None:
        scene = build_scene("[text=测试;size=40]")
        node = scene.layers[0]
        assert node.intrinsic is not None
        self.assertGreater(node.intrinsic[0], 0)
        self.assertGreater(node.intrinsic[1], 40)
        self.assertEqual(node.rect.w, node.intrinsic[0])
        self.assertEqual(node.rect.h, node.intrinsic[1])
        self.assertEqual(node.metadata["size_px"], 40)

    def test_auto_text_can_center_with_own_offsets(self) -> None:
        scene = build_scene("[w=pw100;h=ph100|text=测试;size=40;x=pw1/2;y=ph1/2;xo=w-1/2;yo=h-1/2]")
        node = scene.layers[1]
        self.assertEqual(node.rect.x + node.rect.w / 2, 600)
        self.assertEqual(node.rect.y + node.rect.h / 2, 600)

    def test_auto_scaling_is_explicitly_nonuniform(self) -> None:
        scene = build_scene("[text=测试;size=40;w=a50;h=a100]")
        node = scene.layers[0]
        assert node.intrinsic is not None
        self.assertEqual(node.rect.w, Fraction(node.intrinsic[0], 2))
        self.assertEqual(node.rect.h, node.intrinsic[1])
        self.assertIn('preserveAspectRatio="none"', render_svg(scene))

    def test_parent_dimensions_can_stretch_text_bitmap(self) -> None:
        scene = build_scene("[text=测试;size=40;w=pw50;h=ph1/4]")
        self.assertEqual(scene.layers[0].rect.w, 600)
        self.assertEqual(scene.layers[0].rect.h, 300)

    def test_fill_precedes_text_content(self) -> None:
        svg = render_svg(build_scene("[text=测试;size=40;fill=#f2c94c;color=#202020]"))
        self.assertLess(svg.index("<rect"), svg.index("<image"))
        self.assertIn('fill="#f2c94c"', svg)
        report = scene_report(build_scene("[text=测试;size=40;color=#202020]"))
        self.assertEqual(report["layers"][0]["content_metadata"]["foreground"], "#202020")

    def test_text_can_be_parent_without_clipping_children(self) -> None:
        scene = build_scene("[text=父;size=40|x=pw2/1;w=pw100;h=ph100;fill=#ff0000]")
        child = scene.layers[1]
        self.assertGreater(child.rect.x, scene.layers[0].rect.right)
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_rejects_misplaced_type_head_and_plain_auto(self) -> None:
        with self.assertRaisesRegex(LangError, "类型头"):
            build_scene("[size=40;text=测试]")
        with self.assertRaisesRegex(LangError, "不允许单位 a"):
            build_scene("[w=a100;h=ph100]")

    def test_rejects_zero_negative_and_missing_size(self) -> None:
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[text=测试;size=40;w=a0]")
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[text=测试;size=40;w=a-50]")
        with self.assertRaisesRegex(LangError, "显式 size"):
            build_scene("[text=测试]")

    def test_rejects_spaces_in_literals(self) -> None:
        with self.assertRaises(LangError):
            build_scene("[text=hello world;size=40]")

    def test_hit_uses_scaled_rect(self) -> None:
        scene = build_scene("[w=pw100;h=ph100|text=测试;size=40;x=pw1/2;y=ph1/2;xo=w-1/2;yo=h-1/2]")
        paths = [node.path for node in hit(scene, 600, 600)]
        self.assertEqual(paths, ["0", "0/0"])


class FontTests(unittest.TestCase):
    def test_default_font_fallback_and_diagnostic(self) -> None:
        candidate_names = {path.name for path in default_font_candidates()}
        self.assertIn("NotoSansSC-Regular.ttf", candidate_names)
        self.assertIn("NotoSansSC-VF.ttf", candidate_names)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            missing = directory / "NotoSansSC-Regular.ttf"
            variable = directory / "NotoSansSC-VF.ttf"
            variable.write_bytes(b"")
            self.assertEqual(
                resolve_default_font((missing, variable)),
                str(variable.resolve()),
            )
            with self.assertRaisesRegex(ValueError, "font=字体文件路径"):
                resolve_default_font((missing,))


class ImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((10, 5, 19, 14), fill=(255, 0, 0, 255))
        image.save(self.directory / "sample.png")
        Image.new("RGB", (30, 10), (20, 40, 60)).save(self.directory / "sample.jpg")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, body: str) -> Scene:
        return build_scene(body, resource_dir=self.directory)

    def test_image_auto_dimensions_come_from_file(self) -> None:
        node = self.build("[image=sample.png]").layers[0]
        self.assertEqual(node.kind, "image")
        self.assertEqual(node.intrinsic, (40, 20))
        self.assertEqual(node.rect.w, 40)
        self.assertEqual(node.rect.h, 20)
        self.assertEqual(node.metadata["source_format"], "PNG")

    def test_image_auto_scaling_and_parent_stretch(self) -> None:
        scaled = self.build("[image=sample.png;w=a50;h=a200]").layers[0]
        self.assertEqual((scaled.rect.w, scaled.rect.h), (20, 40))
        stretched = self.build("[image=sample.png;w=pw50;h=ph1/4]").layers[0]
        self.assertEqual((stretched.rect.w, stretched.rect.h), (600, 300))

    def test_image_alpha_survives_png_normalization(self) -> None:
        node = self.build("[image=sample.png]").layers[0]
        assert node.png is not None
        with Image.open(io.BytesIO(node.png)) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((12, 8)), (255, 0, 0, 255))

    def test_fill_precedes_image_and_image_can_be_parent(self) -> None:
        scene = self.build("[image=sample.png;fill=#f2c94c|x=pw2/1;w=pw100;h=ph100;fill=#ff0000]")
        svg = render_svg(scene)
        self.assertLess(svg.index("<rect"), svg.index("<image"))
        self.assertGreater(scene.layers[1].rect.x, scene.layers[0].rect.right)
        self.assertNotIn("<clipPath", svg)

    def test_jpeg_is_accepted(self) -> None:
        node = self.build("[image=sample.jpg]").layers[0]
        self.assertEqual(node.intrinsic, (30, 10))
        self.assertEqual(node.metadata["source_format"], "JPEG")

    def test_rejects_remote_missing_and_misplaced_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "只接受本地文件"):
            self.build("[image=https://example.com/a.png]")
        with self.assertRaisesRegex(ValueError, "无法读取图片"):
            self.build("[image=missing.png]")
        with self.assertRaisesRegex(LangError, "类型头"):
            self.build("[w=pw100;image=sample.png]")



class RefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.write("card", "[w=pw100;h=ph100;fill=#ffffff|x=pw1/4;y=ph1/4;w=pw1/2;h=ph1/2;fill=#ff0000]")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, source: str) -> None:
        (self.directory / f"{name}.txt").write_text(source, encoding="utf-8")

    def build(self, source: str) -> Scene:
        main = self.directory / "main.txt"
        main.write_text(source, encoding="utf-8")
        return build_scene(source, resource_dir=self.directory, source_path=main)

    def test_basic_ref_expands_into_instance_rect(self) -> None:
        scene = self.build("[ref=card;w=pw1/2;h=ph1/4]")
        self.assertEqual([node.path for node in scene.layers], ["0", "0@card/0", "0@card/0/0"])
        self.assertEqual(scene.layers[0].rect, Rect(Fraction(0), Fraction(0), Fraction(600), Fraction(300)))
        self.assertEqual(scene.layers[1].rect, scene.layers[0].rect)
        self.assertEqual(scene.layers[2].rect.x, 150)
        self.assertEqual(scene.layers[2].rect.y, 75)

    def test_repeated_refs_have_independent_instances(self) -> None:
        scene = self.build("[w=pw100;h=ph100|ref=card;w=pw1/2;h=ph100|ref=card;x=pw1/2;w=pw1/2;h=ph100]")
        centers = [node.path for node in scene.layers]
        self.assertIn("0/0@card/0/0", centers)
        self.assertIn("0/1@card/0/0", centers)
        first = next(node for node in scene.layers if node.path == "0/0@card/0/0")
        second = next(node for node in scene.layers if node.path == "0/1@card/0/0")
        self.assertEqual(first.rect.x, 150)
        self.assertEqual(second.rect.x, 750)

    def test_nested_refs_and_content_order(self) -> None:
        self.write("badge", "[w=pw100;h=ph100;fill=#0000ff]")
        self.write("outer", "[ref=badge;w=pw100;h=ph100]")
        scene = self.build("[ref=outer;w=pw100;h=ph100;fill=#f2c94c|w=pw100;h=ph100;fill=#202020]")
        self.assertEqual(
            [node.path for node in scene.layers],
            ["0", "0@outer/0", "0@outer/0@badge/0", "0/0"],
        )

    def test_ref_can_contain_text_and_be_parent(self) -> None:
        self.write("label", "[text=引用;size=40]")
        scene = self.build("[ref=label;w=pw100;h=ph100|x=pw2/1;w=pw100;h=ph100;fill=#ff0000]")
        self.assertEqual(scene.layers[1].kind, "text")
        self.assertGreater(scene.layers[2].rect.x, scene.layers[0].rect.right)
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_direct_and_indirect_cycles_are_rejected(self) -> None:
        self.write("a", "[ref=a;w=pw100;h=ph100]")
        with self.assertRaisesRegex(ValueError, "引用循环"):
            self.build("[ref=a;w=pw100;h=ph100]")
        self.write("a", "[ref=b;w=pw100;h=ph100]")
        self.write("b", "[ref=a;w=pw100;h=ph100]")
        with self.assertRaisesRegex(ValueError, "引用循环"):
            self.build("[ref=a;w=pw100;h=ph100]")

    def test_missing_auto_misplaced_and_path_refs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "无法读取 ref"):
            self.build("[ref=missing;w=pw100;h=ph100]")
        with self.assertRaisesRegex(LangError, "不允许单位 a"):
            self.build("[ref=card;w=a100;h=ph100]")
        with self.assertRaisesRegex(LangError, "类型头"):
            self.build("[w=pw100;ref=card;h=ph100]")
        with self.assertRaisesRegex(LangError, "定义名"):
            self.build("[ref=../card;w=pw100;h=ph100]")

    def test_depth_guard(self) -> None:
        global MAX_REF_DEPTH
        original = MAX_REF_DEPTH
        MAX_REF_DEPTH = 2
        try:
            self.write("a", "[ref=b;w=pw100;h=ph100]")
            self.write("b", "[w=pw100;h=ph100]")
            with self.assertRaisesRegex(ValueError, "深度超过上限"):
                self.build("[ref=a;w=pw100;h=ph100]")
        finally:
            MAX_REF_DEPTH = original

    def test_expanded_node_guard(self) -> None:
        global MAX_EXPANDED_NODES
        original = MAX_EXPANDED_NODES
        MAX_EXPANDED_NODES = 2
        try:
            with self.assertRaisesRegex(ValueError, "节点数超过上限"):
                self.build("[ref=card;w=pw100;h=ph100]")
        finally:
            MAX_EXPANDED_NODES = original


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Layerlang v0.2 渲染器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("input")
    render_parser.add_argument("--width", type=int, default=1200)
    render_parser.add_argument("--height", type=int, default=1200)
    render_parser.add_argument("--svg", required=True)
    render_parser.add_argument("--layers", required=True)
    render_parser.add_argument("--hit", nargs=2, metavar=("X", "Y"))
    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    if args.command == "self-test":
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite([
            loader.loadTestsFromTestCase(RectTests),
            loader.loadTestsFromTestCase(TextTests),
            loader.loadTestsFromTestCase(FontTests),
            loader.loadTestsFromTestCase(ImageTests),
            loader.loadTestsFromTestCase(RefTests),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    try:
        input_path = Path(args.input)
        source = input_path.read_text(encoding="utf-8")
        scene = build_scene(source, args.width, args.height, input_path.resolve().parent, input_path.resolve())
        Path(args.svg).write_text(render_svg(scene), encoding="utf-8")
        report = scene_report(scene, tuple(args.hit) if args.hit else None)
        Path(args.layers).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except LangError as error:
        print(f"输入错误：{error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"运行错误：{error}", file=sys.stderr)
        return 1
    print(f"已生成：{args.svg}")
    print(f"已生成：{args.layers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
