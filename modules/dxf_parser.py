"""
DXF PID 图解析器
从二维PID图中提取：管路标注、部件编号、部件类型、位号、几何走向、图块插入位置
"""
import re
import ezdxf
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextEntity:
    """DXF中的文本实体"""
    text: str
    layer: str
    x: float
    y: float
    z: float = 0.0
    height: float = 2.5
    rotation: float = 0.0


@dataclass
class ComponentNode:
    """PID图中的一个组件节点（阀门/接头/传感器等）"""
    node_id: str  # 部件编号，如 "01"
    component_type: str  # AV/MV/DBV/PT 等
    tag: str = ""  # 位号，如 KS01A
    x: float = 0.0
    y: float = 0.0
    pipe_size: str = ""  # 关联的管径
    medium: str = ""  # 介质
    raw_texts: list = field(default_factory=list)


@dataclass
class PipeSegment:
    """管路段"""
    medium: str  # 介质: Chemical In/Out, CDA, EXH 等
    size: str  # 管径: 1", 1/2", 40A, 50A
    size_mm: float = 0.0  # 毫米管径
    points: list = field(default_factory=list)  # [(x,y), ...]
    layer: str = ""


@dataclass
class InsertEntity:
    """图块插入实体"""
    block_name: str
    x: float
    y: float
    z: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    layer: str = ""


@dataclass
class PIDDiagram:
    """解析后的PID图数据"""
    texts: list = field(default_factory=list)  # 所有文本
    components: list = field(default_factory=list)  # 组件节点
    pipes: list = field(default_factory=list)  # 管路段
    inserts: list = field(default_factory=list)  # 图块插入
    lines: list = field(default_factory=list)  # 线段
    polylines: list = field(default_factory=list)  # 多段线
    extents: tuple = (0, 0, 0, 0)  # 图纸范围 min_x, min_y, max_x, max_y
    layer_names: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "extents": self.extents,
            "layer_count": len(self.layer_names),
            "layer_names": self.layer_names[:30],
            "text_count": len(self.texts),
            "component_count": len(self.components),
            "pipe_count": len(self.pipes),
            "insert_count": len(self.inserts),
            "line_count": len(self.lines),
            "polyline_count": len(self.polylines),
            "components": [
                {
                    "node_id": c.node_id,
                    "type": c.component_type,
                    "tag": c.tag,
                    "x": round(c.x, 2),
                    "y": round(c.y, 2),
                    "pipe_size": c.pipe_size,
                    "medium": c.medium,
                }
                for c in self.components
            ],
            "pipes": [
                {
                    "medium": p.medium,
                    "size": p.size,
                    "size_mm": p.size_mm,
                    "point_count": len(p.points),
                    "points": [(round(x, 2), round(y, 2)) for x, y in p.points[:20]],
                }
                for p in self.pipes
            ],
            "texts_sample": [
                {"text": t.text, "layer": t.layer, "x": round(t.x, 2), "y": round(t.y, 2)}
                for t in self.texts[:50]
            ],
        }


# ---- 管径转换表 ----
SIZE_TO_MM = {
    '1/4"': 6.35, '1/2"': 12.7, '3/4"': 19.05, '1"': 25.4,
    '1-1/4"': 31.75, '1-1/2"': 38.1, '2"': 50.8, '3"': 76.2, '4"': 101.6,
    '15A': 15, '20A': 20, '25A': 25, '32A': 32, '40A': 40, '50A': 50,
    '65A': 65, '80A': 80, '100A': 100,
}

# 介质类型映射
MEDIUM_MAP = {
    'Chemical In': 'Chemical In',
    'Chemical Out': 'Chemical Out',
    'CDA': 'CDA',
    'EXH': 'EXH',
    'PN2': 'PN2',
    'CV': 'CV',
}

# 组件类型映射
COMPONENT_TYPE_MAP = {
    'AV': '自动阀', 'MV': '手动阀', 'DBV': '隔膜阀',
    'PT': '压力传感器', 'PI': '压力表',
    'KS': '流量传感器', 'FS': '流量开关',
}


