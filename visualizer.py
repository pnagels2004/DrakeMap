#!/usr/bin/env python3
"""
Interactive viewer for Drake system diagrams exported as Graphviz SVG.

    python drake_diagram_viewer.py system_diagram.svg
    python drake_diagram_viewer.py system_diagram.svg --export out.png            # headless render
    python drake_diagram_viewer.py system_diagram.svg --collapse --export out.png

The SVG is parsed into blocks (systems), arrows (connections) and diagram boxes
(clusters) and rebuilt as a live scene:

  * Click a block      -> the block and ALL its connection arrows are highlighted
                          (orange = outgoing, blue = incoming, green = between
                          two selected blocks). The connected port rows light up
                          too and everything unrelated is dimmed.
  * Drag a block       -> move it; arrows follow, diagram boxes resize to fit.
  * Drag a diagram box title/border -> moves the whole sub-diagram.
  * Fold / unfold      -> click the [-] button in a sub-diagram's title bar (or
                          double-click the title) to fold it into ONE block that
                          lists its input/output ports; click [+] or double-click
                          the folded block to unfold. Arrows are re-attached to the
                          matching port rows.
  * Rubber-band on empty space / Shift-click -> multi-select.
  * Ctrl+G / Ctrl+Shift+G -> group / ungroup the selection (groups move as one;
                          double-click a group label to rename it).
  * Ctrl+drag (or right/middle-drag) pans, wheel zooms, F fits, Ctrl+F searches blocks.
  * Ctrl+S saves the layout (positions, groups, folded diagrams) next to the SVG;
    it is reloaded automatically next time.
  * Auto-reload        -> when the SVG file is rewritten (e.g. by your own script) the
                          viewer picks up the newest version by itself. The layout is
                          RESET to the new SVG's own positions (folds and groups are
                          cleared too) but the highlighted blocks stay highlighted.
                          F5 forces a reload.
  * Values inspector   -> the dock on the right lists the input and output VALUES of the selected
                          block(s). They come from the cache file written by
                          drake_probe.run_debug(): ~/.cache/drake_diagram_viewer/<svg name>.values.json
                          (override with --values FILE). Toggle the dock with Ctrl+I.
  * Neovim link        -> the block whose NAME equals the word under your Neovim cursor, or else
                          the blocks of that CLASS (GamePad, Gain, ...), is selected and the view
                          glides to it like the search does (the word is written by drake_cursor.lua
                          to ~/.cache/drake_diagram_viewer/nvim_cursor.json). A word that matches
                          nothing leaves the selection as it was. On by default,
                          toggle with Ctrl+Shift+N (remembered; --cursor FILE overrides the path).
  * Dark mode (Dracula palette) is the default; toggle with Ctrl+Shift+D (remembered).

Requires:  pip install PySide6      (PyQt6 also works)
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError:
        sys.exit("This program needs PySide6 (or PyQt6):  pip install PySide6")

Qt = QtCore.Qt
QPointF, QRectF, QPoint, QRect = (
    QtCore.QPointF,
    QtCore.QRectF,
    QtCore.QPoint,
    QtCore.QRect,
)
QColor, QPen, QBrush, QFont = QtGui.QColor, QtGui.QPen, QtGui.QBrush, QtGui.QFont
QPainter, QPainterPath, QPolygonF = QtGui.QPainter, QtGui.QPainterPath, QtGui.QPolygonF
QGraphicsItem = QtWidgets.QGraphicsItem

SVG_NS = "{http://www.w3.org/2000/svg}"
COMPASS = {"n", "ne", "e", "se", "s", "sw", "w", "nw", "c", "_"}

APP_ORG, APP_NAME = "drake-diagram-viewer", "viewer"
DARK_EDGE = {
    "blue": "#8be9fd",
    "green": "#50fa7b",
    "magenta": "#ff79c6",
    "purple": "#bd93f9",
    "red": "#ff5555",
    "orange": "#ffb86c",
    "brown": "#e0b080",
}


class Theme:
    """All colours used by the scene in one place (dark is the default)."""

    def __init__(self, dark: bool):
        self.dark = dark
        if dark:  # Dracula
            self.bg = "#282a36"
            self.cluster_border, self.port_fill, self.port_border = (
                "#6272a4",
                "#21222c",
                "#44475a",
            )
            self.cluster_title, self.cluster_sub = "#f8f8f2", "#6272a4"
            self.block_body = "#343746"
            self.head_sl, self.border_sl = (0.50, 0.36), (0.60, 0.64)
            self.grid, self.line = "#44475a", "#6272a4"
            self.text, self.name_text, self.grey_text = "#f8f8f2", "#a0abd6", "#6272a4"
            self.edge_default = "#7f8ab8"
            self.kind_color = {"out": "#ffb86c", "in": "#8be9fd", "both": "#50fa7b"}
            self.hot_alpha, self.group_alpha = 95, 36
            self.sel_outline, self.nbr_outline = "#f1fa8c", "#bd93f9"
            self.fold_head, self.fold_border = (0.74, 0.40, 0.36), (0.74, 0.60, 0.70)
            self.fold_stack, self.glyph_fill, self.glyph_stroke = (
                "#21222c",
                "#282a36",
                "#bd93f9",
            )
            self.fold_btn_stroke = "#6272a4"
            self.dim_edge, self.dim_block = 0.16, 0.35
        else:
            self.bg = "#fcfcfd"
            self.cluster_border, self.port_fill, self.port_border = (
                "#aeb6c2",
                "#e4e7eb",
                "#cfd4da",
            )
            self.cluster_title, self.cluster_sub = "#343a40", "#6b7280"
            self.block_body = "#ffffff"
            self.head_sl, self.border_sl = (0.55, 0.83), (0.35, 0.48)
            self.grid, self.line = "#d0d4da", "#b0b5bd"
            self.text, self.name_text, self.grey_text = "#1f2328", "#6b7280", "#9aa0a6"
            self.edge_default = "#6b7280"
            self.kind_color = {"out": "#f76707", "in": "#1c7ed6", "both": "#2f9e44"}
            self.hot_alpha, self.group_alpha = 95, 22
            self.sel_outline, self.nbr_outline = "#f59f00", "#ffc078"
            self.fold_head, self.fold_border = (0.58, 0.38, 0.80), (0.58, 0.40, 0.40)
            self.fold_stack, self.glyph_fill, self.glyph_stroke = (
                "#eef1f6",
                "#ffffff",
                "#4b5a73",
            )
            self.fold_btn_stroke = "#6b7280"
            self.dim_edge, self.dim_block = 0.12, 0.4

    def cluster_fill(self, depth: int) -> QColor:
        if self.dark:
            return hsl(0.64, 0.16, min(0.30, 0.150 + 0.030 * depth))
        return hsl(0.60, 0.25, max(0.80, 0.965 - 0.022 * depth))

    def edge_color(self, name: str) -> QColor:
        if name in ("black", "#000000", "", "none"):
            return QColor(self.edge_default)
        c = QColor(name)
        if not c.isValid():
            return QColor(self.edge_default)
        if self.dark:
            return QColor(DARK_EDGE[name]) if name in DARK_EDGE else c.lighter(150)
        return c


T = Theme(True)


def set_theme(dark: bool):
    global T
    T = Theme(dark)


GROUP_COLORS = [
    "#ff79c6",
    "#8be9fd",
    "#bd93f9",
    "#50fa7b",
    "#ffb86c",
    "#f1fa8c",
    "#ff5555",
    "#6272a4",
]

Z_CLUSTER, Z_GROUP, Z_EDGE, Z_BLOCK, Z_EDGE_HOT = -10.0, -1.0, 1.0, 3.0, 5.0


# =============================================================================
# SVG parsing
# =============================================================================
@dataclass
class TextSpec:
    text: str
    x: float
    y: float
    bold: bool
    anchor: str
    color: str | None


@dataclass
class NodeData:
    key: str
    rects: list = field(default_factory=list)  # [(QRectF, stroke, fill)]
    lines: list = field(default_factory=list)  # [([QPointF], stroke)]
    ellipse: QRectF | None = None
    stroke: str = "black"
    texts: list = field(default_factory=list)
    bbox: QRectF = field(default_factory=QRectF)
    has_header: bool = False


@dataclass
class EdgeData:
    src: str
    dst: str
    src_port: str | None
    dst_port: str | None
    color: str
    pts: list  # M point + cubic control points
    arrow: list  # 3 vertices of the arrow head
    tip: QPointF | None = None


@dataclass
class ClusterData:
    title: str
    rect: QRectF
    kind: str  # diagram | in | out
    texts: list = field(default_factory=list)  # (text, y_offset_from_top, bold)
    parent: int | None = None
    children: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    depth: int = 0
    pad: tuple = (0, 0, 0, 0)  # l, t, r, b around its content


def _points(s: str):
    return [QPointF(*map(float, p.split(","))) for p in s.split()]


def _bbox(pts) -> QRectF:
    xs, ys = [p.x() for p in pts], [p.y() for p in pts]
    return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _split_endpoint(s: str):
    name, sep, port = s.rpartition(":")
    if sep and port in COMPASS:
        return name, port
    return s, None


def _text_spec(el) -> TextSpec:
    return TextSpec(
        text="".join(el.itertext()).strip(),
        x=float(el.get("x", 0)),
        y=float(el.get("y", 0)),
        bold=el.get("font-weight") == "bold",
        anchor=el.get("text-anchor", "start"),
        color=el.get("fill"),
    )


class Diagram:
    def __init__(self, path):
        self.path = Path(path)
        self.nodes: dict[str, NodeData] = {}
        self.edges: list[EdgeData] = []
        self.clusters: list[ClusterData] = []
        self._parse()
        self._build_hierarchy()

    # -- parsing ----------------------------------------------------------
    def _parse(self):
        root = ET.parse(self.path).getroot()
        for g in root.iter(SVG_NS + "g"):
            cls = g.get("class")
            if cls not in ("node", "edge", "cluster"):
                continue
            title = g.findtext(SVG_NS + "title") or ""
            if cls == "node":
                self._parse_node(g, title)
            elif cls == "edge":
                self._parse_edge(g, title)
            else:
                self._parse_cluster(g, title)
        if not self.nodes:
            raise ValueError("No Graphviz nodes found - is this a Drake diagram SVG?")

    def _parse_node(self, g, title):
        nd = NodeData(key=title)
        for el in g:
            tag = el.tag.replace(SVG_NS, "")
            if tag == "polygon":
                pts = _points(el.get("points"))
                nd.rects.append(
                    (_bbox(pts), el.get("stroke", "black"), el.get("fill", "none"))
                )
            elif tag == "polyline":
                nd.lines.append((_points(el.get("points")), el.get("stroke", "grey")))
            elif tag == "ellipse":
                cx, cy, rx, ry = (float(el.get(k)) for k in ("cx", "cy", "rx", "ry"))
                nd.ellipse = QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry)
                nd.stroke = el.get("stroke", "black")
            elif tag == "text":
                nd.texts.append(_text_spec(el))
        nd.has_header = any(t.bold for t in nd.texts) and bool(nd.rects)
        if nd.rects and nd.ellipse is None:
            nd.stroke = nd.rects[0][1]
        shapes = [r for r, _, _ in nd.rects] + ([nd.ellipse] if nd.ellipse else [])
        bb = shapes[0]
        for r in shapes[1:]:
            bb = bb.united(r)
        nd.bbox = bb
        self.nodes[title] = nd

    def _parse_edge(self, g, title):
        if "->" not in title:
            return
        a, b = title.split("->", 1)
        (src, sp), (dst, dp) = _split_endpoint(a), _split_endpoint(b)
        path_el = g.find(SVG_NS + "path")
        poly_el = g.find(SVG_NS + "polygon")
        if path_el is None:
            return
        nums = [
            float(v)
            for v in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", path_el.get("d"))
        ]
        pts = [QPointF(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        arrow, tip = [], None
        if poly_el is not None:
            arrow = _points(poly_el.get("points"))[:3]
            end = pts[-1]
            tip = max(arrow, key=lambda p: (p - end).manhattanLength())
        self.edges.append(
            EdgeData(src, dst, sp, dp, path_el.get("stroke", "black"), pts, arrow, tip)
        )

    def _parse_cluster(self, g, title):
        if title.endswith("subsystems"):
            return  # invisible wrapper
        poly = g.find(SVG_NS + "polygon")
        if poly is None:
            return
        rect = _bbox(_points(poly.get("points")))
        kind = (
            "in"
            if title.endswith("in")
            else "out"
            if title.endswith("out")
            else "diagram"
        )
        texts = [
            (t.text, t.y - rect.top(), t.bold)
            for t in map(_text_spec, g.findall(SVG_NS + "text"))
        ]
        self.clusters.append(ClusterData(title, rect, kind, texts))

    # -- containment hierarchy -------------------------------------------
    def _build_hierarchy(self):
        cs = self.clusters
        area = lambda r: r.width() * r.height()
        contains = lambda outer, inner: outer.adjusted(-0.5, -0.5, 0.5, 0.5).contains(
            inner
        )
        for i, c in enumerate(cs):
            best = None
            for j, o in enumerate(cs):
                if i != j and area(o.rect) > area(c.rect) and contains(o.rect, c.rect):
                    if best is None or area(o.rect) < area(cs[best].rect):
                        best = j
            c.parent = best
            if best is not None:
                cs[best].children.append(i)
        for c in cs:
            d, p = 0, c.parent
            while p is not None:
                d, p = d + 1, cs[p].parent
            c.depth = d
        self.node_cluster: dict[str, int | None] = {}
        for key, nd in self.nodes.items():
            best = None
            for j, c in enumerate(cs):
                if contains(c.rect, nd.bbox) and (
                    best is None or area(c.rect) < area(cs[best].rect)
                ):
                    best = j
            self.node_cluster[key] = best
            if best is not None:
                cs[best].nodes.append(key)
        for c in cs:
            parts = [self.nodes[k].bbox for k in c.nodes] + [
                cs[j].rect for j in c.children
            ]
            if not parts:
                continue
            base = parts[0]
            for r in parts[1:]:
                base = base.united(r)
            c.pad = (
                base.left() - c.rect.left(),
                base.top() - c.rect.top(),
                c.rect.right() - base.right(),
                c.rect.bottom() - base.bottom(),
            )


# =============================================================================
# Drawing helpers
# =============================================================================
def hsl(h, s, l, a=1.0) -> QColor:
    c = QColor()
    c.setHslF(h, s, l, a)
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        *(
            int(x * (1 - t) + y * t)
            for x, y in (
                (a.red(), b.red()),
                (a.green(), b.green()),
                (a.blue(), b.blue()),
            )
        )
    )


_font_cache: dict = {}


def font(px: float, bold=False) -> QFont:
    k = (round(px, 1), bold)
    if k not in _font_cache:
        f = QFont()
        f.setPixelSize(max(1, int(round(px))))
        f.setBold(bold)
        _font_cache[k] = f
    return _font_cache[k]


def text_width(text: str, px: float, bold=False) -> float:
    return QtGui.QFontMetricsF(font(px, bold)).horizontalAdvance(text)


def draw_centered(
    p: QPainter,
    text: str,
    cx: float,
    baseline: float,
    px: float,
    bold: bool,
    color: QColor,
    max_width: float | None = None,
):
    px_use = px
    w = text_width(text, px_use, bold)
    if max_width and w > max_width:
        px_use = max(7.0, px * max_width / w)
        w = text_width(text, px_use, bold)
    p.setFont(font(px_use, bold))
    p.setPen(color)
    p.drawText(QPointF(cx - w / 2, baseline), text)


# =============================================================================
# Scene items
# =============================================================================
class BlockItem(QGraphicsItem):
    kind = "block"
    is_collapsed = False

    def __init__(self, data: NodeData):
        super().__init__()
        self.data = data
        self.origin = data.bbox.topLeft()
        self.rect = QRectF(0, 0, data.bbox.width(), data.bbox.height())
        self.setPos(self.origin)
        self.setZValue(Z_BLOCK)
        self.group: Group | None = None
        self.cluster: ClusterItem | None = None
        self.edges: list[EdgeItem] = []
        self.state = "normal"  # normal | selected | neighbor
        self.hot: dict[int, QColor] = {}
        self.label = data.key

        o = self.origin
        loc = lambda r: r.translated(-o.x(), -o.y())
        rects = [loc(r) for r, _, _ in data.rects]
        self.header = rects[0] if data.has_header else None
        self.cells = rects[1:] if data.has_header else rects
        self.lines = [([p - o for p in pts], c) for pts, c in data.lines]

        # texts -> owning rectangle (smallest one containing the text origin)
        self.texts = []
        owners = ([self.header] if self.header else []) + self.cells
        if data.ellipse:
            owners = [self.rect]
        for t in data.texts:
            pt = QPointF(t.x - o.x() + 2, t.y - o.y() - 2)
            cand = [r for r in owners if r.contains(pt)] or [self.rect]
            r = min(cand, key=lambda r: r.width() * r.height())
            self.texts.append((t.text, r, t.y - o.y(), t.bold, t.color))

        bold = [t.text for t in data.texts if t.bold]
        names = [t.text[5:] for t in data.texts if t.text.startswith("name=")]
        self.type_name = bold[0] if bold else ""
        self.sys_name = names[0] if names else ""
        if self.type_name:
            self.label = (
                f"{self.type_name}  “{self.sys_name}”"
                if self.sys_name
                else self.type_name
            )

        self.sig = (
            data.key
        )  # stable signature, set by the scene (survives SVG regeneration)
        self.restyle()

    def restyle(self):
        t, data = T, self.data
        if data.has_header:
            h = (zlib.crc32(self.type_name.encode()) % 360) / 360.0
            self.head_color, self.border = hsl(h, *t.head_sl), hsl(h, *t.border_sl)
            self.body = QColor(t.block_body)
        else:
            base = t.edge_color(
                data.stroke if data.stroke not in ("none", "") else "#888"
            )
            self.border = base.lighter(125) if t.dark else base.darker(110)
            self.body = mix(QColor(t.block_body), base, 0.20 if t.dark else 0.16)
            self.head_color = self.body
        self.grid = QColor(t.grid)
        self.update()

    # geometry -------------------------------------------------------------
    def scene_rect(self) -> QRectF:
        return QRectF(self.pos(), self.rect.size())

    def boundingRect(self):
        return self.rect.adjusted(-8, -8, 8, 8)

    def shape(self):
        p = QPainterPath()
        if self.data.ellipse:
            p.addEllipse(self.rect)
        else:
            p.addRoundedRect(self.rect, 7, 7)
        return p

    # ports ----------------------------------------------------------------
    def port_cell(self, scene_pt: QPointF, side: str | None):
        """Index of the port row an arrow end attaches to (or None)."""
        if not self.cells or side not in ("e", "w"):
            return None
        y = scene_pt.y() - self.pos().y()
        row = [
            i for i, c in enumerate(self.cells) if c.top() - 1 <= y <= c.bottom() + 1
        ]
        if not row:
            return None
        return (
            max(row, key=lambda i: self.cells[i].right())
            if side == "e"
            else min(row, key=lambda i: self.cells[i].left())
        )

    def cell_text(self, idx):
        if idx is None:
            return ""
        for text, r, *_ in self.texts:
            if r == self.cells[idx]:
                return text
        return ""

    # painting -------------------------------------------------------------
    def paint(self, p: QPainter, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect
        if self.data.ellipse:
            p.setPen(QPen(self.border, 1.6))
            p.setBrush(self.body)
            p.drawEllipse(r)
        else:
            path = QPainterPath()
            path.addRoundedRect(r, 7, 7)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.body)
            p.drawPath(path)
            p.save()
            p.setClipPath(path)
            if self.header:
                p.setBrush(self.head_color)
                p.drawRect(self.header)
            p.setPen(Qt.PenStyle.NoPen)
            for i, c in enumerate(self.cells):
                if i in self.hot:
                    p.setBrush(self.hot[i])
                    p.drawRect(c)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self.grid, 0.8))
            for c in ([self.header] if self.header else []) + self.cells:
                p.drawRect(c)
            for pts, col in self.lines:
                p.setPen(QPen(QColor(T.line), 1))
                p.drawPolyline(QPolygonF(pts))
            p.restore()
            p.setPen(QPen(self.border, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        for text, cell, y, bold, color in self.texts:
            is_name = text.startswith("name=")
            if color in ("grey", "gray"):
                col = QColor(T.grey_text)
            elif color and color not in ("black", "#000000"):
                col = T.edge_color(color)
            else:
                col = QColor(T.name_text if is_name else T.text)
            px = 13 if (bold and self.header) else 11 if is_name else 12
            draw_centered(
                p, text, cell.center().x(), y, px, bold, col, cell.width() - 10
            )

        if self.state == "selected":
            p.setPen(QPen(QColor(T.sel_outline), 3.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 9, 9)
        elif self.state == "neighbor":
            p.setPen(QPen(QColor(T.nbr_outline), 2.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 8, 8)

    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.update()


class CollapsedBlock(BlockItem):
    """A folded sub-diagram: header + one row per exported input/output port."""

    is_collapsed = True
    HEAD, ROW = 42.0, 24.0

    def __init__(self, cluster_item: "ClusterItem", ins: list, outs: list):
        cd = cluster_item.data
        type_name = next((t for t, _, b in cd.texts if b), "Diagram")
        name = next((t for t, _, b in cd.texts if t.startswith("name=")), "")
        w_in = max([text_width(t, 12) for t in ins], default=0) + 26
        w_out = max([text_width(t, 12) for t in outs], default=0) + 26
        hdr_w = max(text_width(type_name, 13, True), text_width(name, 11)) + 60
        if ins and outs:
            W = max(w_in + w_out, hdr_w, 180.0)
            w_l = W * w_in / (w_in + w_out)
            w_r = W - w_l
        elif ins:
            W = max(w_in, hdr_w, 150.0)
            w_l, w_r = W, 0.0
        elif outs:
            W = max(w_out, hdr_w, 150.0)
            w_l, w_r = 0.0, W
        else:
            W, w_l, w_r = max(hdr_w, 150.0), 0.0, 0.0
        rows = max(len(ins), len(outs))
        nd = NodeData(key=cd.title, has_header=True, stroke="black")
        nd.rects.append((QRectF(0, 0, W, self.HEAD), "black", "none"))
        nd.texts.append(TextSpec(type_name, 12, 18, True, "start", None))
        if name:
            nd.texts.append(TextSpec(name, 12, 33, False, "start", None))
        self.in_map: dict[str, int] = {}
        self.out_map: dict[str, int] = {}
        idx = 0
        for r in range(rows):
            top = self.HEAD + r * self.ROW
            if w_l:
                nd.rects.append((QRectF(0, top, w_l, self.ROW), "black", "none"))
                if r < len(ins) and ins[r]:
                    nd.texts.append(
                        TextSpec(ins[r], 5, top + 16.5, False, "start", None)
                    )
                    self.in_map.setdefault(ins[r], idx)
                idx += 1
            if w_r:
                nd.rects.append((QRectF(w_l, top, w_r, self.ROW), "black", "none"))
                if r < len(outs) and outs[r]:
                    nd.texts.append(
                        TextSpec(outs[r], w_l + 5, top + 16.5, False, "start", None)
                    )
                    self.out_map.setdefault(outs[r], idx)
                idx += 1
        nd.bbox = QRectF(0, 0, W, self.HEAD + rows * self.ROW)
        super().__init__(nd)
        self.cluster_item = cluster_item
        self.label = (
            f"{type_name} {name[5:]}  (folded)" if name else f"{type_name} (folded)"
        )
        self.restyle()

    def restyle(self):
        super().restyle()
        self.head_color, self.border = hsl(*T.fold_head), hsl(*T.fold_border)
        self.body = QColor(T.block_body)

    def toggle_rect(self) -> QRectF:
        return QRectF(self.pos().x() + 1, self.pos().y() + 1, 22, 22)

    def boundingRect(self):
        return self.rect.adjusted(-8, -8, 18, 18)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.border, 1.2))
        p.setBrush(QColor(T.fold_stack))
        for off in (10, 5):  # "stack of pages" look
            p.drawRoundedRect(self.rect.translated(off, off), 7, 7)
        super().paint(p, option, widget)
        r = QRectF(5, 5, 14, 14)  # [+] unfold button
        p.setPen(QPen(QColor(T.glyph_stroke), 1.4))
        p.setBrush(QColor(T.glyph_fill))
        p.drawRoundedRect(r, 3, 3)
        p.drawLine(QPointF(8.5, 12), QPointF(15.5, 12))
        p.drawLine(QPointF(12, 8.5), QPointF(12, 15.5))


class EdgeItem(QtWidgets.QGraphicsPathItem):
    kind = "edge"

    def __init__(self, data: EdgeData, src: BlockItem, dst: BlockItem):
        super().__init__()
        self.data, self.src, self.dst = data, src, dst
        self.setZValue(Z_EDGE)
        self.base_color = T.edge_color(data.color)
        self.arrow = QtWidgets.QGraphicsPolygonItem(self)
        self.arrow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        pts = data.pts
        self.base = pts[-1]
        t = pts[1] - pts[0] if len(pts) > 1 else QPointF(1, 0)
        n = (t.x() ** 2 + t.y() ** 2) ** 0.5
        self.t0 = QPointF(t.x() / n, t.y() / n) if n > 1e-6 else QPointF(1, 0)
        d = (data.tip - self.base) if data.tip is not None else QPointF(1, 0)
        n = (d.x() ** 2 + d.y() ** 2) ** 0.5
        self.dir = QPointF(d.x() / n, d.y() / n) if n > 1e-6 else QPointF(1, 0)

        self.src_cell = src.port_cell(pts[0], data.src_port)
        end_ref = data.tip if data.tip is not None else pts[-1]
        self.dst_cell = dst.port_cell(end_ref, data.dst_port)
        self.style = None
        self.hidden = False
        self.bind(src, self.src_cell, dst, self.dst_cell)
        self.set_style(None)

    # effective endpoints (differ from src/dst when a sub-diagram is folded) ----
    def bind(self, vsrc, vsrc_cell, vdst, vdst_cell):
        self.vsrc, self.vsrc_cell, self.vdst, self.vdst_cell = (
            vsrc,
            vsrc_cell,
            vdst,
            vdst_cell,
        )
        self.hidden = (
            vsrc is vdst and vsrc.is_collapsed
        )  # edge internal to a folded diagram
        self.setVisible(not self.hidden)
        a, b = vsrc.cell_text(vsrc_cell), vdst.cell_text(vdst_cell)
        self.setToolTip(
            f"{vsrc.label}"
            + (f"  →  {a}" if a else "")
            + f"\n   ↳  {vdst.label}"
            + (f"  ←  {b}" if b else "")
        )
        if not self.hidden:
            self.update_path()

    @staticmethod
    def _anchor(blk: BlockItem, cell, side: str) -> QPointF:
        y = blk.cells[cell].center().y() if cell is not None else blk.rect.height() / 2
        x = blk.rect.width() + 1 if side == "e" else -1
        return blk.pos() + QPointF(x, y)

    def update_path(self):
        if self.hidden:
            return
        ds, dd = self.src.pos() - self.src.origin, self.dst.pos() - self.dst.origin
        pts = self.data.pts
        real_s, real_d = self.vsrc is self.src, self.vdst is self.dst
        path = QPainterPath()
        arrow = []
        if real_s and real_d and (ds - dd).manhattanLength() < 1e-6:
            p = [q + ds for q in pts]  # rigid move: keep Graphviz routing
            path.moveTo(p[0])
            for i in range(1, len(p) - 2, 3):
                path.cubicTo(p[i], p[i + 1], p[i + 2])
            arrow = [q + dd for q in self.data.arrow]
        else:
            if real_s:
                p0, t0 = pts[0] + ds, self.t0
            else:
                p0, t0 = self._anchor(self.vsrc, self.vsrc_cell, "e"), QPointF(1, 0)
            if real_d:
                end, dv = self.base + dd, self.dir
                arrow = [q + dd for q in self.data.arrow]
            else:
                tip, dv = self._anchor(self.vdst, self.vdst_cell, "w"), QPointF(1, 0)
                end = tip - dv * 10
                arrow = [tip, end + QPointF(0, 3.5), end - QPointF(0, 3.5)]
            k = max(50.0, min(300.0, abs(end.x() - p0.x()) * 0.5))
            path.moveTo(p0)
            path.cubicTo(p0 + t0 * k, end - dv * k, end)
        self.setPath(path)
        self.arrow.setPolygon(QPolygonF(arrow))

    def boundingRect(self):
        return self.shape().boundingRect()

    def shape(self):
        s = QtGui.QPainterPathStroker()
        s.setWidth(10)
        return s.createStroke(self.path())

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(self.pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self.path())

    def set_style(self, kind):
        """kind: None | 'in' | 'out' | 'both' | 'dim'"""
        self.style = kind
        if kind in T.kind_color:
            col, w, z, op = QColor(T.kind_color[kind]), 3.4, Z_EDGE_HOT, 1.0
        elif kind == "dim":
            col, w, z, op = self.base_color, 1.3, Z_EDGE, T.dim_edge
        else:
            col, w, z, op = self.base_color, 1.5, Z_EDGE, 1.0
        pen = QPen(col, w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.arrow.setBrush(QBrush(col))
        self.arrow.setPen(QPen(col, 1))
        self.setZValue(z)
        self.setOpacity(op)
        self.update()

    def restyle(self):
        self.base_color = T.edge_color(self.data.color)
        self.set_style(self.style)


class ClusterItem(QGraphicsItem):
    kind = "cluster"

    def __init__(self, data: ClusterData, index: int):
        super().__init__()
        self.data, self.index = data, index
        self.rect = QRectF(data.rect)
        self.blocks: list[BlockItem] = []
        self.child_items: list[ClusterItem] = []
        self.parent_item: ClusterItem | None = None
        self.collapsible = data.kind == "diagram"
        self.collapsed = False
        self.cblock: CollapsedBlock | None = None
        self.setZValue(Z_CLUSTER + data.depth * 0.01)

    def set_rect(self, r: QRectF):
        if r != self.rect:
            self.prepareGeometryChange()
            self.rect = r

    def toggle_rect(self) -> QRectF:
        return QRectF(self.rect.left() + 6, self.rect.top() + 6, 20, 20)

    def refit(self):
        parts = [b.scene_rect() for b in self.blocks]
        parts += [
            c.cblock.scene_rect() if c.collapsed else c.rect for c in self.child_items
        ]
        if not parts:
            return
        base = parts[0]
        for r in parts[1:]:
            base = base.united(r)
        l, t, r, b = self.data.pad
        self.set_rect(base.adjusted(-l, -t, r, b))

    def member_blocks(self):
        """Blocks currently visible inside this diagram (folded children count as one block)."""
        if self.collapsed:
            return [self.cblock]
        out = list(self.blocks)
        for c in self.child_items:
            out += c.member_blocks()
        return out

    def raw_blocks(self):
        out = list(self.blocks)
        for c in self.child_items:
            out += c.raw_blocks()
        return out

    def descendants(self):
        out = []
        for c in self.child_items:
            out += [c] + c.descendants()
        return out

    def boundingRect(self):
        return self.rect.adjusted(-2, -2, 2, 2)

    def shape(self):
        """Only the frame + title band is clickable, so the inside stays free for rubber-banding."""
        head = max(28.0, self.data.pad[1])
        outer = QPainterPath()
        outer.addRect(self.rect)
        inner = QPainterPath()
        inner.addRect(self.rect.adjusted(7, head, -7, -7))
        return outer.subtracted(inner)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.data.kind == "diagram":
            fill = T.cluster_fill(self.data.depth)
            pen = QPen(QColor(T.cluster_border), 1.4)
        else:
            fill, pen = QColor(T.port_fill), QPen(QColor(T.port_border), 1.0)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(self.rect, 10, 10)
        for text, yoff, bold in self.data.texts:
            col = QColor(T.cluster_title if bold else T.cluster_sub)
            draw_centered(
                p,
                text,
                self.rect.center().x(),
                self.rect.top() + yoff,
                13 if bold else 12,
                bold,
                col,
                self.rect.width() - 70,
            )
        if self.collapsible:  # [-] fold button
            r = QRectF(self.rect.left() + 9, self.rect.top() + 9, 14, 14)
            p.setPen(QPen(QColor(T.fold_btn_stroke), 1.4))
            p.setBrush(QColor(T.glyph_fill))
            p.drawRoundedRect(r, 3, 3)
            p.drawLine(
                QPointF(r.left() + 3.5, r.center().y()),
                QPointF(r.right() - 3.5, r.center().y()),
            )


class Group:
    def __init__(self, name, color_idx):
        self.name, self.color_idx = name, color_idx
        self.members: set[BlockItem] = set()
        self.item: GroupItem | None = None


class GroupItem(QGraphicsItem):
    kind = "group"
    PAD, HEAD = 14.0, 24.0

    def __init__(self, group: Group):
        super().__init__()
        self.group = group
        self.rect = QRectF()
        self.setZValue(Z_GROUP)
        self.refit()

    def refit(self):
        vis = [m for m in self.group.members if m.isVisible()]
        self.setVisible(bool(vis))
        if not vis:
            return
        base = vis[0].scene_rect()
        for b in vis[1:]:
            base = base.united(b.scene_rect())
        r = base.adjusted(-self.PAD, -self.HEAD, self.PAD, self.PAD)
        if r != self.rect:
            self.prepareGeometryChange()
            self.rect = r

    def member_blocks(self):
        return [m for m in self.group.members if m.isVisible()]

    def boundingRect(self):
        return self.rect.adjusted(-3, -3, 3, 3)

    def shape(self):
        outer, inner = QPainterPath(), QPainterPath()
        outer.addRect(self.rect)
        inner.addRect(self.rect.adjusted(6, self.HEAD, -6, -6))
        return outer.subtracted(inner)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(GROUP_COLORS[self.group.color_idx % len(GROUP_COLORS)])
        fill = QColor(col)
        fill.setAlpha(T.group_alpha)
        p.setPen(QPen(col, 2, Qt.PenStyle.DashLine))
        p.setBrush(fill)
        p.drawRoundedRect(self.rect, 10, 10)
        p.setFont(font(13, True))
        p.setPen(col.lighter(140) if T.dark else col.darker(115))
        p.drawText(
            QRectF(
                self.rect.left() + 10,
                self.rect.top(),
                self.rect.width() - 20,
                self.HEAD,
            ),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            f"▣ {self.group.name}",
        )


# =============================================================================
# Scene
# =============================================================================
class DiagramScene(QtWidgets.QGraphicsScene):
    def __init__(self, diagram: Diagram):
        super().__init__()
        self.diagram = diagram
        self.blocks: dict[str, BlockItem] = {}
        self.cblocks: dict[str, CollapsedBlock] = {}
        self.edges: list[EdgeItem] = []
        self.clusters: list[ClusterItem] = []
        self.groups: list[Group] = []
        self.selection: set[BlockItem] = set()
        self.dim_unrelated = True
        self.dirty = False
        self.on_selection = lambda: None
        self._group_counter = 0
        self.by_sig: dict = {}
        self.cluster_by_sig: dict = {}
        self.setBackgroundBrush(QColor(T.bg))
        self._build()

    # -- construction -----------------------------------------------------
    def _build(self):
        d = self.diagram
        for key, nd in d.nodes.items():
            b = BlockItem(nd)
            self.blocks[key] = b
            self.addItem(b)
        for i, cd in enumerate(d.clusters):
            c = ClusterItem(cd, i)
            self.clusters.append(c)
            self.addItem(c)
        for i, cd in enumerate(d.clusters):
            for ch in cd.children:
                self.clusters[i].child_items.append(self.clusters[ch])
                self.clusters[ch].parent_item = self.clusters[i]
            for key in cd.nodes:
                self.clusters[i].blocks.append(self.blocks[key])
                self.blocks[key].cluster = self.clusters[i]
        # nicer labels for the grey "input ports" / "output ports" blocks
        for b in self.blocks.values():
            if not b.type_name and b.cluster is not None:
                par = b.cluster.data.parent
                nm = ""
                if par is not None:
                    nm = next(
                        (
                            t[len("name=") :]
                            for t, _, _ in d.clusters[par].texts
                            if t.startswith("name=")
                        ),
                        "",
                    )
                b.label = f"{nm or 'diagram'} {'inputs' if b.cluster.data.kind == 'in' else 'outputs'}"
        # one (initially hidden) folded block per sub-diagram
        for c in self.clusters:
            if c.collapsible:
                c.cblock = self._make_cblock(c)
                c.cblock.setVisible(False)
                self.cblocks[c.data.title] = c.cblock
                self.addItem(c.cblock)
        self._compute_signatures()
        for ed in d.edges:
            if ed.src in self.blocks and ed.dst in self.blocks:
                e = EdgeItem(ed, self.blocks[ed.src], self.blocks[ed.dst])
                self.edges.append(e)
                self.addItem(e)
        self.rebind_edges()
        self.update_scene_rect()

    def _make_cblock(self, c: ClusterItem) -> CollapsedBlock:
        ins, outs = [], []
        for ch in c.child_items:
            if ch.data.kind not in ("in", "out"):
                continue
            target = ins if ch.data.kind == "in" else outs
            for b in sorted(ch.blocks, key=lambda b: b.origin.y()):
                target += [b.cell_text(i) for i in range(len(b.cells))]
        return CollapsedBlock(c, ins, outs)

    def _compute_signatures(self):
        """Names that survive a regenerated SVG (Graphviz ids like 's160' may change between runs):
        the path of enclosing diagram names + block type + block name."""

        def seg(c):
            if c.data.kind != "diagram":
                return c.data.kind
            return "D:" + next(
                (t[5:] for t, _, _ in c.data.texts if t.startswith("name=")), ""
            )

        def path(c):
            parts = []
            while c is not None:
                parts.append(seg(c))
                c = c.parent_item
            return "/".join(reversed(parts))

        seen: dict = {}

        def unique(x):
            seen[x] = seen.get(x, 0) + 1
            return x if seen[x] == 1 else f"{x}#{seen[x]}"

        for c in self.clusters:
            c.sig = unique("cluster|" + path(c))
        for b in self.blocks.values():
            parent = path(b.cluster) if b.cluster is not None else ""
            if b.type_name:
                local = f"{b.type_name}:{b.sys_name}"
            elif b.cluster is not None and b.cluster.data.kind in ("in", "out"):
                local = "ports"
            else:
                local = "node:" + " ".join(t[0] for t in b.texts)
            b.sig = unique(f"{parent}|{local}")
        for c in self.clusters:
            if c.cblock is not None:
                c.cblock.sig = c.sig
        self.by_sig = {b.sig: b for b in self.iter_blocks()}
        self.cluster_by_sig = {c.sig: c for c in self.clusters}

    def block_sigs(self):
        return {b.sig for b in self.blocks.values()}

    def lookup_block(self, key):
        return self.by_sig.get(key) or self.blocks.get(key) or self.cblocks.get(key)

    def lookup_cluster(self, key):
        return self.cluster_by_sig.get(key) or next(
            (c for c in self.clusters if c.data.title == key), None
        )

    def apply_theme(self):
        self.setBackgroundBrush(QColor(T.bg))
        for b in self.iter_blocks():
            b.restyle()
        for e in self.edges:
            e.restyle()
        self.refresh_highlight()
        self.update()

    def iter_blocks(self):
        return list(self.blocks.values()) + list(self.cblocks.values())

    def visible_blocks(self):
        return [b for b in self.iter_blocks() if b.isVisible()]

    def block_by_key(self, key):
        return self.blocks.get(key) or self.cblocks.get(key)

    # -- geometry ---------------------------------------------------------
    def content_rect(self) -> QRectF:
        rects = [c.rect for c in self.clusters if c.isVisible()] + [
            b.scene_rect() for b in self.visible_blocks()
        ]
        if not rects:
            return QRectF(0, 0, 100, 100)
        r = rects[0]
        for x in rects[1:]:
            r = r.united(x)
        return r

    def update_scene_rect(self):
        self.setSceneRect(self.content_rect().adjusted(-1500, -1500, 1500, 1500))

    def relayout(self, moved=None):
        edges = self.edges if moved is None else {e for b in moved for e in b.edges}
        for e in edges:
            e.update_path()
        for c in sorted(self.clusters, key=lambda c: -c.data.depth):
            c.refit()
        for g in self.groups:
            g.item.refit()
        self.update()

    # -- folding sub-diagrams -----------------------------------------------
    def apply_visibility(self):
        for c in self.clusters:
            anc, p = False, c.parent_item
            while p is not None:
                anc |= p.collapsed
                p = p.parent_item
            c.setVisible(not anc and not c.collapsed)
            if c.cblock is not None:
                c.cblock.setVisible(c.collapsed and not anc)
        for b in self.blocks.values():
            hidden, p = False, b.cluster
            while p is not None:
                hidden |= p.collapsed
                p = p.parent_item
            b.setVisible(not hidden)

    def _rep(self, blk, cell, side):
        """Block (and port row) that currently represents `blk` on screen."""
        outer, cur = None, blk.cluster
        while cur is not None:
            if cur.collapsed:
                outer = cur
            cur = cur.parent_item
        if outer is None:
            return blk, cell
        cb = outer.cblock
        text = blk.cell_text(cell)
        return cb, (cb.out_map if side == "e" else cb.in_map).get(
            text
        ) if text else None

    def rebind_edges(self):
        for b in self.iter_blocks():
            b.edges = []
        for e in self.edges:
            vs, vsc = self._rep(e.src, e.src_cell, "e")
            vd, vdc = self._rep(e.dst, e.dst_cell, "w")
            e.bind(vs, vsc, vd, vdc)
            if not e.hidden:
                vs.edges.append(e)
                if vd is not vs:
                    vd.edges.append(e)

    def _after_fold(self):
        self.apply_visibility()
        self.rebind_edges()
        self.selection = {b for b in self.selection if b.isVisible()}
        self.relayout()
        self.update_scene_rect()
        self.dirty = True
        self.refresh_highlight()
        self.on_selection()

    def collapse_cluster(
        self, c: ClusterItem, pos: QPointF | None = None, refresh=True
    ):
        if not c.collapsible or c.collapsed:
            return
        cb = c.cblock
        cb.setPos(
            pos
            if pos is not None
            else c.rect.center() - QPointF(cb.rect.width() / 2, cb.rect.height() / 2)
        )
        c.collapsed = True
        if refresh:
            self._after_fold()
            self.set_selection({cb})

    def expand_cluster(self, c: ClusterItem, refresh=True):
        if not c.collapsed:
            return
        delta = c.cblock.scene_rect().center() - c.rect.center()
        if (
            delta.manhattanLength() > 0.5
        ):  # contents follow wherever the folded block was dragged
            for b in c.raw_blocks():
                b.setPos(b.pos() + delta)
            for d in c.descendants():
                if d.cblock is not None:
                    d.cblock.setPos(d.cblock.pos() + delta)
        c.collapsed = False
        if refresh:
            self._after_fold()
            self.set_selection(set())

    def toggle_cluster(self, c: ClusterItem):
        self.collapse_cluster(c) if not c.collapsed else self.expand_cluster(c)

    def collapse_all(self):
        """Fold every sub-diagram (keeps the outermost diagram open)."""
        targets = [
            c
            for c in self.clusters
            if c.collapsible and c.data.depth >= 1 and not c.collapsed
        ]
        centers = {c: c.rect.center() for c in targets}
        for c in sorted(targets, key=lambda c: -c.data.depth):
            cb = c.cblock
            self.collapse_cluster(
                c,
                centers[c] - QPointF(cb.rect.width() / 2, cb.rect.height() / 2),
                refresh=False,
            )
        self._after_fold()
        self.set_selection(set())

    def expand_all(self):
        for c in sorted(self.clusters, key=lambda c: c.data.depth):
            self.expand_cluster(c, refresh=False)
        self._after_fold()
        self.set_selection(set())

    def reveal(self, b: BlockItem):
        chain, p = [], b.cluster
        while p is not None:
            chain.append(p)
            p = p.parent_item
        for c in reversed(chain):  # outermost first
            self.expand_cluster(c, refresh=False)
        self._after_fold()

    # -- selection / highlighting ------------------------------------------
    def expand(self, blocks):
        out = set()
        for b in blocks:
            out |= {m for m in b.group.members if m.isVisible()} if b.group else {b}
        return out

    def set_selection(self, blocks):
        self.selection = set(blocks)
        self.refresh_highlight()
        self.on_selection()

    def refresh_highlight(self):
        sel = self.selection
        for b in self.iter_blocks():
            b.hot.clear()
        neighbors = set()
        for e in self.edges:
            if e.hidden:
                continue
            out, inc = e.vsrc in sel, e.vdst in sel
            if out or inc:
                kind = "both" if (out and inc) else "out" if out else "in"
                e.set_style(kind)
                col = QColor(T.kind_color[kind])
                col.setAlpha(T.hot_alpha)
                if e.vsrc_cell is not None:
                    e.vsrc.hot[e.vsrc_cell] = col
                if e.vdst_cell is not None:
                    e.vdst.hot[e.vdst_cell] = col
                neighbors |= {e.vsrc, e.vdst}
            else:
                e.set_style("dim" if (sel and self.dim_unrelated) else None)
        for b in self.iter_blocks():
            if b in sel:
                b.set_state("selected")
                b.setOpacity(1.0)
            elif b in neighbors:
                b.set_state("neighbor")
                b.setOpacity(1.0)
            else:
                b.set_state("normal")
                b.setOpacity(T.dim_block if (sel and self.dim_unrelated) else 1.0)
            b.update()

    def selection_summary(self) -> str:
        sel = self.selection
        if not sel:
            return (
                "Click a block to highlight its connections  ·  drag to move  ·  [-]/[+] fold diagrams  ·  "
                "Ctrl+G group  ·  Ctrl+drag / right-drag pan  ·  wheel zoom"
            )
        live = [e for e in self.edges if not e.hidden]
        n_out = {e for e in live if e.vsrc in sel and e.vdst not in sel}
        n_in = {e for e in live if e.vdst in sel and e.vsrc not in sel}
        head = next(iter(sel)).label if len(sel) == 1 else f"{len(sel)} blocks selected"
        return (
            f"{head}   ·   {len(n_out)} outgoing (orange), {len(n_in)} incoming (blue)"
        )

    def set_dim(self, on: bool):
        self.dim_unrelated = on
        self.refresh_highlight()

    # -- groups -------------------------------------------------------------
    def _add_group(self, name, members, color_idx=None):
        self._group_counter += 1
        g = Group(name, self._group_counter - 1 if color_idx is None else color_idx)
        g.members = set(members)
        for b in g.members:
            b.group = g
        g.item = GroupItem(g)
        self.addItem(g.item)
        self.groups.append(g)
        return g

    def make_group(self, blocks):
        blocks = self.expand(blocks)
        if len(blocks) < 2:
            return None
        self._drop_groups({b.group for b in blocks if b.group})
        g = self._add_group(f"Group {self._group_counter + 1}", blocks)
        self.dirty = True
        self.refresh_highlight()
        return g

    def _drop_groups(self, groups):
        for g in groups:
            for b in g.members:
                b.group = None
            self.removeItem(g.item)
            self.groups.remove(g)

    def ungroup(self, blocks):
        gs = {b.group for b in blocks if b.group}
        if gs:
            self._drop_groups(gs)
            self.dirty = True
            self.update()
        return bool(gs)

    # -- layout persistence -------------------------------------------------
    def layout_dict(self):
        return {
            "version": 3,
            "nodes": {b.sig: [b.pos().x(), b.pos().y()] for b in self.blocks.values()},
            "collapsed": {
                c.sig: [c.cblock.pos().x(), c.cblock.pos().y()]
                for c in self.clusters
                if c.collapsed
            },
            "groups": [
                {
                    "name": g.name,
                    "color": g.color_idx,
                    "members": sorted(b.sig for b in g.members),
                }
                for g in self.groups
            ],
        }

    def apply_layout(self, d) -> int:
        """Restore positions, folds and groups. Returns the number of blocks that were matched.

        Blocks that did not exist when the layout was saved (the SVG was regenerated with new
        blocks) are placed exactly like their nearest matched neighbour in the new SVG, so they
        land next to the blocks they sit beside."""
        absolute = d.get("version", 1) >= 3
        for c in self.clusters:
            c.collapsed = False
        matched: dict = {}
        for k, (x, y) in d.get("nodes", {}).items():
            blk = self.lookup_block(k)
            if blk is None or blk.is_collapsed or blk in matched:
                continue
            matched[blk] = QPointF(x, y) if absolute else blk.origin + QPointF(x, y)
        for blk, pos in matched.items():
            blk.setPos(pos)
        if matched:
            ctr = lambda q: q.origin + QPointF(q.rect.width() / 2, q.rect.height() / 2)
            for n in self.blocks.values():
                if n not in matched:
                    m = min(matched, key=lambda m: (ctr(m) - ctr(n)).manhattanLength())
                    n.setPos(n.origin + (matched[m] - m.origin))
        self._drop_groups(list(self.groups))
        for gd in d.get("groups", []):
            members = [
                m
                for m in (self.lookup_block(k) for k in gd["members"])
                if m is not None
            ]
            if len(members) >= 2:
                self._add_group(gd.get("name", "Group"), members, gd.get("color"))
        for title, (x, y) in d.get("collapsed", {}).items():
            c = self.lookup_cluster(title)
            if c is not None:
                self.collapse_cluster(c, QPointF(x, y), refresh=False)
        self._after_fold()
        self.set_selection(set())
        self.dirty = False
        return len(matched)

    def reset_layout(self):
        for c in self.clusters:
            c.collapsed = False
        for b in self.blocks.values():
            b.setPos(b.origin)
        self._drop_groups(list(self.groups))
        self._after_fold()
        self.set_selection(set())
        self.dirty = True


# =============================================================================
# View (all mouse interaction lives here)
# =============================================================================
class DiagramView(QtWidgets.QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(
            QtWidgets.QGraphicsView.ViewportUpdateMode.FullViewportUpdate
        )
        self.setMouseTracking(True)
        self.mode = None
        self._anim = None
        self.rename_cb = lambda group: None
        self._band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Shape.Rectangle, self.viewport()
        )

    # -- helpers ------------------------------------------------------------
    def pick(self, vpos: QPoint):
        for it in self.items(vpos):
            if (
                getattr(it, "kind", None) in ("block", "cluster", "group")
                and it.isVisible()
            ):
                return it
        return None

    ANIM_MS = 220  # duration of the jump to a searched / followed block (0 = instant)

    def stop_anim(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _apply_view(self, scale: float, center: QPointF):
        self.setTransform(QtGui.QTransform.fromScale(scale, scale))
        self.centerOn(center)

    def animate_to(self, scale: float, center: QPointF):
        """Glide to `scale` / `center`: zoom is interpolated logarithmically, the centre linearly."""
        self.stop_anim()
        s0 = self.transform().m11()
        c0 = self.mapToScene(self.viewport().rect().center())
        if not self.isVisible() or self.ANIM_MS <= 0:
            self._apply_view(scale, center)
            return
        if (
            abs(math.log(scale / s0)) < 0.01
            and (center - c0).manhattanLength() * s0 < 2
        ):
            return  # already there
        ls0, ls1 = math.log(s0), math.log(scale)
        anim = QtCore.QVariantAnimation(self)
        anim.setDuration(self.ANIM_MS)
        anim.setEasingCurve(
            QtCore.QEasingCurve.Type.OutCubic
        )  # fast start, soft landing
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(
            lambda t: self._apply_view(
                math.exp(ls0 + (ls1 - ls0) * float(t)), c0 + (center - c0) * float(t)
            )
        )
        anim.finished.connect(lambda: setattr(self, "_anim", None))
        self._anim = anim
        anim.start()

    def focus_rect(self, rect: QRectF):
        """Smoothly zoom / pan so that `rect` fills the view (never closer than 100 %)."""
        vp = self.viewport().rect()
        s = min(
            (vp.width() - 4) / max(rect.width(), 1.0),
            (vp.height() - 4) / max(rect.height(), 1.0),
        )
        self.animate_to(1.0 if s > 1.5 else max(s, 0.02), rect.center())

    def fit(self):
        self.stop_anim()
        sc = self.scene()
        if sc:
            self.fitInView(
                sc.content_rect().adjusted(-40, -40, 40, 40),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    # -- mouse --------------------------------------------------------------
    def mousePressEvent(self, e):
        self.stop_anim()
        sc: DiagramScene = self.scene()
        btn = e.button()
        vpos = e.position().toPoint()
        ctrl_pan = btn == Qt.MouseButton.LeftButton and bool(
            e.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        if ctrl_pan or btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.mode, self._last = "pan", e.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if btn != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)

        mods = e.modifiers()
        additive = bool(
            mods & Qt.KeyboardModifier.ShiftModifier
        )  # Ctrl is reserved for panning
        solo = bool(mods & Qt.KeyboardModifier.AltModifier)
        hit = self.pick(vpos)
        self._pending = None

        if hit is not None:  # fold / unfold buttons
            sp = self.mapToScene(vpos)
            if (
                hit.kind == "cluster"
                and hit.collapsible
                and hit.toggle_rect().contains(sp)
            ):
                sc.collapse_cluster(hit)
                return
            if (
                hit.kind == "block"
                and hit.is_collapsed
                and hit.toggle_rect().contains(sp)
            ):
                sc.expand_cluster(hit.cluster_item)
                return

        if hit is None:
            self.mode = "band"
            self._origin, self._additive = vpos, additive
            self._band.setGeometry(QRect(vpos, QtCore.QSize()))
            self._band.show()
            return

        if hit.kind == "block":
            members = {hit} if solo else sc.expand({hit})
        else:
            members = sc.expand(hit.member_blocks())

        if additive:
            sc.set_selection(
                sc.selection - members
                if members <= sc.selection
                else sc.selection | members
            )
            if not sc.selection:
                return
        elif not (members <= sc.selection):
            sc.set_selection(members)
        else:
            self._pending = members  # click without drag narrows the selection

        self.mode = "drag"
        self._moved = False
        self._start = self.mapToScene(vpos)
        self._press_vpos = vpos
        self._orig = {b: b.pos() for b in sc.selection}

    def mouseMoveEvent(self, e):
        sc: DiagramScene = self.scene()
        vpos = e.position().toPoint()
        if self.mode == "pan":
            d = e.position() - self._last
            self._last = e.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(d.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(d.y())
            )
        elif self.mode == "drag":
            if not self._moved and (vpos - self._press_vpos).manhattanLength() < 4:
                return
            self._moved = True
            delta = self.mapToScene(vpos) - self._start
            for b, p0 in self._orig.items():
                b.setPos(p0 + delta)
            sc.relayout(self._orig.keys())
        elif self.mode == "band":
            self._band.setGeometry(QRect(self._origin, vpos).normalized())
        else:
            hit = self.pick(vpos)
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if hit else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        sc: DiagramScene = self.scene()
        mode, self.mode = self.mode, None
        if mode == "pan":
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if mode == "drag":
            if self._moved:
                sc.dirty = True
                sc.update_scene_rect()
            elif self._pending:
                sc.set_selection(self._pending)
        elif mode == "band":
            self._band.hide()
            rect = self._band.geometry()
            if rect.width() < 4 and rect.height() < 4:
                if not self._additive:
                    sc.set_selection(set())
            else:
                area = self.mapToScene(rect).boundingRect()
                inside = {
                    b for b in sc.visible_blocks() if area.intersects(b.scene_rect())
                }
                inside = sc.expand(inside)
                sc.set_selection(sc.selection | inside if self._additive else inside)
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        sc: DiagramScene = self.scene()
        hit = self.pick(e.position().toPoint())
        if hit is not None:
            if hit.kind == "group":
                self.rename_cb(hit.group)
                return
            if hit.kind == "cluster" and hit.collapsible:
                sc.collapse_cluster(hit)
                return
            if hit.kind == "block" and hit.is_collapsed:
                sc.expand_cluster(hit.cluster_item)
                return
        self.mousePressEvent(e)

    def wheelEvent(self, e):
        self.stop_anim()
        dy = e.angleDelta().y()
        if not dy:
            return
        f = 1.0015**dy
        cur = self.transform().m11()
        f = min(max(f, 0.02 / cur), 4.0 / cur)
        self.scale(f, f)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.scene():
            self.scene().set_selection(set())
        else:
            super().keyPressEvent(e)


# =============================================================================
# Live values (written by drake_probe.run_debug)
# =============================================================================
def values_cache_path(name) -> Path:
    """Same location as drake_probe.values_path(): $XDG_CACHE_HOME or ~/.cache (Fedora/Linux)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "drake_diagram_viewer" / f"{Path(str(name)).stem}.values.json"


