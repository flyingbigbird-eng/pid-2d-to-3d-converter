"""
点料表(BOM)解析器
解析xls点料表，按类别分类物料（阀门/接头/管道/壳体/传感器等）
"""
import re
import xlrd
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Material:
    """单个物料条目"""
    name: str           # 物料名称
    material: str = ""  # 材质
    spec: str = ""      # 规格描述
    quantity: float = 0 # 需求数量
    unit: str = ""      # 单位
    supplier: str = ""  # 推荐供应商/品牌
    k3_code: str = ""   # k3代码
    remark: str = ""    # 备注
    category: str = ""  # 分类: valve/fitting/pipe/sensor/shell/accessory
    size: str = ""      # 从名称中提取的管径
    size_mm: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "material": self.material,
            "spec": self.spec,
            "quantity": self.quantity,
            "unit": self.unit,
            "supplier": self.supplier,
            "k3_code": self.k3_code,
            "category": self.category,
            "size": self.size,
            "size_mm": self.size_mm,
        }


@dataclass
class BOMSheet:
    """点料表中的一个sheet"""
    name: str
    materials: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "material_count": len(self.materials),
            "materials": [m.to_dict() for m in self.materials],
        }


@dataclass
class BOMDocument:
    """完整的点料表文档"""
    sheets: list = field(default_factory=list)
    all_materials: list = field(default_factory=list)

    def to_dict(self) -> dict:
        # 按分类统计
        category_stats = {}
        for m in self.all_materials:
            cat = m.category or "other"
            if cat not in category_stats:
                category_stats[cat] = {"count": 0, "total_qty": 0}
            category_stats[cat]["count"] += 1
            category_stats[cat]["total_qty"] += m.quantity

        return {
            "sheet_count": len(self.sheets),
            "total_materials": len(self.all_materials),
            "category_stats": category_stats,
            "sheets": [s.to_dict() for s in self.sheets],
        }


# ---- 分类规则 ----
CATEGORY_RULES = [
    # (分类, 关键词列表)
    ("valve", ["隔膜阀", "球阀", "针阀", "调压阀", "节流阀", "蝶阀", "止回阀", "阀"]),
    ("fitting", ["union", "三通", "弯头", "大小头", "堵头", "穿板", "转接头", "法兰", "接头", "入珠", "卡套", "Cap", "nut", "gasket", "垫片"]),
    ("pipe", ["管道", "Tube", "管路"]),
    ("sensor", ["传感器", "压力表", "流量", "PT", "PI", "压力变送", "温度"]),
    ("shell", ["壳体", "BOX壳", "侧板", "顶板", "底板", "面板"]),
    ("accessory", ["支架", "支撑", "减震", "搭扣", "卡线", "快插", "气管"]),
]


def classify_material(name: str) -> str:
    """根据物料名称分类"""
    name_lower = name.lower()
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw.lower() in name_lower:
                return category
    return "other"


def extract_size(name: str) -> tuple:
    """从物料名称中提取管径
    返回 (size_str, size_mm)
    """
    # 匹配各种管径格式
    # 1/2", 3/4", 1", 1-1/4", 1-1/2"
    patterns = [
        r'(\d+-\d+/\d+)"',       # 1-1/2"
        r'(\d+/\d+)"',            # 1/2"
        r'(\d+)"',                # 1"
        r'(\d+)A',                # 40A, 50A
        r'(\d+)x(\d+)"',          # 1x1/2" (大小头)
        r'(\d+)Ax(\d+)A',         # 50Ax25A
        r'(\d+)Ax(\d+/\d+)"',     # 50Ax1/2"
    ]

    for pat in patterns:
        m = re.search(pat, name)
        if m:
            size_str = m.group(0)
            # 转换为mm
            size_mm = _size_to_mm(size_str)
            return size_str, size_mm

    return "", 0.0


def _size_to_mm(size_str: str) -> float:
    """管径转毫米"""
    s = size_str.strip().strip('"')
    # 50Ax...
    m = re.match(r'(\d+)A', s)
    if m:
        return float(m.group(1))
    # 1-1/2
    m = re.match(r'(\d+)-(\d+)/(\d+)', s)
    if m:
        return (float(m.group(1)) + float(m.group(2)) / float(m.group(3))) * 25.4
    # 1/2
    m = re.match(r'(\d+)/(\d+)', s)
    if m:
        return float(m.group(1)) / float(m.group(2)) * 25.4
    # 1
    m = re.match(r'^(\d+)$', s)
    if m:
        return float(m.group(1)) * 25.4
    return 0.0


def parse_bom(file_path: str) -> BOMDocument:
    """解析xls点料表文件"""
    wb = xlrd.open_workbook(file_path)
    doc = BOMDocument()

    for sheet_name in wb.sheet_names():
        sh = wb.sheet_by_name(sheet_name)
        sheet = BOMSheet(name=sheet_name)

        # 找到表头行（包含"物料名称"的行）
        header_row = -1
        col_map = {}
        for row_idx in range(min(sh.nrows, 10)):
            for col_idx in range(sh.ncols):
                cell = str(sh.cell_value(row_idx, col_idx)).strip()
                if cell == "物料名称":
                    header_row = row_idx
                    # 映射列
                    for c in range(sh.ncols):
                        header = str(sh.cell_value(row_idx, c)).strip()
                        col_map[header] = c
                    break
            if header_row >= 0:
                break

        if header_row < 0:
            # 没有标准表头，跳过
            continue

        # 读取数据行
        for row_idx in range(header_row + 1, sh.nrows):
            name = _get_cell(sh, row_idx, col_map, "物料名称")
            if not name or name == "None" or len(name) < 2:
                continue

            mat = Material(
                name=name,
                material=_get_cell(sh, row_idx, col_map, "材质"),
                spec=_get_cell(sh, row_idx, col_map, "规格描述"),
                quantity=_get_num(sh, row_idx, col_map, "需求数量"),
                unit=_get_cell(sh, row_idx, col_map, "单位"),
                supplier=_get_cell(sh, row_idx, col_map, "推荐供应商"),
                k3_code=_get_cell(sh, row_idx, col_map, "k3代码"),
                remark=_get_cell(sh, row_idx, col_map, "备注"),
            )

            # 分类
            mat.category = classify_material(mat.name)

            # 提取管径
            mat.size, mat.size_mm = extract_size(mat.name)

            sheet.materials.append(mat)
            doc.all_materials.append(mat)

        doc.sheets.append(sheet)

    return doc


def _get_cell(sh, row, col_map, header_name) -> str:
    """根据表头名获取单元格值"""
    col = col_map.get(header_name, -1)
    if col < 0:
        return ""
    val = sh.cell_value(row, col)
    if isinstance(val, float):
        if val == int(val):
            return str(int(val))
        return str(val)
    return str(val).strip() if val else ""


def _get_num(sh, row, col_map, header_name) -> float:
    """获取数值"""
    col = col_map.get(header_name, -1)
    if col < 0:
        return 0.0
    try:
        val = sh.cell_value(row, col)
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0