def parse_size_to_mm(size_str: str) -> float:
    """将管径字符串转换为毫米"""
    s = size_str.strip()
    if s in SIZE_TO_MM:
        return SIZE_TO_MM[s]
    # 尝试解析
    m = re.match(r'(\d+)A$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'(\d+(?:-\d+)?/\d+)"$', s)
    if m:
        parts = m.group(1).split('-')
        if len(parts) == 2:
            num, den = parts[1].split('/')
            return float(parts[0]) * 25.4 + float(num) / float(den) * 25.4
        else:
            num, den = parts[0].split('/')
            return float(num) / float(den) * 25.4
    m = re.match(r'(\d+)"$', s)
    if m:
        return float(m.group(1)) * 25.4
    return 0.0


def parse_dxf(file_path: str) -> PIDDiagram:
    """解析DXF文件，返回结构化的PID图数据"""
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    diagram = PIDDiagram()

    # 图纸范围
    try:
        extmin = doc.header.get('$EXTMIN', (0, 0, 0))
        extmax = doc.header.get('$EXTMAX', (0, 0, 0))
        diagram.extents = (
            float(extmin[0]), float(extmin[1]),
            float(extmax[0]), float(extmax[1])
        )
    except Exception:
        pass

    # 收集图层名
    diagram.layer_names = [layer.dxf.name for layer in doc.layers]

    # 收集所有文本
    all_texts = []
    for entity in msp:
        etype = entity.dxftype()

        if etype == 'TEXT':
            t = TextEntity(
                text=entity.dxf.text,
                layer=entity.dxf.layer,
                x=float(entity.dxf.insert[0]),
                y=float(entity.dxf.insert[1]),
                z=float(entity.dxf.insert[2]) if len(entity.dxf.insert) > 2 else 0.0,
                height=float(getattr(entity.dxf, 'height', 2.5)),
                rotation=float(getattr(entity.dxf, 'rotation', 0.0)),
            )
            all_texts.append(t)

        elif etype == 'MTEXT':
            text_content = entity.text
            # MTEXT可能有多行
            for line in text_content.split('\\P'):
                line = line.strip()
                if line:
                    t = TextEntity(
                        text=line,
                        layer=entity.dxf.layer,
                        x=float(entity.dxf.insert[0]),
                        y=float(entity.dxf.insert[1]),
                        z=float(entity.dxf.insert[2]) if len(entity.dxf.insert) > 2 else 0.0,
                        height=float(getattr(entity.dxf, 'char_height', 2.5)),
                    )
                    all_texts.append(t)

        elif etype == 'INSERT':
            ins = InsertEntity(
                block_name=entity.dxf.name,
                x=float(entity.dxf.insert[0]),
                y=float(entity.dxf.insert[1]),
                z=float(entity.dxf.insert[2]) if len(entity.dxf.insert) > 2 else 0.0,
                scale_x=float(getattr(entity.dxf, 'xscale', 1.0)),
                scale_y=float(getattr(entity.dxf, 'yscale', 1.0)),
                rotation=float(getattr(entity.dxf, 'rotation', 0.0)),
                layer=entity.dxf.layer,
            )
            diagram.inserts.append(ins)

        elif etype == 'LINE':
            diagram.lines.append({
                'start': (float(entity.dxf.start[0]), float(entity.dxf.start[1])),
                'end': (float(entity.dxf.end[0]), float(entity.dxf.end[1])),
                'layer': entity.dxf.layer,
            })

        elif etype in ('LWPOLYLINE', 'POLYLINE'):
            pts = []
            if etype == 'LWPOLYLINE':
                for p in entity.get_points(format='xy'):
                    pts.append((float(p[0]), float(p[1])))
            else:
                for v in entity.vertices:
                    pts.append((float(v.dxf.location[0]), float(v.dxf.location[1])))
            if pts:
                diagram.polylines.append({
                    'points': pts,
                    'layer': entity.dxf.layer,
                    'closed': getattr(entity, 'closed', False),
                })

    diagram.texts = all_texts

    # 分析文本，分类提取组件和管路标注
    _extract_components(diagram)
    _extract_pipes(diagram)

    return diagram


def _extract_components(diagram: PIDDiagram):
    """从文本中提取组件节点（阀门、传感器等）
    支持两种PID标注格式：
    1. 分离式（海神VMB）：部件编号"01"和类型"AV"分开，在不同图层
    2. 合并式（8A/8B）：类型+编号合在一起"AV01"，在NOTE等图层
    """
    number_texts = []  # 部件编号文本 (01, 02, ...)
    type_texts = []    # 类型代码文本 (AV, MV, DBV, ...)
    tag_texts = []     # 位号文本 (KS01A, KS01B, ...)
    size_texts = []    # 管径文本
    medium_texts = []  # 介质文本
    combined_texts = []  # 合并式文本 (AV01, MV00, CV01, ...)

    # 类型代码集合（用于匹配合并式文本）
    type_prefixes = {'AV', 'MV', 'DBV', 'PT', 'PI', 'KS', 'FS', 'CV', 'SV', 'BV'}

    for t in diagram.texts:
        layer = t.layer
        text = t.text.strip()

        if not text:
            continue

        # 合并式文本检测：AV01, MV00, CV01 等（类型代码+数字）
        m = re.match(r'^([A-Z]{2,3})(\d{2,3})$', text)
        if m:
            prefix = m.group(1)
            if prefix in type_prefixes or prefix in COMPONENT_TYPE_MAP:
                combined_texts.append(t)
                continue

        # 部件编号
        if '部件编号' in layer or re.match(r'^\d{2}$', text):
            if re.match(r'^\d{2}$', text):
                number_texts.append(t)
            elif text in COMPONENT_TYPE_MAP or text in type_prefixes:
                type_texts.append(t)
            elif re.match(r'^[A-Z]{2}\d+', text):  # 位号如KS01A
                tag_texts.append(t)
            else:
                # 可能是编号或类型
                if text.isdigit():
                    number_texts.append(t)
                else:
                    type_texts.append(t)
        elif '管路标注' in layer:
            if text in MEDIUM_MAP or 'Chemical' in text or text in ('CDA', 'EXH', 'PN2'):
                medium_texts.append(t)
            elif re.match(r'^\d', text) and ('"' in text or 'A' in text):
                size_texts.append(t)
        elif '位号' in layer:
            tag_texts.append(t)
        elif 'NOTE' in layer.upper() or 'CHDS' in layer.upper():
            # NOTE图层在8A/8B中包含管径、介质、合并式组件编号
            if text in MEDIUM_MAP or 'Chemical' in text or text in ('CDA', 'EXH', 'PN2', 'EXH'):
                medium_texts.append(t)
            elif re.match(r'^\d', text) and ('"' in text or re.match(r'^\d+A', text)):
                size_texts.append(t)
            elif text.startswith('STICK'):
                pass  # 忽略STICK标注
            elif re.match(r'^[A-Z]{2,3}\d{2,3}$', text):
                # 已在上面combined_texts处理了，这里跳过
                pass
            elif re.match(r'^\d{2}$', text):
                number_texts.append(t)
            else:
                # 其他NOTE文本尝试作为介质或管径
                if re.match(r'^\d+["A]', text):
                    size_texts.append(t)
        elif layer == '0':
            # Layer 0 在8A/8B中包含DBV、管径等
            if text in type_prefixes or text in COMPONENT_TYPE_MAP:
                type_texts.append(t)
            elif re.match(r'^\d', text) and ('"' in text or 'A' in text):
                size_texts.append(t)
            elif re.match(r'^[A-Z]{2,3}\d{2,3}$', text):
                combined_texts.append(t)
            elif text in MEDIUM_MAP or text in ('CDA', 'EXH', 'PN2'):
                medium_texts.append(t)
        elif 'EXH' in layer.upper() or text in ('CDA', 'EXH', 'PN2'):
            medium_texts.append(t)

    # 处理合并式文本：AV01 -> type=AV, node_id=01
    for ct in combined_texts:
        m = re.match(r'^([A-Z]{2,3})(\d{2,3})$', ct.text)
        if m:
            comp_type = m.group(1)
            comp_num = m.group(2)

            # 找最近的管径和介质
            best_size = ""
            best_size_dist = 50.0
            for size_t in size_texts:
                dist = ((ct.x - size_t.x) ** 2 + (ct.y - size_t.y) ** 2) ** 0.5
                if dist < best_size_dist:
                    best_size_dist = dist
                    best_size = size_t.text

            best_medium = ""
            best_medium_dist = 80.0
            for med_t in medium_texts:
                dist = ((ct.x - med_t.x) ** 2 + (ct.y - med_t.y) ** 2) ** 0.5
                if dist < best_medium_dist:
                    best_medium_dist = dist
                    best_medium = med_t.text

            comp = ComponentNode(
                node_id=comp_num,
                component_type=comp_type,
                x=ct.x,
                y=ct.y,
                pipe_size=best_size,
                medium=best_medium,
                raw_texts=[ct.text],
            )
            diagram.components.append(comp)

    # 处理分离式文本（海神VMB格式）：按位置将编号和类型代码配对
    used_numbers = set()
    used_types = set()

    for num_t in number_texts:
        best_type = None
        best_dist = 100.0  # 最大配对距离

        for type_t in type_texts:
            if id(type_t) in used_types:
                continue
            dist = ((num_t.x - type_t.x) ** 2 + (num_t.y - type_t.y) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_type = type_t

        # 找最近的管径和介质
        best_size = ""
        best_size_dist = 50.0
        for size_t in size_texts:
            dist = ((num_t.x - size_t.x) ** 2 + (num_t.y - size_t.y) ** 2) ** 0.5
            if dist < best_size_dist:
                best_size_dist = dist
                best_size = size_t.text

        best_medium = ""
        best_medium_dist = 80.0
        for med_t in medium_texts:
            dist = ((num_t.x - med_t.x) ** 2 + (num_t.y - med_t.y) ** 2) ** 0.5
            if dist < best_medium_dist:
                best_medium_dist = dist
                best_medium = med_t.text

        # 找最近的位号
        best_tag = ""
        best_tag_dist = 80.0
        for tag_t in tag_texts:
            dist = ((num_t.x - tag_t.x) ** 2 + (num_t.y - tag_t.y) ** 2) ** 0.5
            if dist < best_tag_dist:
                best_tag_dist = dist
                best_tag = tag_t.text

        comp = ComponentNode(
            node_id=num_t.text,
            component_type=best_type.text if best_type else "",
            tag=best_tag,
            x=num_t.x,
            y=num_t.y,
            pipe_size=best_size,
            medium=best_medium,
            raw_texts=[num_t.text, best_type.text if best_type else ""],
        )
        diagram.components.append(comp)

        if best_type:
            used_types.add(id(best_type))

    # 没有编号但有类型的（独立组件）
    for type_t in type_texts:
        if id(type_t) not in used_types:
            comp = ComponentNode(
                node_id="?",
                component_type=type_t.text,
                x=type_t.x,
                y=type_t.y,
            )
            diagram.components.append(comp)


def _extract_pipes(diagram: PIDDiagram):
    """从文本和线段中提取管路段"""
    # 按图层和管径分组线段
    pipe_layers = set()
    for layer_name in diagram.layer_names:
        if '管路' in layer_name or 'PID' in layer_name or 'Pipe' in layer_name.lower():
            pipe_layers.add(layer_name)

    # 收集管径标注，按介质分组
    size_by_pos = {}  # (approx_x, approx_y) -> size
    medium_by_pos = {}

    for t in diagram.texts:
        text = t.text.strip()
        if not text:
            continue
        if '管路标注' in t.layer:
            if text in MEDIUM_MAP or 'Chemical' in text or text in ('CDA', 'EXH', 'PN2'):
                medium_by_pos[(round(t.x, 0), round(t.y, 0))] = text
            elif re.match(r'^\d', text) and ('"' in text or re.match(r'^\d+A$', text)):
                size_by_pos[(round(t.x, 0), round(t.y, 0))] = text

    # 从多段线和线段构建管路
    # 简化：将每条polyline作为一个管路段
    for pl in diagram.polylines:
        layer = pl['layer']
        pts = pl['points']
        if len(pts) < 2:
            continue

        # 找最近的管径和介质标注
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)

        best_size = ""
        best_dist = 200.0
        for (sx, sy), size in size_by_pos.items():
            d = ((cx - sx) ** 2 + (cy - sy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_size = size

        best_medium = ""
        best_dist = 300.0
        for (mx, my), medium in medium_by_pos.items():
            d = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_medium = medium

        if not best_medium:
            best_medium = "Unknown"

        size_mm = parse_size_to_mm(best_size) if best_size else 0.0

        pipe = PipeSegment(
            medium=best_medium,
            size=best_size,
            size_mm=size_mm,
            points=pts,
            layer=layer,
        )
        diagram.pipes.append(pipe)

    # 如果没有polyline管路，从线段中构建
    if not diagram.pipes:
        for line in diagram.lines:
            layer = line['layer']
            if layer in pipe_layers or '管路' in layer:
                pts = [line['start'], line['end']]
                pipe = PipeSegment(
                    medium="Unknown",
                    size="",
                    size_mm=0.0,
                    points=pts,
                    layer=layer,
                )
                diagram.pipes.append(pipe)