def nvim_cursor_path() -> Path:
    """Written by drake_cursor.lua (same XDG rule)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "drake_diagram_viewer" / "nvim_cursor.json"


def _n(x) -> str:
    try:
        return f"{x:.6g}"
    except (TypeError, ValueError):
        return str(x)


def _vec(xs) -> str:
    return "[" + ", ".join(_n(x) for x in xs) + "]"


def fmt_value(d) -> tuple:
    """(text, is_bad) for one summarised port value. is_bad flags NaN/inf and evaluation errors."""
    if not isinstance(d, dict):
        return str(d), False
    k = d.get("kind")
    if k == "scalar":
        v = d.get("value")
        return _n(v), isinstance(v, float) and not math.isfinite(v)
    if k == "array":
        text = _vec(d.get("values", [])) + (" …" if d.get("truncated") else "")
        shape = tuple(d.get("shape", []))
        if d.get("truncated"):
            text += f"   shape {shape}  min {_n(d.get('min'))}  max {_n(d.get('max'))}  ‖x‖ {_n(d.get('norm'))}"
        elif len(shape) > 1:
            text += f"   shape {shape}"
        bad = d.get("nonfinite", 0) > 0
        return text + (f"   ⚠ {d['nonfinite']} non-finite" if bad else ""), bad
    if k == "pose":
        return f"p = {_vec(d['p'])}   rpy = {_vec(d['rpy'])} rad", False
    if k == "rotation":
        return f"rpy = {_vec(d['rpy'])} rad", False
    if k == "spatial":
        return (
            f"{d.get('type', '')}  rot = {_vec(d['rot'])}   trans = {_vec(d['trans'])}",
            False,
        )
    if k == "image":
        return (
            f"{d.get('type', 'image')} {d['width']}×{d['height']}×{d['channels']}   "
            f"range [{_n(d['min'])}, {_n(d['max'])}]"
        ), False
    if k == "pointcloud":
        return f"point cloud, {d['n']} points", False
    if k == "list":
        parts = [fmt_value(x) for x in d.get("items", [])]
        more = f" … (+{d['n'] - len(parts)})" if d["n"] > len(parts) else ""
        return f"{d['n']} items: " + "  |  ".join(t for t, _ in parts) + more, any(
            b for _, b in parts
        )
    if k == "object":
        return d.get("type", "?") + (f"  ({d['info']})" if d.get("info") else ""), False
    if k == "unconnected":
        return "— unconnected", False
    if k == "skipped":
        return "(skipped)", False
    if k == "error":
        return f"error: {d.get('msg', '')}", True
    return str(d), False


# =============================================================================
# Main window
# =============================================================================
class MainWindow(QtWidgets.QMainWindow):
    POLL_MS = 500

    def __init__(
        self,
        svg_path=None,
        layout_path=None,
        dark=True,
        values_path=None,
        cursor_path=None,
    ):
        super().__init__()
        self.resize(1500, 900)
        self.setMinimumSize(
            480, 320
        )  # small minimum size: a tiling window manager can always fit it
        self.view = DiagramView()
        self.view.rename_cb = self.rename_group
        self.setCentralWidget(self.view)
        self.scene: DiagramScene | None = None
        self.svg_path: Path | None = None
        self.layout_path: Path | None = None
        self._loaded_stat = self._pending_stat = self._failed_stat = None
        self.values_override = Path(values_path) if values_path else None
        self.values_path: Path | None = None
        self.values: dict | None = None
        self._values_stat = None
        self.cursor_path = Path(cursor_path) if cursor_path else nvim_cursor_path()
        self._cursor_stat = self._file_stat(
            self.cursor_path
        )  # ignore whatever an earlier session left behind
        self._build_inspector()
        self._make_actions()
        self.dark_act.setChecked(dark)
        self.statusBar().showMessage("Open a Drake diagram SVG (Ctrl+O)")
        if svg_path:
            self.load(svg_path, layout_path)
        self._timer = QtCore.QTimer(self)  # watches the SVG for changes
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)
        self._cursor_timer = QtCore.QTimer(
            self
        )  # watches the Neovim cursor file (fast: it should feel live)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._cursor_timer.start(120)

    # -- actions ------------------------------------------------------------
    def _act(
        self, text, shortcut, slot, menu=None, checkable=False, toolbar=None, tip=None
    ):
        a = QtGui.QAction(text, self)
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        if tip:
            a.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
        if menu is not None:
            menu.addAction(a)
        if toolbar is not None:
            toolbar.addAction(a)
        self.addAction(a)
        return a

    def _make_actions(self):
        mb = self.menuBar()
        f, ed, lay, v, h = (
            mb.addMenu("&File"),
            mb.addMenu("&Edit"),
            mb.addMenu("&Layout"),
            mb.addMenu("&View"),
            mb.addMenu("&Help"),
        )
        tb = self.addToolBar("Main")
        tb.setMovable(False)

        self._act("&Open SVG…", "Ctrl+O", self.open_dialog, f)
        self._act("&Reload SVG from disk", "F5", lambda: self.reload(), f)
        self.auto_reload_act = self._act(
            "Auto-reload when the SVG &changes", None, lambda on: None, f, True
        )
        self.auto_reload_act.setChecked(True)
        f.addSeparator()
        self._act("&Save layout", "Ctrl+S", self.save_layout, f)
        self._act("&Export image (PNG / SVG)…", "Ctrl+E", self.export_dialog, f)
        f.addSeparator()
        self._act("&Quit", "Ctrl+Q", self.close, f)

        self._act(
            "Select &all",
            "Ctrl+A",
            lambda: (
                self.scene
                and self.scene.set_selection(set(self.scene.visible_blocks()))
            ),
            ed,
        )
        self._act("&Group selection", "Ctrl+G", self.group_sel, ed)
        self._act("&Ungroup selection", "Ctrl+Shift+G", self.ungroup_sel, ed)

        self._act(
            "⊟  Fold all sub-diagrams",
            "Ctrl+K",
            self.collapse_all,
            lay,
            toolbar=tb,
            tip="Fold every sub-diagram into a single block",
        )
        self._act(
            "⊞  Unfold all",
            "Ctrl+Shift+K",
            self.expand_all,
            lay,
            toolbar=tb,
            tip="Unfold all sub-diagrams",
        )
        lay.addSeparator()
        self._act("&Reset layout (original positions)", None, self.reset_layout, lay)

        self._act("&Fit to window", "F", lambda: self.view.fit(), v)
        self._act("Zoom &100%", "Ctrl+1", lambda: self.view.resetTransform(), v)
        dim = self._act(
            "&Dim unrelated blocks/arrows",
            "D",
            lambda on: self.scene and self.scene.set_dim(on),
            v,
            True,
        )
        dim.setChecked(True)
        self._act("&Controls", "F1", self.show_help, h)
        tog = self.inspector_dock.toggleViewAction()
        tog.setText("&Values inspector")
        tog.setShortcut(QtGui.QKeySequence("Ctrl+I"))
        v.addAction(tog)

        tb.addSeparator()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search blocks…  (Ctrl+F, Enter)")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(300)
        self.search.returnPressed.connect(self.do_search)
        tb.addWidget(self.search)
        self._act(
            "Search",
            "Ctrl+F",
            lambda: (self.search.setFocus(), self.search.selectAll()),
        )
        tb.addSeparator()
        self.dark_act = self._act(
            "☾  Dark mode",
            "Ctrl+Shift+D",
            self.set_dark,
            v,
            True,
            toolbar=tb,
            tip="Toggle dark / light mode",
        )
        self.follow_act = self._act(
            "⌖  Follow Neovim",
            "Ctrl+Shift+N",
            self._on_follow_toggled,
            v,
            True,
            toolbar=tb,
            tip="Select the block named like the word under the Neovim cursor",
        )
        self.follow_act.setChecked(
            QtCore.QSettings(APP_ORG, APP_NAME).value("follow_nvim", True, type=bool)
        )

    # -- theme --------------------------------------------------------------
    def set_dark(self, on):
        on = bool(on)
        set_theme(on)
        apply_app_theme(QtWidgets.QApplication.instance(), on)
        QtCore.QSettings(APP_ORG, APP_NAME).setValue("dark", on)
        if self.scene:
            self.scene.apply_theme()
        self.view.viewport().update()
        self.refresh_inspector()

    # -- values inspector -----------------------------------------------------
    def _build_inspector(self):
        dock = QtWidgets.QDockWidget("Values of the selected block(s)", self)
        dock.setObjectName("values_inspector")
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        self.values_status = QtWidgets.QLabel()
        self.values_status.setWordWrap(True)
        self.values_view = QtWidgets.QTextBrowser()
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.values_view.setFont(mono)
        lay.addWidget(self.values_status)
        lay.addWidget(self.values_view)
        dock.setWidget(w)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [440], Qt.Orientation.Horizontal)
        self.inspector_dock = dock

    def _on_selection(self, scene):
        self.statusBar().showMessage(scene.selection_summary())
        self.refresh_inspector()

    def _poll_values(self, force=False):
        p = self.values_path
        if p is None:
            return
        try:
            st = os.stat(p)
            stat = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stat = None
        if stat is not None and (force or stat != self._values_stat):
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                data = None  # being replaced right now - next tick
            if data is not None:
                self.values, self._values_stat = data, stat
                self.refresh_inspector()
        self._update_values_status()

    def _update_values_status(self):
        if self.values is None:
            msg, col = (
                f"No values yet – run the sim with drake_probe.run_debug().\nWatching {self.values_path}",
                T.name_text,
            )
        else:
            age = time.time() - self.values.get("wall_time", 0.0)
            stale = age > max(3 * self.values.get("period", 1.0), 3.0)
            msg = f"sim t = {self.values.get('sim_time', 0.0):.3f} s  ·  updated {age:.1f} s ago"
            col = T.kind_color["out"] if stale else T.name_text
            if stale:
                msg += "   ⚠ stale – is the simulation still running?"
        self.values_status.setText(msg)
        self.values_status.setStyleSheet(f"color: {col};")

    @staticmethod
    def _diagram_names(c):
        """Names of the enclosing diagram boxes, outermost first."""
        names = []
        while c is not None:
            if c.data.kind == "diagram":
                names.append(
                    next(
                        (t[5:] for t, _, _ in c.data.texts if t.startswith("name=")), ""
                    )
                )
            c = c.parent_item
        return names[::-1]

    def _lookup_values(self, blk):
        if not self.values or not blk.sys_name:
            return None
        owner = blk.cluster_item.parent_item if blk.is_collapsed else blk.cluster
        enc = self._diagram_names(owner)
        systems = self.values.get("systems", {})
        # the probe omits the root diagram, the SVG may or may not draw it as a box: try both
        for names in (enc + [blk.sys_name], enc[1:] + [blk.sys_name]):
            e = systems.get("/".join(names))
            if e is not None:
                return e
        return None

    def _block_html(self, b) -> str:
        muted = lambda t: f"<div style='color:{T.name_text}'>{html.escape(t)}</div>"
        out = [f"<h3 style='margin:10px 0 2px 0'>{html.escape(b.label)}</h3>"]
        e = self._lookup_values(b)
        if self.values is None:
            out.append(muted("No values file yet."))
        elif e is None:
            out.append(muted("No values for this block in the latest snapshot."))
        else:
            for title, key, col in (
                ("Inputs", "inputs", T.kind_color["in"]),
                ("Outputs", "outputs", T.kind_color["out"]),
            ):
                ports = e.get(key, {})
                out.append(
                    f"<div style='color:{col}; font-weight:bold; margin-top:6px'>{title} ({len(ports)})</div>"
                )
                if not ports:
                    out.append(muted("none"))
                    continue
                out.append("<table cellspacing='0' cellpadding='2'>")
                for pname, val in ports.items():
                    text, bad = fmt_value(val)
                    vcol = "#ff5555" if bad else T.text
                    out.append(
                        f"<tr><td valign='top' style='color:{T.name_text}'>{html.escape(pname)}&nbsp;&nbsp;</td>"
                        f"<td style='color:{vcol}'>{html.escape(text)}</td></tr>"
                    )
                out.append("</table>")
        return "".join(out)

    def refresh_inspector(self):
        sc = self.scene
        if sc is None:
            return
        blocks = sorted(sc.selection, key=lambda b: (b.pos().y(), b.pos().x()))
        sb = self.values_view.verticalScrollBar()
        keep = sb.value()
        if not blocks:
            self.values_view.setHtml(
                f"<p style='color:{T.name_text}'>Select a block to see the values of its "
                f"inputs and outputs.</p>"
            )
        else:
            shown = blocks[:8]
            body = "".join(self._block_html(b) for b in shown)
            if len(blocks) > len(shown):
                body += f"<p style='color:{T.name_text}'>… and {len(blocks) - len(shown)} more selected blocks.</p>"
            self.values_view.setHtml(body)
        sb.setValue(keep)

    # -- Neovim link ------------------------------------------------------------
    @staticmethod
    def _file_stat(p):
        try:
            st = os.stat(p)
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def _on_follow_toggled(self, on):
        QtCore.QSettings(APP_ORG, APP_NAME).setValue("follow_nvim", bool(on))
        if on:
            self._cursor_stat = (
                None  # apply the word that is under the cursor right now
            )
        self.statusBar().showMessage(
            "Following the Neovim cursor" if on else "Neovim cursor link off", 3000
        )

    def _poll_cursor(self):
        if self.scene is None or not self.follow_act.isChecked():
            return
        stat = self._file_stat(self.cursor_path)
        if stat is None or stat == self._cursor_stat:
            return
        self._cursor_stat = stat
        try:
            word = str(json.loads(self.cursor_path.read_text()).get("word", "")).strip()
        except (OSError, ValueError, AttributeError):
            return
        self.select_by_name(word)

    @staticmethod
    def _outer_folded(c):
        """Outermost folded diagram box at or above `c` (None if everything above is open)."""
        outer = None
        while c is not None:
            if c.collapsed:
                outer = c
            c = c.parent_item
        return outer

    @staticmethod
    def _short_type(t: str) -> str:
        """'__main__.GamePad' -> 'GamePad',  'drake::systems::Gain<double>' -> 'Gain'"""
        return re.split(r"[.:]+", t.split("<")[0])[-1]

    def select_by_name(self, word) -> bool:
        """Select the block(s) / diagram NAMED `word`; if there is none, the block(s) of the CLASS `word`.
        No match at all: the selection stays as it is."""
        sc = self.scene
        if sc is None or not word:
            return False

        def shown(
            b,
        ):  # hidden inside a folded diagram: that folded block stands in for it
            outer = self._outer_folded(b.cluster)
            return outer.cblock if outer is not None else b

        targets = {shown(b) for b in sc.blocks.values() if b.sys_name == word}
        for c in sc.clusters:
            if (
                c.collapsible
                and c.data.depth > 0
                and next(
                    (t[5:] for t, _, _ in c.data.texts if t.startswith("name=")), None
                )
                == word
            ):
                outer = self._outer_folded(c)
                targets |= (
                    {outer.cblock} if outer is not None else set(c.member_blocks())
                )
        targets = {b for b in targets if b.isVisible()}
        if not targets:  # not a system name: maybe a class name (GamePad, Gain, ...)
            targets = {
                shown(b)
                for b in sc.blocks.values()
                if b.type_name
                and (b.type_name == word or self._short_type(b.type_name) == word)
            }
            targets = {b for b in targets if b.isVisible()}
        if not targets:
            return False
        sc.set_selection(sc.expand(targets))
        self.zoom_to(targets)
        return True

    # -- file handling ------------------------------------------------------
    def _attach(self, scene: DiagramScene):
        old, self.scene = self.scene, scene
        scene.on_selection = lambda s=scene: self._on_selection(s)
        self.view.setScene(scene)
        if old is not None:
            old.deleteLater()

    def _stat(self):
        try:
            st = os.stat(self.svg_path)
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def load(self, svg_path, layout_path=None):
        try:
            diagram = Diagram(svg_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not read SVG", str(exc))
            return
        self.svg_path = Path(svg_path)
        self.layout_path = (
            Path(layout_path)
            if layout_path
            else self.svg_path.with_suffix(".layout.json")
        )
        self.values_path = self.values_override or values_cache_path(self.svg_path.stem)
        self.values, self._values_stat = None, None
        self._attach(DiagramScene(diagram))
        if self.layout_path.exists():
            try:
                self.scene.apply_layout(json.loads(self.layout_path.read_text()))
            except Exception as exc:
                print(f"Ignoring layout file: {exc}", file=sys.stderr)
        self._loaded_stat, self._pending_stat, self._failed_stat = (
            self._stat(),
            None,
            None,
        )
        self.setWindowTitle(f"Drake diagram viewer — {self.svg_path.name}")
        self.statusBar().showMessage(self.scene.selection_summary())
        QtCore.QTimer.singleShot(0, self.view.fit)
        self._poll_values(force=True)

    # -- auto-reload ----------------------------------------------------------
    def _poll(self):
        self._poll_values()
        if not self.svg_path or not self.auto_reload_act.isChecked():
            return
        st = self._stat()
        if st is None or st == self._loaded_stat or st == self._failed_stat:
            self._pending_stat = None
            return
        if (
            st != self._pending_stat
        ):  # changed: wait one more tick so a half-written file is not read
            self._pending_stat = st
            return
        if (
            self.view.mode is not None
        ):  # the user is dragging right now: try again on the next tick
            return
        self._pending_stat = None
        self.reload()

    def reload(self):
        """Re-read the SVG, reset the layout to the new SVG and carry over the highlighted blocks."""
        if not self.svg_path:
            return
        stat = self._stat()
        try:
            diagram = Diagram(self.svg_path)
        except Exception as exc:
            self._failed_stat = stat
            self.statusBar().showMessage(
                f"Could not read the updated SVG ({exc}) – keeping the previous version",
                8000,
            )
            return
        old = self.scene
        sel = {b.sig for b in old.selection} if old else set()
        old_sigs = old.block_sigs() if old else set()

        self._attach(DiagramScene(diagram))
        sc = self.scene
        # The old layout is deliberately NOT restored: the new SVG's own positions are shown
        # (folds and groups are cleared as well). Only the highlighted blocks are carried over.
        keep = {
            sc.by_sig[k] for k in sel if k in sc.by_sig and sc.by_sig[k].isVisible()
        }
        sc.set_selection(keep)
        sc.dirty = False
        if keep:  # keep the zoom, stay on what you were looking at
            area = None
            for b in keep:
                area = b.scene_rect() if area is None else area.united(b.scene_rect())
            self.view.centerOn(area.center())
        else:
            self.view.fit()
        self._loaded_stat, self._pending_stat, self._failed_stat = stat, None, None

        new_sigs = sc.block_sigs()
        added, removed = len(new_sigs - old_sigs), len(old_sigs - new_sigs)
        change = (
            f"{added} new, {removed} removed block(s)"
            if (added or removed)
            else "no structural changes"
        )
        when = datetime.datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage(
            f"{self.svg_path.name} changed on disk – reloaded at {when} ({change}); "
            f"layout reset, {len(keep)} highlighted block(s) kept",
            10000,
        )

    def open_dialog(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Drake diagram", "", "SVG (*.svg)"
        )
        if p and self.maybe_save():
            self.load(p)

    def save_layout(self):
        if not self.scene:
            return
        self.layout_path.write_text(json.dumps(self.scene.layout_dict(), indent=1))
        self.scene.dirty = False
        self.statusBar().showMessage(f"Layout saved to {self.layout_path}", 4000)

    def maybe_save(self) -> bool:
        if self.scene and self.scene.dirty:
            B = QtWidgets.QMessageBox.StandardButton
            r = QtWidgets.QMessageBox.question(
                self,
                "Unsaved layout",
                "Save the current layout first?",
                B.Save | B.Discard | B.Cancel,
            )
            if r == B.Cancel:
                return False
            if r == B.Save:
                self.save_layout()
        return True

    def closeEvent(self, ev):
        ev.accept() if self.maybe_save() else ev.ignore()

    def export_dialog(self):
        if not self.scene:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export image",
            str(self.svg_path.with_suffix(".png")),
            "PNG (*.png);;SVG (*.svg)",
        )
        if p:
            export_scene(self.scene, p)
            self.statusBar().showMessage(f"Exported {p}", 4000)

    # -- editing ------------------------------------------------------------
    def group_sel(self):
        if self.scene and not self.scene.make_group(self.scene.selection):
            self.statusBar().showMessage(
                "Select at least two blocks to group them", 3000
            )

    def ungroup_sel(self):
        if self.scene:
            self.scene.ungroup(self.scene.selection)

    def collapse_all(self):
        if self.scene:
            self.scene.collapse_all()
            self.view.fit()

    def expand_all(self):
        if self.scene:
            self.scene.expand_all()
            self.view.fit()

    def reset_layout(self):
        if self.scene:
            self.scene.reset_layout()
            self.view.fit()

    def rename_group(self, group: Group):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename group", "Group name:", text=group.name
        )
        if ok and name.strip():
            group.name = name.strip()
            self.scene.dirty = True
            group.item.update()

    def do_search(self):
        if not self.scene:
            return
        sc = self.scene
        q = self.search.text().strip().lower()
        if not q:
            return

        def match(b):
            return (
                q in " ".join([b.label, b.data.key] + [t[0] for t in b.texts]).lower()
            )

        hits = [b for b in sc.blocks.values() if match(b)] + [
            b for b in sc.cblocks.values() if b.isVisible() and match(b)
        ]
        if not hits:
            self.statusBar().showMessage(f"No block matches “{q}”", 3000)
            return
        for b in [
            b for b in hits if not b.isVisible()
        ]:  # unfold diagrams hiding a match
            sc.reveal(b)
        hits = [b for b in hits if b.isVisible()]
        sc.set_selection(sc.expand(hits))
        self.zoom_to(hits)

    def zoom_to(self, blocks):
        """Glide onto `blocks` (never closer than 100 %) - used by the search and by the Neovim link."""
        area = None
        for b in blocks:
            area = b.scene_rect() if area is None else area.united(b.scene_rect())
        if area is None:
            return
        self.view.focus_rect(area.adjusted(-150, -150, 150, 150))

    def show_help(self):
        QtWidgets.QMessageBox.information(
            self,
            "Controls",
            (
                "<b>Click</b> a block – highlight it and all its arrows (orange out, blue in)<br>"
                "<b>Drag</b> a block – move it (arrows follow, diagram boxes resize)<br>"
                "<b>Drag a diagram box border/title</b> – move that whole sub-diagram<br>"
                "<b>[-] in a diagram's title bar</b> (or double-click the title) – fold it into one block<br>"
                "<b>[+] on a folded block</b> (or double-click it) – unfold<br>"
                "<b>Ctrl+K</b> fold all · <b>Ctrl+Shift+K</b> unfold all<br>"
                "<b>Drag on empty space</b> – rubber-band select; <b>Shift+click</b> adds<br>"
                "<b>Alt+click</b> – select one block, ignoring its group<br>"
                "<b>Ctrl+G / Ctrl+Shift+G</b> – group / ungroup selection (double-click label to rename)<br>"
                "<b>Ctrl+drag</b> (or right/middle drag) – pan, <b>wheel</b> – zoom, <b>F</b> – fit<br>"
                "<b>Ctrl+F</b> – search blocks (unfolds diagrams hiding a match), <b>D</b> – toggle dimming<br>"
                "<b>Ctrl+S</b> – save layout next to the SVG (auto-loaded next time)<br>"
                "<b>Ctrl+E</b> – export PNG / SVG<br>"
                "<b>F5</b> – reload the SVG now (also automatic when the file changes; layout resets, highlight stays)<br>"
                "<b>Ctrl+Shift+N</b> – follow the word under the Neovim cursor (drake_cursor.lua), on / off<br>"
                "<b>Ctrl+I</b> – show / hide the values inspector (fed by drake_probe.run_debug)<br>"
                "<b>Ctrl+Shift+D</b> – dark / light mode"
            ),
        )


# =============================================================================
# Export / CLI
# =============================================================================
def export_scene(scene: DiagramScene, path: str, scale: float = 1.0):
    src = scene.content_rect().adjusted(-30, -30, 30, 30)
    if path.lower().endswith(".svg"):
        from PySide6.QtSvg import QSvgGenerator  # type: ignore

        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(src.size().toSize())
        gen.setViewBox(QRectF(QPointF(0, 0), src.size()))
        p = QPainter(gen)
        scene.render(p, QRectF(QPointF(0, 0), src.size()), src)
        p.end()
        return
    img = QtGui.QImage(
        int(src.width() * scale),
        int(src.height() * scale),
        QtGui.QImage.Format.Format_ARGB32,
    )
    img.fill(QColor(T.bg))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    scene.render(p, QRectF(img.rect()), src)
    p.end()
    img.save(path)


_ORIG_STYLE: list = []


def dark_palette() -> QtGui.QPalette:
    R, G = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
    p = QtGui.QPalette()
    for role, col in [
        (R.Window, "#21222c"),
        (R.WindowText, "#f8f8f2"),
        (R.Base, "#282a36"),
        (R.AlternateBase, "#21222c"),
        (R.ToolTipBase, "#44475a"),
        (R.ToolTipText, "#f8f8f2"),
        (R.Text, "#f8f8f2"),
        (R.Button, "#343746"),
        (R.ButtonText, "#f8f8f2"),
        (R.BrightText, "#ff5555"),
        (R.Link, "#8be9fd"),
        (R.Highlight, "#bd93f9"),
        (R.HighlightedText, "#282a36"),
        (R.PlaceholderText, "#6272a4"),
        (R.Light, "#44475a"),
        (R.Midlight, "#3a3d4d"),
        (R.Dark, "#191a21"),
        (R.Mid, "#21222c"),
        (R.Shadow, "#000000"),
    ]:
        p.setColor(role, QColor(col))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, QColor("#6272a4"))
    return p


def apply_app_theme(app, dark: bool):
    if dark:
        app.setStyle("Fusion")
        app.setPalette(dark_palette())
    else:
        app.setStyle(_ORIG_STYLE[0] if _ORIG_STYLE else "Fusion")
        app.setPalette(app.style().standardPalette())


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Interactive Drake diagram viewer (Graphviz SVG input)"
    )
    ap.add_argument(
        "svg",
        nargs="?",
        help="SVG produced by pydrake (e.g. from GenerateSvg / graphviz)",
    )
    ap.add_argument(
        "--layout", help="layout JSON (default: <svg>.layout.json if it exists)"
    )
    ap.add_argument(
        "--cursor",
        help="file written by drake_cursor.lua (default: ~/.cache/drake_diagram_viewer/nvim_cursor.json)",
    )
    ap.add_argument(
        "--values",
        help="values JSON written by drake_probe (default: ~/.cache/drake_diagram_viewer/<svg name>.values.json)",
    )
    ap.add_argument(
        "--theme",
        choices=["dark", "light"],
        help="override the saved theme (default: dark)",
    )
    ap.add_argument(
        "--export", metavar="FILE", help="render to FILE (.png/.svg) and exit"
    )
    ap.add_argument("--scale", type=float, default=1.0, help="PNG scale for --export")
    ap.add_argument(
        "--collapse",
        action="store_true",
        help="fold all sub-diagrams first (with --export)",
    )
    args = ap.parse_args(argv)

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(
        "drake-diagram-viewer"
    )  # WM_CLASS / Wayland app_id instead of a generic "python3"
    app.setDesktopFileName("drake-diagram-viewer")
    _ORIG_STYLE.append(app.style().objectName())
    dark = QtCore.QSettings(APP_ORG, APP_NAME).value("dark", True, type=bool)
    if args.theme:
        dark = args.theme == "dark"
    set_theme(dark)
    apply_app_theme(app, dark)

    if args.export:
        if not args.svg:
            ap.error("--export needs an SVG")
        sc = DiagramScene(Diagram(args.svg))
        lp = (
            Path(args.layout)
            if args.layout
            else Path(args.svg).with_suffix(".layout.json")
        )
        if lp.exists():
            sc.apply_layout(json.loads(lp.read_text()))
        if args.collapse:
            sc.collapse_all()
        export_scene(sc, args.export, args.scale)
        print(f"wrote {args.export}")
        return 0
    win = MainWindow(
        args.svg, args.layout, dark, values_path=args.values, cursor_path=args.cursor
    )
    win.show()
    if not args.svg:
        QtCore.QTimer.singleShot(0, win.open_dialog)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
