#!/usr/bin/env python3
"""Layerlang v0.8：矩形、文字、本地图片、参数化本地引用、文件内常量、lining 与可溯源图层检查。

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
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

SPEC_VERSION = "0.8"
HERE = Path(__file__).resolve().parent


def source_line_column(source: str, position: int) -> tuple[int, int]:
    position = max(0, min(position, len(source)))
    line = 1
    line_start = 0
    index = 0
    while index < position:
        char = source[index]
        if char == "\r":
            if index + 1 < position and source[index + 1] == "\n":
                index += 1
            line += 1
            line_start = index + 1
        elif char == "\n":
            line += 1
            line_start = index + 1
        index += 1
    return line, position - line_start + 1


class LangError(Exception):
    def __init__(self, message: str, source: str = "", position: int = 0):
        self.message = message
        self.source_path: Optional[str] = None
        self.position = max(0, min(position, len(source)))
        self.line, self.column = source_line_column(source, self.position)
        super().__init__(str(self))

    def __str__(self) -> str:
        prefix = f"{self.source_path}：" if self.source_path else ""
        return f"{prefix}第 {self.line} 行，第 {self.column} 列：{self.message}"


class EvaluationError(ValueError):
    def __init__(self, message: str, code: dict[str, object], node_path: str):
        self.message = message
        self.code = code
        self.node_path = node_path
        super().__init__(str(self))

    def __str__(self) -> str:
        start = self.code["start"]
        assert isinstance(start, dict)
        source = self.code["file"] or "<memory>"
        return f"{source}：第 {start['line']} 行，第 {start['column']} 列（节点 {self.node_path}）：{self.message}"


@dataclass
class Attr:
    name: str
    value: str
    position: int
    quoted: bool = False


@dataclass
class NodeSpec:
    attrs: dict[str, Attr]
    position: int
    end: int


@dataclass
class Group:
    base: NodeSpec
    children: list[NodeSpec]


@dataclass
class ConstantSpec:
    name: str
    value: str
    position: int


@dataclass
class ParsedDocument:
    groups: list[Group]
    constant_specs: list[ConstantSpec]


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

    def parse(self) -> ParsedDocument:
        groups: list[Group] = []
        constants: list[ConstantSpec] = []
        self.skip_space()
        while self.pos < len(self.source) and self.source[self.pos] == "@":
            constants.append(self.parse_constant())
            self.skip_space()
        while self.pos < len(self.source):
            if self.source[self.pos] != "[":
                raise self.error("常量声明只能位于文档开头；顶层 primitive 由 [ ] 包裹")
            groups.append(self.parse_group())
            self.skip_space()
        if not groups:
            raise self.error("文档为空", 0)
        return ParsedDocument(groups, constants)

    def parse_constant(self) -> ConstantSpec:
        start = self.pos
        self.pos += 1  # @
        match = re.match(r"[A-Za-z][A-Za-z0-9]*", self.source[self.pos:])
        if not match:
            raise self.error("常量名必须是字母开头的 ASCII 字母数字串", self.pos)
        name = match.group(0)
        self.pos += len(name)
        self.skip_space()
        if self.pos >= len(self.source) or self.source[self.pos] != "=":
            raise self.error(f"常量 {name} 后缺少 =", start)
        self.pos += 1
        self.skip_space()
        value_start = self.pos
        while self.pos < len(self.source) and not self.source[self.pos].isspace():
            self.pos += 1
        value = self.source[value_start:self.pos]
        if not value:
            raise self.error(f"常量 {name} 缺少值", value_start)
        while self.pos < len(self.source) and self.source[self.pos] in " \t":
            self.pos += 1
        if self.pos < len(self.source) and self.source[self.pos] not in "\r\n":
            raise self.error("常量声明每行只能有一个值", self.pos)
        return ConstantSpec(name, value, value_start)

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

    def parse_quoted_value(self) -> tuple[str, int]:
        opening = self.pos
        self.pos += 1
        decoded: list[str] = []
        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char == '"':
                self.pos += 1
                value_end = self.pos
                self.skip_space()
                if self.pos < len(self.source) and self.source[self.pos] not in ";|]":
                    raise self.error("闭合引号后只允许空白、;、| 或 ]", self.pos)
                return "".join(decoded), value_end
            if char in "\r\n":
                raise self.error("字符串不支持原始换行", self.pos)
            if char == "\\":
                escape_position = self.pos
                self.pos += 1
                if self.pos >= len(self.source):
                    raise self.error("未闭合的双引号字符串", opening)
                escaped = self.source[self.pos]
                if escaped not in {'"', "\\"}:
                    raise self.error("字符串只支持 \\\" 和 \\\\ 两种转义", escape_position)
                decoded.append(escaped)
                self.pos += 1
                continue
            decoded.append(char)
            self.pos += 1
        raise self.error("未闭合的双引号字符串", opening)

    def parse_segment(self) -> NodeSpec:
        segment_start = self.pos
        segment_end = self.pos
        attrs: dict[str, Attr] = {}
        while True:
            self.skip_space()
            key_start = self.pos
            match = re.match(r"[a-z][a-z0-9_]*", self.source[self.pos:])
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
            quoted = self.pos < len(self.source) and self.source[self.pos] == '"'
            if quoted:
                value, value_end = self.parse_quoted_value()
            else:
                while self.pos < len(self.source):
                    char = self.source[self.pos]
                    if char.isspace() or char in ";|]":
                        break
                    if char == "[":
                        raise self.error("当前版本不支持嵌套括号")
                    self.pos += 1
                value = self.source[value_start:self.pos]
                value_end = self.pos
                if not value:
                    raise self.error(f"属性 {name} 缺少值", value_start)
            if name in attrs:
                raise self.error(f"属性 {name} 重复", key_start)
            attrs[name] = Attr(name, value, value_start, quoted)
            segment_end = value_end
            self.skip_space()
            if self.pos < len(self.source) and self.source[self.pos] == ";":
                self.pos += 1
                segment_end = self.pos
                self.skip_space()
                if self.pos < len(self.source) and self.source[self.pos] in "|]":
                    break
                continue
            break
        return NodeSpec(attrs, segment_start, segment_end)


def parse(source: str) -> ParsedDocument:
    return Parser(source).parse()


def source_position(source: str, offset: int) -> dict[str, int]:
    offset = max(0, min(offset, len(source)))
    line, column = source_line_column(source, offset)
    return {"offset": offset, "line": line, "column": column}


def read_source_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as opened:
        return opened.read()


def make_code_location(source: str, start: int, end: int, source_path: Optional[str]) -> dict[str, object]:
    return {
        "file": source_path,
        "start": source_position(source, start),
        "end": source_position(source, end),
    }






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
DIMENSION_RE = re.compile(r"^(-?\d+(?:/\d+)?)(pw|ph|w|h|a|px)$")
CONSTANT_RE = re.compile(r"^(?:(-?\d+(?:/\d+)?)?)@([A-Za-z][A-Za-z0-9]*)$")
PARAMETER_RE = re.compile(r"^\$([a-z][a-z0-9_]*)$")
PARAMETER_TARGETS = {
    "text": "text",
    "color": "color",
    "fill": "color",
    "x": "dimension", "y": "dimension", "w": "dimension", "h": "dimension",
    "xo": "dimension", "yo": "dimension",
    "ll": "dimension", "lr": "dimension", "lt": "dimension", "lb": "dimension",
}
COMMON_ATTRS = {"x", "y", "w", "h", "xo", "yo", "anchor", "fill", "lining", "ll", "lr", "lt", "lb"}
TEXT_ATTRS = {"text", "font", "size", "color"}
IMAGE_ATTRS = {"image"}
REF_ATTRS = {"ref"}
MAX_REF_DEPTH = 32
MAX_EXPANDED_NODES = 10000


@dataclass(frozen=True)
class Dimension:
    unit: str
    scale: Fraction


def resolve_constants(specs: list[ConstantSpec], source: str) -> dict[str, Dimension]:
    constants: dict[str, Dimension] = {}
    for spec in specs:
        if spec.name in constants:
            raise LangError(f"常量 {spec.name} 重复声明", source, spec.position)
        direct = DIMENSION_RE.fullmatch(spec.value)
        if direct:
            number, unit = direct.groups()
            if "/" in number:
                numerator, denominator = number.split("/", 1)
                if int(denominator) == 0:
                    raise LangError("分数分母不能为零", source, spec.position)
                scale = Fraction(int(numerator), int(denominator))
            else:
                scale = Fraction(int(number)) if unit == "px" else Fraction(int(number), 100)
            constants[spec.name] = Dimension(unit, scale)
            continue
        reference = CONSTANT_RE.fullmatch(spec.value)
        if not reference:
            raise LangError("常量值必须是尺寸或 倍数@先前常量", source, spec.position)
        multiplier, name = reference.groups()
        if name not in constants:
            raise LangError(f"常量 {spec.name} 只能引用先前声明的常量；未找到 {name}", source, spec.position)
        if multiplier is None:
            factor = Fraction(1)
        elif "/" in multiplier:
            numerator, denominator = multiplier.split("/", 1)
            if int(denominator) == 0:
                raise LangError("分数分母不能为零", source, spec.position)
            factor = Fraction(int(numerator), int(denominator))
        else:
            factor = Fraction(int(multiplier))
        base = constants[name]
        constants[spec.name] = Dimension(base.unit, base.scale * factor)
    return constants


@dataclass(frozen=True)
class ParameterValue:
    kind: str
    value: object


def parameter_name(attr: Attr) -> Optional[str]:
    if attr.quoted:
        return None
    match = PARAMETER_RE.fullmatch(attr.value)
    return match.group(1) if match else None


def iter_node_specs(document: ParsedDocument):
    for group in document.groups:
        yield group.base
        yield from group.children


def infer_parameters(document: ParsedDocument, source: str) -> dict[str, str]:
    inferred: dict[str, str] = {}
    reserved = COMMON_ATTRS | TEXT_ATTRS | IMAGE_ATTRS | REF_ATTRS
    for spec in iter_node_specs(document):
        for attr in spec.attrs.values():
            name = parameter_name(attr)
            if name is None:
                continue
            kind = PARAMETER_TARGETS.get(attr.name)
            if kind is None:
                raise LangError(f"参数 ${name} 只能用于受支持的 text、color、fill 或几何属性；不允许嵌套转发", source, attr.position)
            if name in reserved:
                raise LangError(f"参数名 {name} 与内置属性冲突", source, attr.position)
            previous = inferred.get(name)
            if previous is not None and previous != kind:
                raise LangError(f"参数 {name} 同时被当作 {previous} 和 {kind} 使用", source, attr.position)
            inferred[name] = kind
    return inferred


def dimension_text(dimension: Dimension) -> str:
    numerator, denominator = dimension.scale.numerator, dimension.scale.denominator
    if dimension.unit == "px" and denominator == 1:
        return f"{numerator}px"
    return f"{numerator}/{denominator}{dimension.unit}"


def substitute_document(document: ParsedDocument, values: dict[str, ParameterValue]) -> ParsedDocument:
    def substitute(spec: NodeSpec) -> NodeSpec:
        attrs: dict[str, Attr] = {}
        for name, attr in spec.attrs.items():
            placeholder = parameter_name(attr)
            if placeholder is None:
                attrs[name] = attr
                continue
            value = values[placeholder]
            rendered = value.value if value.kind in {"text", "color"} else dimension_text(value.value)
            attrs[name] = Attr(name, rendered, attr.position, True)
        return NodeSpec(attrs, spec.position, spec.end)

    groups = [Group(substitute(group.base), [substitute(child) for child in group.children]) for group in document.groups]
    return ParsedDocument(groups, document.constant_specs)


def bind_parameters(document: ParsedDocument, attrs: dict[str, Attr], source: str, constants: dict[str, Dimension], ref_attr: Attr, definition_source: str, expected: Optional[dict[str, str]] = None) -> tuple[dict[str, ParameterValue], ParsedDocument]:
    expected = infer_parameters(document, definition_source) if expected is None else expected
    reserved = COMMON_ATTRS | TEXT_ATTRS | IMAGE_ATTRS | REF_ATTRS
    for attr in attrs.values():
        if attr.name in reserved:
            continue
        if attr.name not in expected:
            raise LangError(f"未知 ref 参数 {attr.name}；组件需要：{sorted(expected)}", source, attr.position)
    missing = set(expected) - set(attrs)
    if missing:
        raise LangError(f"ref 缺少参数：{sorted(missing)}", source, ref_attr.position)
    values: dict[str, ParameterValue] = {}
    for name, kind in expected.items():
        attr = attrs[name]
        if kind == "text":
            values[name] = ParameterValue(kind, attr.value)
        elif kind == "color":
            values[name] = ParameterValue(kind, validate_color(attr, source))
        else:
            values[name] = ParameterValue(kind, parse_dimension(attr, source, {"pw", "ph", "w", "h", "a", "px"}, constants))
    return values, substitute_document(document, values)


ANCHOR_ALIASES = {
    "left": "null-left",
    "right": "null-right",
    "top": "top-null",
    "bottom": "bottom-null",
    "center": "center-center",
}


def desugar_anchor(value: str) -> dict[str, Dimension]:
    """把固定 anchor 糖展开为后端位置属性，不读取任何场景状态。"""
    value = ANCHOR_ALIASES.get(value, value)
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError(f"非法 anchor {value!r}；应为 vertical-horizontal 或受支持的短形式")
    vertical, horizontal = parts
    if vertical not in {"null", "top", "center", "bottom"} or horizontal not in {"null", "left", "center", "right"}:
        raise ValueError(f"非法 anchor {value!r}；垂直轴为 null/top/center/bottom，水平轴为 null/left/center/right")

    dimensions: dict[str, Dimension] = {}
    if horizontal == "left":
        dimensions.update(x=Dimension("pw", Fraction(0)), xo=Dimension("w", Fraction(0)))
    elif horizontal == "center":
        dimensions.update(x=Dimension("pw", Fraction(1, 2)), xo=Dimension("w", Fraction(-1, 2)))
    elif horizontal == "right":
        dimensions.update(x=Dimension("pw", Fraction(1)), xo=Dimension("w", Fraction(-1)))
    if vertical == "top":
        dimensions.update(y=Dimension("ph", Fraction(0)), yo=Dimension("h", Fraction(0)))
    elif vertical == "center":
        dimensions.update(y=Dimension("ph", Fraction(1, 2)), yo=Dimension("h", Fraction(-1, 2)))
    elif vertical == "bottom":
        dimensions.update(y=Dimension("ph", Fraction(1)), yo=Dimension("h", Fraction(-1)))
    return dimensions


def desugar_lining(value: str) -> dict[str, str]:
    """把 1/2/4 值 lining 展开为四边属性，不读取场景状态。"""
    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise ValueError("lining 的逗号之间必须有尺寸")
    if len(parts) == 1:
        values = dict(zip(("lt", "lr", "lb", "ll"), parts * 4))
    elif len(parts) == 2:
        vertical, horizontal = parts
        values = {"lt": vertical, "lr": horizontal, "lb": vertical, "ll": horizontal}
    elif len(parts) == 4:
        values = dict(zip(("lt", "lr", "lb", "ll"), parts))
    else:
        raise ValueError("lining 只接受 1、2 或 4 个逗号分隔尺寸；顺序为上、右、下、左")
    return values


@dataclass
class Node:
    rect: object
    parent_rect: object
    path: str
    fill: Optional[str]
    kind: str
    text: Optional[str] = None
    resource: Optional[str] = None
    png: Optional[bytes] = None
    intrinsic: Optional[tuple[int, int]] = None
    metadata: dict[str, object] = field(default_factory=dict)
    code: dict[str, object] = field(default_factory=dict)
    child_rect: object = None
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
    cache: dict[Path, tuple[str, ParsedDocument]] = field(default_factory=dict)
    stack: list[Path] = field(default_factory=list)
    created_nodes: int = 0


def parse_dimension(attr, source: str, allowed: set[str], constants: dict[str, Dimension]) -> Dimension:
    match = DIMENSION_RE.fullmatch(attr.value)
    if match:
        number, unit = match.groups()
        if "/" in number:
            numerator, denominator = number.split("/", 1)
            if int(denominator) == 0:
                raise LangError("分数分母不能为零", source, attr.position)
            scale = Fraction(int(numerator), int(denominator))
        else:
            scale = Fraction(int(number)) if unit == "px" else Fraction(int(number), 100)
        dimension = Dimension(unit, scale)
    else:
        reference = CONSTANT_RE.fullmatch(attr.value)
        if not reference:
            raise LangError(f"{attr.name} 必须使用 数字+单位或 常量引用，例如 100pw、24px、1/12ph、@margin；收到 {attr.value!r}", source, attr.position)
        multiplier, name = reference.groups()
        if name not in constants:
            raise LangError(f"未找到当前文件常量 {name}", source, attr.position)
        base = constants[name]
        if multiplier is None:
            factor = Fraction(1)
        elif "/" in multiplier:
            numerator, denominator = multiplier.split("/", 1)
            if int(denominator) == 0:
                raise LangError("分数分母不能为零", source, attr.position)
            factor = Fraction(int(numerator), int(denominator))
        else:
            factor = Fraction(int(multiplier))
        dimension = Dimension(base.unit, base.scale * factor)
    if dimension.unit not in allowed:
        raise LangError(f"{attr.name} 不允许单位 {dimension.unit}；允许：{sorted(allowed)}", source, attr.position)
    return dimension


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


def rasterize_text(text: str, font_path: str, size: int, color: str, resource_dir: Path) -> tuple[bytes, tuple[int, int], dict[str, object]]:
    if font_path == "default":
        path = resolve_default_font()
    else:
        font_file = Path(font_path)
        if not font_file.is_absolute():
            font_file = resource_dir / font_file
        path = str(font_file.resolve())
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
    windows_absolute = re.match(r"^[A-Za-z]:[\\/]", source_name)
    uri_scheme = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source_name)
    if uri_scheme and not windows_absolute:
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


def validate_common(spec, source: str, kind: str, constants: dict[str, Dimension], parameters: Optional[set[str]] = None) -> dict[str, object]:
    attrs = spec.attrs
    type_attrs = TEXT_ATTRS if kind == "text" else IMAGE_ATTRS if kind == "image" else REF_ATTRS if kind == "ref" else set()
    allowed = COMMON_ATTRS | type_attrs | (parameters or set())
    for attr in attrs.values():
        if attr.name not in allowed:
            raise LangError(f"未知属性 {attr.name}，或当前 primitive 类型不允许该属性", source, attr.position)
    fill = validate_color(attrs["fill"], source) if "fill" in attrs else None
    dimensions: dict[str, Dimension] = {}
    if "anchor" in attrs:
        anchor = attrs["anchor"]
        try:
            expanded = desugar_anchor(anchor.value)
        except ValueError as error:
            raise LangError(str(error), source, anchor.position) from error
        conflicts = set(expanded) & set(attrs)
        if conflicts:
            raise LangError(f"anchor 展开属性与手写属性冲突：{sorted(conflicts)}", source, anchor.position)
        dimensions.update(expanded)
    for name in ("x", "xo"):
        if name in attrs:
            dimensions[name] = parse_dimension(attrs[name], source, {"pw", "px"} if name == "x" else {"pw", "w", "px"}, constants)
    for name in ("y", "yo"):
        if name in attrs:
            dimensions[name] = parse_dimension(attrs[name], source, {"ph", "px"} if name == "y" else {"ph", "h", "px"}, constants)
    has_intrinsic = kind in {"text", "image"}
    width_units = {"pw", "a", "px"} if has_intrinsic else {"pw", "px"}
    height_units = {"ph", "a", "px"} if has_intrinsic else {"ph", "px"}
    if has_intrinsic:
        dimensions["w"] = parse_dimension(attrs["w"], source, width_units, constants) if "w" in attrs else Dimension("a", Fraction(1))
        dimensions["h"] = parse_dimension(attrs["h"], source, height_units, constants) if "h" in attrs else Dimension("a", Fraction(1))
    else:
        for required in ("w", "h"):
            if required not in attrs:
                label = "ref" if kind == "ref" else "普通"
                suffix = "；ref 没有 auto 尺寸" if kind == "ref" else ""
                raise LangError(f"{label} primitive 缺少 {required}{suffix}", source, spec.position)
        dimensions["w"] = parse_dimension(attrs["w"], source, width_units, constants)
        dimensions["h"] = parse_dimension(attrs["h"], source, height_units, constants)
    lining_values: dict[str, str] = {}
    if "lining" in attrs:
        lining = attrs["lining"]
        try:
            expanded = desugar_lining(lining.value)
        except ValueError as error:
            raise LangError(str(error), source, lining.position) from error
        conflicts = set(expanded) & set(attrs)
        if conflicts:
            raise LangError(f"lining 展开属性与手写属性冲突：{sorted(conflicts)}", source, lining.position)
        lining_values.update(expanded)
    for name in ("ll", "lr", "lt", "lb"):
        if name in attrs:
            lining_values[name] = attrs[name].value
    lining: dict[str, Dimension] = {}
    for name, value in lining_values.items():
        allowed_units = {"pw", "w", "px"} if name in {"ll", "lr"} else {"ph", "h", "px"}
        lining[name] = parse_dimension(Attr(name, value, attrs[name].position if name in attrs else attrs["lining"].position), source, allowed_units, constants)
    return {"fill": fill, "dimensions": dimensions, "lining": lining}


def _evaluate_node(spec, parent, path: str, source: str, constants: dict[str, Dimension], context: RefContext) -> Node:
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
    current_dir = context.stack[-1].parent if context.stack else context.resource_dir
    current_source = str(context.stack[-1]) if context.stack else None
    code = make_code_location(source, spec.position, spec.end, current_source)
    parameter_names: set[str] = set()
    parameter_values: dict[str, ParameterValue] = {}
    ref_document = None
    definition_source = None
    definition_constants: dict[str, Dimension] = {}
    target = None
    if kind != "ref":
        for attr in spec.attrs.values():
            placeholder = parameter_name(attr)
            if placeholder is not None:
                raise LangError(f"参数 ${placeholder} 只能在 ref 定义实例化时使用", source, attr.position)
    else:
        ref_name = spec.attrs["ref"].value
        if not ref_name or ref_name in {".", ".."} or "/" in ref_name or "\\" in ref_name or "@" in ref_name or ref_name.endswith(".txt"):
            raise LangError("ref 只接受不带路径、@ 和 .txt 后缀的定义名", source, spec.attrs["ref"].position)
        target = (current_dir / f"{ref_name}.txt").resolve()
        if target in context.stack:
            chain = " → ".join(path.name for path in [*context.stack, target])
            raise ValueError(f"ref 引用循环：{chain}")
        if len(context.stack) >= MAX_REF_DEPTH:
            raise ValueError(f"ref 引用深度超过上限 {MAX_REF_DEPTH}")
        if target not in context.cache:
            try:
                definition_source = read_source_text(target)
            except OSError as error:
                raise EvaluationError(f"无法读取 ref 定义 {str(target)!r}：{error}", code, path) from error
            try:
                document = parse(definition_source)
                resolve_constants(document.constant_specs, definition_source)
            except LangError as error:
                error.source_path = str(target)
                raise
            context.cache[target] = (definition_source, document)
        definition_source, document = context.cache[target]
        try:
            expected = infer_parameters(document, definition_source)
        except LangError as error:
            error.source_path = str(target)
            raise
        parameter_names = set(expected)
        parameter_values, ref_document = bind_parameters(document, spec.attrs, source, constants, spec.attrs["ref"], definition_source, expected)
        definition_constants = resolve_constants(document.constant_specs, definition_source)
    common = validate_common(spec, source, kind, constants, parameter_names)
    dimensions = common["dimensions"]
    lining = common["lining"]

    text = None
    source_name = None
    png = None
    intrinsic = None
    metadata: dict[str, object] = {}
    if kind == "text":
        size = 40
        if "size" in spec.attrs:
            size_attr = spec.attrs["size"]
            if not re.fullmatch(r"\d+", size_attr.value) or int(size_attr.value) <= 0:
                raise LangError("size 必须是正整数像素", source, size_attr.position)
            size = int(size_attr.value)
        foreground = validate_color(spec.attrs["color"], source) if "color" in spec.attrs else "#000000"
        font_path = spec.attrs["font"].value if "font" in spec.attrs else "default"
        text = spec.attrs["text"].value
        try:
            png, intrinsic, metadata = rasterize_text(text, font_path, size, foreground, current_dir)
        except ValueError as error:
            raise EvaluationError(str(error), code, path) from error
    elif kind == "image":
        source_name = spec.attrs["image"].value
        try:
            png, intrinsic, metadata, source_name = load_image(source_name, current_dir)
        except ValueError as error:
            raise EvaluationError(str(error), code, path) from error

    def basis(dimension: Dimension, own_w: Fraction, own_h: Fraction) -> Fraction:
        if dimension.unit == "px":
            return Fraction(1)
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
    left = lining["ll"].scale * basis(lining["ll"], width, height) if "ll" in lining else Fraction(0)
    right = lining["lr"].scale * basis(lining["lr"], width, height) if "lr" in lining else Fraction(0)
    top = lining["lt"].scale * basis(lining["lt"], width, height) if "lt" in lining else Fraction(0)
    bottom = lining["lb"].scale * basis(lining["lb"], width, height) if "lb" in lining else Fraction(0)
    child_rect = Rect(x + left, y + top, width - left - right, height - top - bottom)
    if child_rect.w <= 0 or child_rect.h <= 0:
        raise LangError("lining 修正后的 parent_rect 宽高必须大于零", source, spec.attrs["lining"].position if "lining" in spec.attrs else spec.position)
    node = Node(rect=rect, parent_rect=parent, path=path, fill=common["fill"], kind=kind, text=text, resource=source_name, png=png, intrinsic=intrinsic, metadata=metadata, code=code, child_rect=child_rect)
    if kind == "ref":
        ref_name = spec.attrs["ref"].value
        assert ref_document is not None and definition_source is not None and target is not None
        context.stack.append(target)
        try:
            node.expansion = evaluate_groups(ref_document.groups, child_rect, f"{path}@{ref_name}", definition_source, definition_constants, context)
        finally:
            context.stack.pop()
        node.resource = str(target)
        node.metadata = {"ref_path": str(target), "expanded_roots": len(node.expansion)}
        if parameter_values:
            node.metadata["parameters"] = {
                name: {"type": value.kind, "value": value.value if value.kind != "dimension" else dimension_text(value.value)}
                for name, value in parameter_values.items()
            }
    return node


def evaluate_node(spec, parent, path: str, source: str, constants: dict[str, Dimension], context: RefContext) -> Node:
    try:
        return _evaluate_node(spec, parent, path, source, constants, context)
    except LangError as error:
        if error.source_path is None and context.stack:
            error.source_path = str(context.stack[-1])
        raise


def evaluate_groups(groups: list[Group], parent: Rect, prefix: str, source: str, constants: dict[str, Dimension], context: RefContext) -> list[Node]:
    roots: list[Node] = []
    for root_index, group in enumerate(groups):
        root_path = f"{prefix}/{root_index}" if prefix else str(root_index)
        root = evaluate_node(group.base, parent, root_path, source, constants, context)
        root.children = [
            evaluate_node(child, root.child_rect, f"{root_path}/{index}", source, constants, context)
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
    try:
        document = parse(source)
        constants = resolve_constants(document.constant_specs, source)
    except LangError as error:
        if source_path is not None:
            error.source_path = str(source_path.resolve())
        raise
    roots = evaluate_groups(document.groups, canvas, "", source, constants, context)
    return Scene(width, height, roots, flatten(roots))


def render_svg(scene: Scene) -> str:
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{scene.width}" height="{scene.height}" viewBox="0 0 {scene.width} {scene.height}">',
        "  <!-- Layerlang v0.8: deterministic layers; content is scaled without preserving aspect ratio. -->",
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
        scale = None
        if node.intrinsic:
            scale = [
                number(node.rect.w / Fraction(node.intrinsic[0])),
                number(node.rect.h / Fraction(node.intrinsic[1])),
            ]
        layers.append({
            "index": index,
            "path": node.path,
            "kind": node.kind,
            "fill": node.fill,
            "text": node.text,
            "resource": node.resource,
            "intrinsic": list(node.intrinsic) if node.intrinsic else None,
            "content_metadata": node.metadata or None,
            "code": node.code,
            "scale": scale,
            "rect_px": {key: number(value) for key, value in {
                "x": node.rect.x, "y": node.rect.y, "w": node.rect.w, "h": node.rect.h,
            }.items()},
            "rect_exact": {key: exact(value) for key, value in {
                "x": node.rect.x, "y": node.rect.y, "w": node.rect.w, "h": node.rect.h,
            }.items()},
            "parent_rect": {key: exact(value) for key, value in {
                "x": node.parent_rect.x, "y": node.parent_rect.y, "w": node.parent_rect.w, "h": node.parent_rect.h,
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
        scene = build_scene("[w=100pw;h=100ph;fill=#000000]", 1200, 630)
        self.assertEqual(len(scene.layers), 1)
        self.assertEqual(scene.layers[0].rect, Rect(Fraction(0), Fraction(0), Fraction(1200), Fraction(630)))
        svg = render_svg(scene)
        self.assertIn('fill="#000000"', svg)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

    def test_02_siblings_share_parent_and_draw_left_to_right(self) -> None:
        source = "[w=100pw;h=100ph|w=1/2pw;h=100ph;fill=#ff0000|x=1/4pw;w=1/2pw;h=100ph;fill=#0000ff]"
        scene = build_scene(source)
        self.assertEqual([node.path for node in scene.layers], ["0", "0/0", "0/1"])
        self.assertEqual(scene.layers[1].rect.x, 0)
        self.assertEqual(scene.layers[2].rect.x, 300)
        svg = render_svg(scene)
        self.assertLess(svg.index("#ff0000"), svg.index("#0000ff"))

    def test_03_own_size_offsets_center_a_block(self) -> None:
        source = "[w=100pw;h=100ph|x=1/2pw;y=1/2ph;w=1/4pw;h=1/4ph;xo=-1/2w;yo=-1/2h;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[1].rect, Rect(Fraction(450), Fraction(450), Fraction(300), Fraction(300)))

    def test_04_children_are_not_clipped_by_parent(self) -> None:
        source = "[x=1/4pw;y=1/4ph;w=1/4pw;h=1/4ph|x=1/1pw;w=1/2pw;h=1/2ph;fill=#ff0000]"
        scene = build_scene(source)
        child = scene.layers[1]
        self.assertEqual(child.rect, Rect(Fraction(600), Fraction(300), Fraction(150), Fraction(150)))
        self.assertEqual(outside_status(child.rect, scene.canvas_rect), "inside")
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_05_fully_outside_layer_is_reported(self) -> None:
        source = "[w=100pw;h=100ph|x=2/1pw;w=1/4pw;h=1/4ph;fill=#ff0000]"
        scene = build_scene(source)
        report = scene_report(scene)
        self.assertEqual(report["outside"], {"partial": [], "full": ["0/0"]})
        self.assertIn('x="2400"', render_svg(scene))

    def test_fraction_and_percent_have_distinct_exact_rules(self) -> None:
        scene = build_scene(
            "[w=100pw;h=100ph|w=1/12pw;h=100ph;fill=#ff0000|w=1pw;h=1/2ph;fill=#000000|w=50pw;h=100ph;fill=#ffffff]"
        )
        self.assertEqual(scene.layers[0].rect.w, 1200)
        self.assertEqual(scene.layers[1].rect.w, 100)
        self.assertEqual(scene.layers[2].rect.w, 12)
        self.assertEqual(scene.layers[2].rect.h, 600)
        self.assertEqual(scene.layers[3].rect.w, 600)

    def test_parent_final_offset_rect_is_child_reference(self) -> None:
        source = "[x=1/4pw;w=1/2pw;h=100ph;xo=-1/12pw|w=100pw;h=100ph;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[0].rect.x, 200)
        self.assertEqual(scene.layers[1].rect.x, 200)

    def test_hit_returns_bottom_to_top_including_containers(self) -> None:
        source = "[w=100pw;h=100ph|w=100pw;h=100ph;fill=#ffffff|x=1/4pw;y=1/4ph;w=1/2pw;h=1/2ph;fill=#000000]"
        scene = build_scene(source)
        self.assertEqual([node.path for node in hit(scene, 600, 600)], ["0", "0/0", "0/1"])
        self.assertEqual([node.path for node in hit(scene, 10, 10)], ["0", "0/0"])
        self.assertEqual(hit(scene, -1, 10), [])

    def test_prettify_does_not_change_semantics(self) -> None:
        compact = "[w=100pw;h=100ph|x=1/2pw;y=1/2ph;w=1/4pw;h=1/4ph;xo=-1/2w;yo=-1/2h;fill=#000000]"
        pretty = """[
            w=100pw; h=100ph
            |
            x=1/2pw; y=1/2ph;
            w=1/4pw; h=1/4ph;
            xo=-1/2w; yo=-1/2h;
            fill=#000000;
        ]"""
        self.assertEqual(render_svg(build_scene(compact)), render_svg(build_scene(pretty)))

    def test_multiple_roots_keep_document_order(self) -> None:
        source = "[w=100pw;h=100ph;fill=#ffffff][w=1/2pw;h=100ph;fill=#000000]"
        svg = render_svg(build_scene(source))
        self.assertLess(svg.index("#ffffff"), svg.index("#000000"))

    def test_rejects_bare_numbers(self) -> None:
        with self.assertRaisesRegex(LangError, "数字\\+单位"):
            build_scene("[w=100;h=100ph]")

    def test_rejects_all_legacy_prefix_units(self) -> None:
        legacy_sources = [
            "[x=pw100;w=100pw;h=100ph]",
            "[x=pw1/2;w=100pw;h=100ph]",
            "[y=ph100;w=100pw;h=100ph]",
            "[xo=w-1/2;w=100pw;h=100ph]",
            "[yo=h-1/2;w=100pw;h=100ph]",
            "[text=测试;w=a100]",
        ]
        for source in legacy_sources:
            with self.subTest(source=source):
                with self.assertRaisesRegex(LangError, "数字\\+单位"):
                    build_scene(source)

    def test_rejects_current_size_unit_in_width(self) -> None:
        with self.assertRaisesRegex(LangError, "不允许单位 w"):
            build_scene("[w=100w;h=100ph]")

    def test_rejects_unknown_duplicate_and_nonpositive(self) -> None:
        with self.assertRaisesRegex(LangError, "未知属性"):
            build_scene("[w=100pw;h=100ph;aspect=1]")
        with self.assertRaisesRegex(LangError, "重复"):
            build_scene("[w=100pw;w=50pw;h=100ph]")
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[w=0pw;h=100ph]")

    def test_rejects_bad_structure_and_color(self) -> None:
        with self.assertRaisesRegex(LangError, "空的"):
            build_scene("[w=100pw;h=100ph|]")
        with self.assertRaisesRegex(LangError, "六位"):
            build_scene("[w=100pw;h=100ph;fill=#000]")
        with self.assertRaisesRegex(LangError, "嵌套括号"):
            build_scene("[w=100pw;h=100ph[w=50pw;h=50ph]]")



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
        scene = build_scene("[w=100pw;h=100ph|text=测试;size=40;x=1/2pw;y=1/2ph;xo=-1/2w;yo=-1/2h]")
        node = scene.layers[1]
        self.assertEqual(node.rect.x + node.rect.w / 2, 600)
        self.assertEqual(node.rect.y + node.rect.h / 2, 600)

    def test_auto_scaling_is_explicitly_nonuniform(self) -> None:
        scene = build_scene("[text=测试;size=40;w=50a;h=100a]")
        node = scene.layers[0]
        assert node.intrinsic is not None
        self.assertEqual(node.rect.w, Fraction(node.intrinsic[0], 2))
        self.assertEqual(node.rect.h, node.intrinsic[1])
        self.assertIn('preserveAspectRatio="none"', render_svg(scene))

    def test_parent_dimensions_can_stretch_text_bitmap(self) -> None:
        scene = build_scene("[text=测试;size=40;w=50pw;h=1/4ph]")
        self.assertEqual(scene.layers[0].rect.w, 600)
        self.assertEqual(scene.layers[0].rect.h, 300)

    def test_fill_precedes_text_content(self) -> None:
        svg = render_svg(build_scene("[text=测试;size=40;fill=#f2c94c;color=#202020]"))
        self.assertLess(svg.index("<rect"), svg.index("<image"))
        self.assertIn('fill="#f2c94c"', svg)
        report = scene_report(build_scene("[text=测试;size=40;color=#202020]"))
        self.assertEqual(report["layers"][0]["content_metadata"]["foreground"], "#202020")

    def test_text_can_be_parent_without_clipping_children(self) -> None:
        scene = build_scene("[text=父;size=40|x=2/1pw;w=100pw;h=100ph;fill=#ff0000]")
        child = scene.layers[1]
        self.assertGreater(child.rect.x, scene.layers[0].rect.right)
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_rejects_misplaced_type_head_and_plain_auto(self) -> None:
        with self.assertRaisesRegex(LangError, "类型头"):
            build_scene("[size=40;text=测试]")
        with self.assertRaisesRegex(LangError, "不允许单位 a"):
            build_scene("[w=100a;h=100ph]")

    def test_default_size_is_forty_pixels(self) -> None:
        node = build_scene("[text=测试]").layers[0]
        self.assertEqual(node.metadata["size_px"], 40)

    def test_rejects_zero_and_negative_size(self) -> None:
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[text=测试;size=40;w=0a]")
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[text=测试;size=40;w=-50a]")
        with self.assertRaisesRegex(LangError, "正整数"):
            build_scene("[text=测试;size=0]")

    def test_rejects_spaces_in_literals(self) -> None:
        with self.assertRaises(LangError):
            build_scene("[text=hello world;size=40]")

    def test_hit_uses_scaled_rect(self) -> None:
        scene = build_scene("[w=100pw;h=100ph|text=测试;size=40;x=1/2pw;y=1/2ph;xo=-1/2w;yo=-1/2h]")
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

    def test_explicit_font_is_relative_to_definition_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            copied = directory / "Noto Sans Test.ttf"
            copied.write_bytes(Path(resolve_default_font()).read_bytes())
            node = build_scene(
                '[text=测试;font="Noto Sans Test.ttf"]',
                resource_dir=directory,
            ).layers[0]
            self.assertEqual(node.metadata["font_path"], str(copied.resolve()))


class QuoteTests(unittest.TestCase):
    def test_quoted_text_allows_spaces_delimiters_and_brackets(self) -> None:
        node = build_scene('[text="hello world;|[draft]"]').layers[0]
        self.assertEqual(node.text, "hello world;|[draft]")

    def test_quoted_escapes_are_decoded_once(self) -> None:
        node = build_scene(r'[text="say \"hi\" \\ ok"]').layers[0]
        self.assertEqual(node.text, 'say "hi" \\ ok')
        plain = build_scene('[text=abc"def]').layers[0]
        self.assertEqual(plain.text, 'abc"def')

    def test_quoted_resource_path_can_contain_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            Image.new("RGBA", (3, 2), (255, 0, 0, 255)).save(directory / "pixel red.png")
            node = build_scene('[image="pixel red.png"]', resource_dir=directory).layers[0]
            self.assertEqual(node.intrinsic, (3, 2))

    def test_unclosed_quote_reports_opening_line_and_column(self) -> None:
        with self.assertRaises(LangError) as caught:
            build_scene('\n[text="hello]')
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(caught.exception.column, 7)
        self.assertIn("未闭合", caught.exception.message)

    def test_invalid_escape_trailing_text_and_raw_newline_are_rejected(self) -> None:
        with self.assertRaisesRegex(LangError, "两种转义"):
            build_scene(r'[text="bad\q";size=40]')
        with self.assertRaisesRegex(LangError, "闭合引号后"):
            build_scene('[text="ok"tail;size=40]')
        with self.assertRaisesRegex(LangError, "原始换行"):
            build_scene('[text="two\nlines"]')


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
        scaled = self.build("[image=sample.png;w=50a;h=200a]").layers[0]
        self.assertEqual((scaled.rect.w, scaled.rect.h), (20, 40))
        stretched = self.build("[image=sample.png;w=50pw;h=1/4ph]").layers[0]
        self.assertEqual((stretched.rect.w, stretched.rect.h), (600, 300))

    def test_image_alpha_survives_png_normalization(self) -> None:
        node = self.build("[image=sample.png]").layers[0]
        assert node.png is not None
        with Image.open(io.BytesIO(node.png)) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((12, 8)), (255, 0, 0, 255))

    def test_fill_precedes_image_and_image_can_be_parent(self) -> None:
        scene = self.build("[image=sample.png;fill=#f2c94c|x=2/1pw;w=100pw;h=100ph;fill=#ff0000]")
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
            self.build("[w=100pw;image=sample.png]")

    def test_windows_absolute_paths_are_not_misclassified_as_urls(self) -> None:
        for source_name in (
            "C:/definitely-missing-layerlang-image.png",
            r"C:\definitely-missing-layerlang-image.png",
        ):
            with self.subTest(source_name=source_name):
                with self.assertRaisesRegex(ValueError, "无法读取图片"):
                    load_image(source_name, self.directory)
        for source_name in ("C:relative.png", "file:///C:/sample.png"):
            with self.subTest(source_name=source_name):
                with self.assertRaisesRegex(ValueError, "只接受本地文件"):
                    load_image(source_name, self.directory)



class RefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.write("card", "[w=100pw;h=100ph;fill=#ffffff|x=1/4pw;y=1/4ph;w=1/2pw;h=1/2ph;fill=#ff0000]")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, source: str) -> None:
        (self.directory / f"{name}.txt").write_text(source, encoding="utf-8")

    def build(self, source: str) -> Scene:
        main = self.directory / "main.txt"
        main.write_text(source, encoding="utf-8")
        return build_scene(source, resource_dir=self.directory, source_path=main)

    def test_basic_ref_expands_into_instance_rect(self) -> None:
        scene = self.build("[ref=card;w=1/2pw;h=1/4ph]")
        self.assertEqual([node.path for node in scene.layers], ["0", "0@card/0", "0@card/0/0"])
        self.assertEqual(scene.layers[0].rect, Rect(Fraction(0), Fraction(0), Fraction(600), Fraction(300)))
        self.assertEqual(scene.layers[1].rect, scene.layers[0].rect)
        self.assertEqual(scene.layers[2].rect.x, 150)
        self.assertEqual(scene.layers[2].rect.y, 75)

    def test_repeated_refs_have_independent_instances(self) -> None:
        scene = self.build("[w=100pw;h=100ph|ref=card;w=1/2pw;h=100ph|ref=card;x=1/2pw;w=1/2pw;h=100ph]")
        centers = [node.path for node in scene.layers]
        self.assertIn("0/0@card/0/0", centers)
        self.assertIn("0/1@card/0/0", centers)
        first = next(node for node in scene.layers if node.path == "0/0@card/0/0")
        second = next(node for node in scene.layers if node.path == "0/1@card/0/0")
        self.assertEqual(first.rect.x, 150)
        self.assertEqual(second.rect.x, 750)

    def test_nested_refs_and_content_order(self) -> None:
        self.write("badge", "[w=100pw;h=100ph;fill=#0000ff]")
        self.write("outer", "[ref=badge;w=100pw;h=100ph]")
        scene = self.build("[ref=outer;w=100pw;h=100ph;fill=#f2c94c|w=100pw;h=100ph;fill=#202020]")
        self.assertEqual(
            [node.path for node in scene.layers],
            ["0", "0@outer/0", "0@outer/0@badge/0", "0/0"],
        )

    def test_ref_can_contain_text_and_be_parent(self) -> None:
        self.write("label", "[text=引用;size=40]")
        scene = self.build("[ref=label;w=100pw;h=100ph|x=2/1pw;w=100pw;h=100ph;fill=#ff0000]")
        self.assertEqual(scene.layers[1].kind, "text")
        self.assertGreater(scene.layers[2].rect.x, scene.layers[0].rect.right)
        self.assertNotIn("<clipPath", render_svg(scene))

    def test_direct_and_indirect_cycles_are_rejected(self) -> None:
        self.write("a", "[ref=a;w=100pw;h=100ph]")
        with self.assertRaisesRegex(ValueError, "引用循环"):
            self.build("[ref=a;w=100pw;h=100ph]")
        self.write("a", "[ref=b;w=100pw;h=100ph]")
        self.write("b", "[ref=a;w=100pw;h=100ph]")
        with self.assertRaisesRegex(ValueError, "引用循环"):
            self.build("[ref=a;w=100pw;h=100ph]")

    def test_missing_auto_misplaced_and_path_refs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "无法读取 ref"):
            self.build("[ref=missing;w=100pw;h=100ph]")
        with self.assertRaisesRegex(LangError, "不允许单位 a"):
            self.build("[ref=card;w=100a;h=100ph]")
        with self.assertRaisesRegex(LangError, "类型头"):
            self.build("[w=100pw;ref=card;h=100ph]")
        with self.assertRaisesRegex(LangError, "定义名"):
            self.build("[ref=../card;w=100pw;h=100ph]")
        with self.assertRaisesRegex(LangError, "定义名"):
            self.build("[ref=a@b;w=100pw;h=100ph]")

    def test_depth_guard(self) -> None:
        global MAX_REF_DEPTH
        original = MAX_REF_DEPTH
        MAX_REF_DEPTH = 2
        try:
            self.write("a", "[ref=b;w=100pw;h=100ph]")
            self.write("b", "[w=100pw;h=100ph]")
            with self.assertRaisesRegex(ValueError, "深度超过上限"):
                self.build("[ref=a;w=100pw;h=100ph]")
        finally:
            MAX_REF_DEPTH = original

    def test_expanded_node_guard(self) -> None:
        global MAX_EXPANDED_NODES
        original = MAX_EXPANDED_NODES
        MAX_EXPANDED_NODES = 2
        try:
            with self.assertRaisesRegex(ValueError, "节点数超过上限"):
                self.build("[ref=card;w=100pw;h=100ph]")
        finally:
            MAX_EXPANDED_NODES = original


class AnchorTests(unittest.TestCase):
    def test_all_sixteen_composites_produce_the_expected_rectangles(self) -> None:
        horizontal_x = {"null": 0, "left": 0, "center": 450, "right": 900}
        vertical_y = {"null": 0, "top": 0, "center": 450, "bottom": 900}
        for vertical in vertical_y:
            for horizontal in horizontal_x:
                value = f"{vertical}-{horizontal}"
                with self.subTest(anchor=value):
                    node = build_scene(f"[w=1/4pw;h=1/4ph;anchor={value}]").layers[0]
                    self.assertEqual(
                        node.rect,
                        Rect(
                            Fraction(horizontal_x[horizontal]),
                            Fraction(vertical_y[vertical]),
                            Fraction(300),
                            Fraction(300),
                        ),
                    )

    def test_short_aliases_match_their_full_forms(self) -> None:
        aliases = {
            "left": "null-left",
            "right": "null-right",
            "top": "top-null",
            "bottom": "bottom-null",
            "center": "center-center",
        }
        for short, full in aliases.items():
            with self.subTest(anchor=short):
                sugared = build_scene(f"[text=hello;anchor={short}]")
                explicit = build_scene(f"[text=hello;anchor={full}]")
                self.assertEqual(render_svg(sugared), render_svg(explicit))

    def test_unoccupied_axes_accept_manual_attributes_in_any_order(self) -> None:
        equivalent_sources = [
            "[w=1/4pw;h=1/4ph;anchor=right;y=1/2ph;yo=-1/2h]",
            "[y=1/2ph;anchor=right;yo=-1/2h;w=1/4pw;h=1/4ph]",
            "[w=1/4pw;h=1/4ph;anchor=center-right]",
        ]
        scenes = [build_scene(source) for source in equivalent_sources]
        self.assertEqual(render_svg(scenes[0]), render_svg(scenes[1]))
        self.assertEqual(render_svg(scenes[0]), render_svg(scenes[2]))

        horizontal_center = build_scene("[w=1/4pw;h=1/4ph;anchor=null-center;y=100ph;yo=-100h]")
        vertical_center = build_scene("[w=1/4pw;h=1/4ph;anchor=center-null;x=100pw;xo=-100w]")
        self.assertEqual(horizontal_center.layers[0].rect, Rect(Fraction(450), Fraction(900), Fraction(300), Fraction(300)))
        self.assertEqual(vertical_center.layers[0].rect, Rect(Fraction(900), Fraction(450), Fraction(300), Fraction(300)))

        manual = "[w=1/4pw;h=1/4ph;x=1/4pw;y=1/4ph;xo=10w;yo=10h]"
        noop = "[w=1/4pw;h=1/4ph;anchor=null-null;x=1/4pw;y=1/4ph;xo=10w;yo=10h]"
        self.assertEqual(render_svg(build_scene(manual)), render_svg(build_scene(noop)))

    def test_conflicts_follow_all_expanded_keys_including_zero_axes(self) -> None:
        invalid_sources = [
            "[w=100pw;h=100ph;anchor=right;x=1/2pw]",
            "[w=100pw;h=100ph;anchor=right;xo=10w]",
            "[w=100pw;h=100ph;anchor=top;y=1/2ph]",
            "[w=100pw;h=100ph;anchor=top;yo=10h]",
            "[w=100pw;h=100ph;anchor=left;x=0pw]",
            "[w=100pw;h=100ph;anchor=top-right;y=1/2ph]",
            "[w=100pw;h=100ph;anchor=null-center;x=1/2pw]",
            "[w=100pw;h=100ph;anchor=center-null;y=1/2ph]",
            "[text=hello;anchor=center;xo=10w]",
        ]
        for source in invalid_sources:
            with self.subTest(source=source):
                with self.assertRaisesRegex(LangError, "冲突"):
                    build_scene(source)

    def test_invalid_and_duplicate_anchors_are_rejected(self) -> None:
        for value in ("middle", "left-top", "right-center", "null", "center-left-right"):
            with self.subTest(anchor=value):
                with self.assertRaisesRegex(LangError, "非法 anchor"):
                    build_scene(f"[text=hello;anchor={value}]")
        with self.assertRaisesRegex(LangError, "重复"):
            build_scene("[text=hello;anchor=center;anchor=right]")


class ReportTests(unittest.TestCase):
    def test_code_locations_point_to_main_and_ref_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.txt"
            source = '[text="hello world";anchor=center]'
            main.write_text(source, encoding="utf-8")
            report = scene_report(build_scene(source, source_path=main))
            span = report["layers"][0]["code"]
            self.assertEqual(span["file"], str(main.resolve()))
            self.assertEqual(span["start"], {"offset": 1, "line": 1, "column": 2})
            self.assertEqual(span["end"]["offset"], len(source) - 1)

            card_source = '[w=100pw;h=100ph|text="组件"]'
            card = directory / "card.txt"
            card.write_text(card_source, encoding="utf-8")
            main_source = "[ref=card;w=1/2pw;h=100ph][ref=card;x=1/2pw;w=1/2pw;h=100ph]"
            main.write_text(main_source, encoding="utf-8")
            ref_report = scene_report(build_scene(main_source, source_path=main))
            internal = ref_report["layers"][2]
            repeated = ref_report["layers"][5]
            self.assertEqual(internal["path"], "0@card/0/0")
            self.assertEqual(repeated["path"], "1@card/0/0")
            self.assertEqual(ref_report["layers"][0]["resource"], str(card.resolve()))
            self.assertEqual(ref_report["layers"][3]["resource"], str(card.resolve()))
            self.assertIsNone(internal["resource"])
            self.assertEqual(internal["code"], repeated["code"])
            self.assertEqual(internal["code"]["file"], str(card.resolve()))
            self.assertEqual(
                internal["code"]["start"]["offset"],
                card_source.index("text"),
            )

    def test_resource_and_code_schema_covers_image_text_rect_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pixel = directory / "pixel.png"
            Image.new("RGBA", (2, 3), (255, 0, 0, 255)).save(pixel)
            main = directory / "main.txt"
            source = "[image=pixel.png][text=测试][w=100pw;h=100ph]"
            main.write_text(source, encoding="utf-8")
            report = scene_report(build_scene(source, resource_dir=directory, source_path=main))
            image_layer, text_layer, rect_layer = report["layers"]
            self.assertEqual(image_layer["resource"], str(pixel.resolve()))
            self.assertEqual(image_layer["content_metadata"]["source_path"], str(pixel.resolve()))
            self.assertIsNone(text_layer["resource"])
            self.assertIn("font_path", text_layer["content_metadata"])
            self.assertIsNone(rect_layer["resource"])
            for layer in report["layers"]:
                self.assertNotIn("source", layer)
                self.assertNotIn("source_span", layer)
                self.assertEqual(layer["code"]["file"], str(main.resolve()))

            memory_layer = scene_report(build_scene("[w=100pw;h=100ph]"))["layers"][0]
            self.assertIsNone(memory_layer["resource"])
            self.assertIsNone(memory_layer["code"]["file"])
            self.assertIn("offset", memory_layer["code"]["start"])

    def test_scale_reports_natural_uniform_and_nonuniform_states(self) -> None:
        report = scene_report(build_scene(
            "[text=测试][text=测试;w=50a;h=50a]"
            "[text=测试;w=50a;h=100a][w=100pw;h=100ph]"
        ))
        self.assertEqual(report["layers"][0]["scale"], [1, 1])
        self.assertEqual(report["layers"][1]["scale"], [0.5, 0.5])
        self.assertEqual(report["layers"][2]["scale"], [0.5, 1])
        self.assertIsNone(report["layers"][3]["scale"])

    def test_crlf_code_offsets_slice_original_main_and_ref_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.txt"
            card = directory / "card.txt"
            main_source = "\r\n[ref=card;w=100pw;h=100ph]\r\n"
            card_source = '\r\n[w=100pw;h=100ph;fill=#ffffff|\r\ntext=组件]\r\n'
            with main.open("w", encoding="utf-8", newline="") as opened:
                opened.write(main_source)
            with card.open("w", encoding="utf-8", newline="") as opened:
                opened.write(card_source)
            svg = directory / "out.svg"
            layers = directory / "out.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "layerlang_mvp.py"),
                    "render",
                    str(main),
                    "--svg",
                    str(svg),
                    "--layers",
                    str(layers),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", errors="replace"),
            )
            report = json.loads(layers.read_text(encoding="utf-8"))
            expected = [
                (main, "ref=card;w=100pw;h=100ph"),
                (card, "w=100pw;h=100ph;fill=#ffffff"),
                (card, "text=组件"),
            ]
            for layer, (source_path, source_fragment) in zip(report["layers"], expected):
                with self.subTest(path=layer["path"]):
                    span = layer["code"]
                    self.assertEqual(span["file"], str(source_path.resolve()))
                    with source_path.open("r", encoding="utf-8", newline="") as opened:
                        raw_source = opened.read()
                    self.assertEqual(
                        raw_source[span["start"]["offset"]:span["end"]["offset"]],
                        source_fragment,
                    )


class ConstantsTests(unittest.TestCase):
    def test_px_and_derived_constants_evaluate_exactly(self) -> None:
        source = "@margin=24px\n@halfMargin=1/2@margin\n[x=@margin;y=@halfMargin;w=48px;h=1/2@margin;fill=#000000]"
        rect = build_scene(source, 200, 100).layers[0].rect
        self.assertEqual(rect, Rect(Fraction(24), Fraction(12), Fraction(48), Fraction(12)))

    def test_constant_multipliers_keep_their_unit(self) -> None:
        source = "@half=50pw\n@double=2@half\n[x=1/2@half;w=@half;h=100ph][w=@double;h=100ph]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[0].rect.x, 300)
        self.assertEqual(scene.layers[0].rect.w, 600)
        self.assertEqual(scene.layers[1].rect.w, 1200)

    def test_ref_constants_are_local_to_each_definition_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "card.txt").write_text("@margin=7px\n[x=@margin;w=10px;h=10px;fill=#ffffff]", encoding="utf-8")
            scene = build_scene("@margin=19px\n[ref=card;w=100pw;h=100ph][x=@margin;w=10px;h=10px]", resource_dir=directory)
            internal = next(node for node in scene.layers if "@card" in node.path)
            self.assertEqual(internal.rect.x, 7)
            self.assertEqual(scene.layers[-1].rect.x, 19)
            (directory / "bad.txt").write_text("[x=@margin;w=10px;h=10px]", encoding="utf-8")
            with self.assertRaisesRegex(LangError, "未找到当前文件常量"):
                build_scene("[ref=bad;w=100pw;h=100ph]", resource_dir=directory)

    def test_invalid_declarations_and_unknown_references_are_rejected(self) -> None:
        invalid_sources = [
            "@m=1px\n@m=2px\n[w=10px;h=10px]",
            "@first=@later\n@later=1px\n[w=10px;h=10px]",
            "@m=1/0px\n[w=10px;h=10px]",
            "[w=10px;h=10px]\n@m=1px",
            "[x=@missing;w=10px;h=10px]",
        ]
        for source in invalid_sources:
            with self.subTest(source=source):
                with self.assertRaises(LangError):
                    build_scene(source)

    def test_px_and_constant_units_remain_strictly_checked(self) -> None:
        with self.assertRaisesRegex(LangError, "必须大于零"):
            build_scene("[w=0px;h=10px]")
        with self.assertRaisesRegex(LangError, "不允许单位 w"):
            build_scene("@own=50w\n[x=@own;w=10px;h=10px]")

    def test_crlf_header_preserves_original_code_offsets(self) -> None:
        source = "@m=2px\r\n[x=@m;w=10px;h=10px]\r\n"
        report = scene_report(build_scene(source))
        code = report["layers"][0]["code"]
        self.assertEqual(source[code["start"]["offset"]:code["end"]["offset"]], "x=@m;w=10px;h=10px")
        self.assertEqual(code["start"]["line"], 2)


class LiningTests(unittest.TestCase):
    def test_direct_lining_changes_only_the_child_parent(self) -> None:
        source = "[x=100px;y=50px;w=400px;h=200px;ll=25px;lr=50px;lt=20px;lb=30px;fill=#000000|w=100pw;h=100ph;fill=#ffffff]"
        scene = build_scene(source)
        outer, child = scene.layers
        self.assertEqual(outer.rect, Rect(Fraction(100), Fraction(50), Fraction(400), Fraction(200)))
        self.assertEqual(child.rect, Rect(Fraction(125), Fraction(70), Fraction(325), Fraction(150)))
        self.assertEqual(child.parent_rect, outer.child_rect)
        report = scene_report(scene)
        self.assertEqual(report["layers"][1]["parent_rect"], {"x": "125", "y": "70", "w": "325", "h": "150"})
        self.assertEqual(report["layers"][0]["rect_exact"], {"x": "100", "y": "50", "w": "400", "h": "200"})

    def test_one_two_and_four_value_forms_desugar(self) -> None:
        cases = [
            ("10px", Rect(Fraction(10), Fraction(10), Fraction(180), Fraction(80))),
            ("10px,20px", Rect(Fraction(20), Fraction(10), Fraction(160), Fraction(80))),
            ("1px,2px,3px,4px", Rect(Fraction(4), Fraction(1), Fraction(194), Fraction(96))),
        ]
        for value, expected in cases:
            with self.subTest(lining=value):
                child = build_scene(f"[w=200px;h=100px;lining={value}|w=100pw;h=100ph]").layers[1]
                self.assertEqual(child.rect, expected)

    def test_ref_expansion_and_own_children_share_lined_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "card.txt").write_text("[w=100pw;h=100ph;fill=#ffffff]", encoding="utf-8")
            source = "[ref=card;x=100px;w=200px;h=100px;ll=20px;lr=10px;lt=5px;lb=15px|w=100pw;h=100ph]"
            scene = build_scene(source, resource_dir=directory)
            instance, internal, own_child = scene.layers
            self.assertEqual(instance.rect, Rect(Fraction(100), Fraction(0), Fraction(200), Fraction(100)))
            expected = Rect(Fraction(120), Fraction(5), Fraction(170), Fraction(80))
            self.assertEqual(internal.rect, expected)
            self.assertEqual(own_child.rect, expected)
            self.assertEqual(internal.parent_rect, own_child.parent_rect)

    def test_negative_lining_expands_without_clamping(self) -> None:
        source = "[w=100px;h=100px;ll=-20px;lr=-30px;lt=-10px;lb=-40px|w=100pw;h=100ph]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[1].rect, Rect(Fraction(-20), Fraction(-10), Fraction(150), Fraction(150)))
        self.assertEqual(scene.layers[1].parent_rect, scene.layers[0].child_rect)
        self.assertEqual(scene_report(scene)["outside"]["partial"], ["0/0"])

    def test_nonpositive_lined_parent_is_rejected(self) -> None:
        for source in (
            "[w=100px;h=100px;ll=60px;lr=50px|w=100pw;h=100ph]",
            "[w=100px;h=100px;lining=100px|w=100pw;h=100ph]",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(LangError, "parent_rect 宽高必须大于零"):
                    build_scene(source)

    def test_constants_and_px_work_in_lining(self) -> None:
        source = "@pad=24px\n[x=100px;w=400px;h=200px;lining=@pad|w=100pw;h=100ph]"
        scene = build_scene(source)
        self.assertEqual(scene.layers[1].rect, Rect(Fraction(124), Fraction(24), Fraction(352), Fraction(152)))

    def test_lining_conflicts_invalid_forms_and_units_are_rejected(self) -> None:
        with self.assertRaisesRegex(LangError, "冲突"):
            build_scene("[w=100px;h=100px;lining=10px;ll=1px]")
        for value in ("1px,2px,3px", "1px,,2px", "1px,2px,3px,4px,5px"):
            with self.subTest(lining=value):
                with self.assertRaisesRegex(LangError, "lining"):
                    build_scene(f"[w=100px;h=100px;lining={value}]")
        with self.assertRaisesRegex(LangError, "不允许单位 a"):
            build_scene("[w=100px;h=100px;ll=1/2a]")


class ParameterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, source: str) -> None:
        (self.directory / f"{name}.txt").write_text(source, encoding="utf-8")

    def build(self, source: str) -> Scene:
        return build_scene(source, resource_dir=self.directory)

    def test_text_and_color_parameters_create_independent_variants(self) -> None:
        self.write("event", "[text=$label;size=12;color=$kind_color]")
        scene = self.build(
            "[ref=event;label=CALL;kind_color=#79c0ff;w=100px;h=40px]"
            "[ref=event;label=RET;kind_color=#7ee787;w=100px;h=40px]"
        )
        first, second = scene.layers[1], scene.layers[3]
        self.assertEqual((first.text, second.text), ("CALL", "RET"))
        self.assertEqual(first.metadata["foreground"], "#79c0ff")
        self.assertEqual(second.metadata["foreground"], "#7ee787")
        parameters = scene.layers[0].metadata["parameters"]
        self.assertEqual(parameters["kind_color"], {"type": "color", "value": "#79c0ff"})

    def test_dimension_parameters_resolve_caller_constants(self) -> None:
        self.write("chip", "[x=$offset;w=$width;h=24px;fill=$bg]")
        scene = self.build("@pad=24px\n[ref=chip;offset=@pad;width=2@pad;bg=#ffffff;w=200px;h=100px]")
        internal = scene.layers[1]
        self.assertEqual(internal.rect, Rect(Fraction(24), Fraction(0), Fraction(48), Fraction(24)))
        parameters = scene.layers[0].metadata["parameters"]
        self.assertEqual(parameters["offset"], {"type": "dimension", "value": "24px"})
        self.assertEqual(parameters["width"], {"type": "dimension", "value": "48px"})

    def test_quoted_placeholders_are_literal_text(self) -> None:
        self.write("literal", "[text=\"$label\"][text=\"hello $label\"]")
        scene = self.build("[ref=literal;w=100px;h=100px]")
        self.assertEqual([node.text for node in scene.layers[1:]], ["$label", "hello $label"])

    def test_missing_unknown_and_reserved_parameters_are_rejected(self) -> None:
        self.write("event", "[text=$label]")
        with self.assertRaisesRegex(LangError, "缺少参数"):
            self.build("[ref=event;w=100px;h=100px]")
        with self.assertRaisesRegex(LangError, "未知 ref 参数 extra"):
            self.build("[ref=event;label=CALL;extra=1;w=100px;h=100px]")
        self.write("bad", "[text=$w]")
        with self.assertRaisesRegex(LangError, "内置属性冲突"):
            self.build("[ref=bad;w=100px;h=100px]")

    def test_invalid_values_and_type_conflicts_are_rejected(self) -> None:
        self.write("event", "[text=$label;color=$tone]")
        with self.assertRaisesRegex(LangError, "颜色只接受"):
            self.build("[ref=event;label=CALL;tone=#fff;w=100px;h=100px]")
        self.write("chip", "[x=$offset;w=10px;h=10px]")
        with self.assertRaisesRegex(LangError, "数字\\+单位"):
            self.build("[ref=chip;offset=nope;w=100px;h=100px]")
        self.write("conflict", "[text=$thing][w=10px;h=10px;fill=$thing]")
        with self.assertRaisesRegex(LangError, "同时被当作"):
            self.build("[ref=conflict;thing=#ffffff;w=100px;h=100px]")

    def test_nested_literal_arguments_work_but_forwarding_is_rejected(self) -> None:
        self.write("child", "[text=$label]")
        self.write("outer", "[ref=child;label=static;w=100pw;h=100ph]")
        scene = self.build("[ref=outer;w=100px;h=100px]")
        self.assertEqual(scene.layers[-1].text, "static")
        self.write("forward", "[ref=child;label=$label;w=100pw;h=100ph]")
        with self.assertRaisesRegex(LangError, "不允许嵌套转发"):
            self.build("[ref=forward;label=value;w=100px;h=100px]")

    def test_report_keeps_definition_code_and_instance_arguments(self) -> None:
        self.write("event", "[text=$label;color=$tone]")
        main = self.directory / "main.txt"
        main.write_text("[ref=event;label=CALL;tone=#79c0ff;w=100px;h=40px]", encoding="utf-8")
        report = scene_report(build_scene(main.read_text(encoding="utf-8"), resource_dir=self.directory, source_path=main))
        instance, internal = report["layers"]
        self.assertEqual(instance["code"]["file"], str(main.resolve()))
        self.assertEqual(internal["code"]["file"], str((self.directory / "event.txt").resolve()))
        self.assertEqual(internal["code"]["start"]["offset"], 1)
        self.assertEqual(instance["content_metadata"]["parameters"]["label"], {"type": "text", "value": "CALL"})

    def test_standalone_and_unsupported_parameter_positions_are_rejected(self) -> None:
        with self.assertRaisesRegex(LangError, "只能在 ref 定义实例化时使用"):
            build_scene("[text=$label]")
        self.write("badlining", "[w=100px;h=100px;lining=$pad]")
        with self.assertRaisesRegex(LangError, "只能用于受支持"):
            self.build("[ref=badlining;pad=24px;w=100px;h=100px]")
        self.write("badsize", "[text=hello;size=$size]")
        with self.assertRaisesRegex(LangError, "只能用于受支持"):
            self.build("[ref=badsize;size=40;w=100px;h=100px]")


class InspectTests(unittest.TestCase):
    def run_inspect(self, source: str, *arguments: str, width: int = 100, height: int = 100, refs: bool = False) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            if refs:
                (directory / "card.txt").write_text("[text=hello]", encoding="utf-8")
            path = directory / "main.txt"
            path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, str(HERE / "layerlang_mvp.py"), "inspect", str(path),
                    "--width", str(width), "--height", str(height), *arguments,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
            return json.loads(completed.stdout.decode("utf-8"))

    def test_summary_counts_kinds_and_outside_without_dumping_layers(self) -> None:
        report = self.run_inspect("[w=100px;h=100px|text=hello][x=200px;w=10px;h=10px]")
        self.assertEqual(report["summary"]["layers"], 3)
        self.assertEqual(report["summary"]["kinds"], {"rect": 2, "text": 1})
        self.assertEqual(report["summary"]["outside"], {"partial": [], "full": ["1"]})
        self.assertEqual(report["layers"], [])

    def test_text_filter_returns_only_matching_text_layers(self) -> None:
        report = self.run_inspect("[text=hello][text=world]", "--text", "hello")
        self.assertEqual([layer["text"] for layer in report["layers"]], ["hello"])

    def test_kind_and_path_filters_include_ref_subtree(self) -> None:
        source = "[ref=card;w=100px;h=100px][text=hello]"
        by_kind = self.run_inspect(source, "--kind", "ref", refs=True)
        self.assertEqual([layer["path"] for layer in by_kind["layers"]], ["0"])
        by_path = self.run_inspect(source, "--path", "0", refs=True)
        self.assertEqual([layer["path"] for layer in by_path["layers"]], ["0", "0@card/0"])


class CliTests(unittest.TestCase):
    def test_piped_stdout_and_stderr_are_utf8_under_legacy_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "gbk"

            def run_cli(input_path: Path, stem: str) -> subprocess.CompletedProcess[bytes]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(HERE / "layerlang_mvp.py"),
                        "render",
                        str(input_path),
                        "--svg",
                        str(directory / f"{stem}.svg"),
                        "--layers",
                        str(directory / f"{stem}.json"),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    check=False,
                )

            valid = directory / "valid.txt"
            valid.write_text("[w=100pw;h=100ph;fill=#ffffff]", encoding="utf-8")
            succeeded = run_cli(valid, "valid")
            self.assertEqual(
                succeeded.returncode,
                0,
                succeeded.stderr.decode("utf-8", errors="replace"),
            )
            self.assertIn("已生成", succeeded.stdout.decode("utf-8"))

            invalid = directory / "invalid.txt"
            invalid.write_text("[text=测试;font=missing.ttf]", encoding="utf-8")
            failed = run_cli(invalid, "invalid")
            self.assertEqual(failed.returncode, 1)
            error_output = failed.stderr.decode("utf-8")
            self.assertIn("运行错误", error_output)
            self.assertIn("节点 0", error_output)


class ErrorContextTests(unittest.TestCase):
    def test_nested_resource_error_contains_code_location_and_node_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            card = directory / "card.txt"
            card.write_text("[text=测试;font=missing.ttf]", encoding="utf-8")
            main = directory / "main.txt"
            source = "[ref=card;w=100pw;h=100ph]"
            main.write_text(source, encoding="utf-8")
            with self.assertRaises(EvaluationError) as caught:
                build_scene(source, source_path=main)
            error = caught.exception
            self.assertEqual(error.node_path, "0@card/0")
            self.assertEqual(error.code["file"], str(card.resolve()))
            self.assertIn("0@card/0", str(error))
            self.assertIn("card.txt", str(error))
            self.assertIn("第 1 行", str(error))

    def test_parameter_definition_error_points_to_definition_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bad = directory / "bad.txt"
            bad.write_text("[text=$w]", encoding="utf-8")
            main = directory / "main.txt"
            source = "[ref=bad;w=100px;h=100px]"
            main.write_text(source, encoding="utf-8")
            with self.assertRaises(LangError) as caught:
                build_scene(source, resource_dir=directory, source_path=main)
            self.assertEqual(caught.exception.source_path, str(bad.resolve()))
            self.assertIn("内置属性冲突", caught.exception.message)


def inspect_report(scene: Scene, text: Optional[str] = None, kind: Optional[str] = None, path: Optional[str] = None, outside: bool = False) -> dict[str, object]:
    report = scene_report(scene)
    layers = report["layers"]
    kinds = Counter(layer["kind"] for layer in layers)
    summary = {
        "layers": len(layers),
        "kinds": dict(sorted(kinds.items())),
        "parameterized_refs": sum(1 for layer in layers if layer["kind"] == "ref" and layer["content_metadata"] and layer["content_metadata"].get("parameters")),
        "outside": report["outside"],
    }
    filters = {"text": text, "kind": kind, "path": path, "outside": outside}

    def matches(layer: dict[str, object]) -> bool:
        if text is not None and text not in (layer["text"] or ""):
            return False
        if kind is not None and layer["kind"] != kind:
            return False
        if path is not None and not (layer["path"] == path or layer["path"].startswith(path + "/") or layer["path"].startswith(path + "@")):
            return False
        if outside and layer["outside"] == "inside":
            return False
        return True

    selected = [layer for layer in layers if matches(layer)] if any((text is not None, kind is not None, path is not None, outside)) else []
    return {
        "spec_version": report["spec_version"],
        "canvas": report["canvas"],
        "summary": summary,
        "filters": filters,
        "layers": selected,
    }


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Layerlang v0.8 渲染器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("input")
    render_parser.add_argument("--width", type=int, default=1200)
    render_parser.add_argument("--height", type=int, default=1200)
    render_parser.add_argument("--svg", required=True)
    render_parser.add_argument("--layers", required=True)
    render_parser.add_argument("--hit", nargs=2, metavar=("X", "Y"))
    subparsers.add_parser("self-test")
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("input")
    inspect_parser.add_argument("--width", type=int, default=1200)
    inspect_parser.add_argument("--height", type=int, default=1200)
    inspect_parser.add_argument("--text")
    inspect_parser.add_argument("--kind", choices=("rect", "text", "image", "ref"))
    inspect_parser.add_argument("--path")
    inspect_parser.add_argument("--outside", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "self-test":
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite([
            loader.loadTestsFromTestCase(RectTests),
            loader.loadTestsFromTestCase(TextTests),
            loader.loadTestsFromTestCase(FontTests),
            loader.loadTestsFromTestCase(QuoteTests),
            loader.loadTestsFromTestCase(ImageTests),
            loader.loadTestsFromTestCase(RefTests),
            loader.loadTestsFromTestCase(AnchorTests),
            loader.loadTestsFromTestCase(ReportTests),
            loader.loadTestsFromTestCase(ConstantsTests),
            loader.loadTestsFromTestCase(LiningTests),
            loader.loadTestsFromTestCase(ParameterTests),
            loader.loadTestsFromTestCase(InspectTests),
            loader.loadTestsFromTestCase(CliTests),
            loader.loadTestsFromTestCase(ErrorContextTests),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    try:
        input_path = Path(args.input)
        source = read_source_text(input_path)
        scene = build_scene(source, args.width, args.height, input_path.resolve().parent, input_path.resolve())
        if args.command == "inspect":
            print(json.dumps(inspect_report(scene, args.text, args.kind, args.path, args.outside), ensure_ascii=False, indent=2))
            return 0
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
