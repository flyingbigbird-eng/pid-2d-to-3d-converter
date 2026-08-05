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


# ---- 表头列名匹配规则（兼容多种点料表布局） ----
_COL_ALIASES = {
    "name": ["物料名称", "物料编码", "名称"],
    "material": ["材质", "材料", "材质描述"],
    "spec": ["规格", "规格描述", "规格+订货号+描述", "规格(订货号)描述", "订货号", "型号规格"],
    "quantity": ["需求数量", "合计数量", "数量", "总数量", "用量"],
    "unit": ["单位"],
    "supplier": ["供应商", "推荐供应商", "品牌", "厂家"],
    "k3_code": ["k3代码", "k3编码", "U9码", "U9代码", "物料代码", "编码"],
    "remark": ["备注", "说明"],
    "lock_qty": ["锁库量"],
}


def _match_col(header: str, aliases: list) -> bool:
    """判断表头是否匹配某个字段的别名"""
    h = (header or "").strip().lower()
    if not h:
        return False
    for alias in aliases:
        if alias.lower() in h or h in alias.lower():
            return True
    return False


def _is_name_header(h: str) -> bool:
    """判段是否为"物料名称"表头（精确首选项，避免误匹配数据行）
    只有含"物料名称"/"名称"且作为独立表头时算。
    """
    ah = (h or "").strip()
    for kw in ("物料名称", "物料编码", "物料名称及规格", "名称"):
        if ah == kw or ah.startswith(kw):
            return True
    return False


def _find_header_row(sh) -> tuple:
    """在sheet中定位表头行（包含"物料名称"的行）
    返回 (header_row, col_map)
    col_map: {"name": col_idx, "quantity": col_idx, ...}
    """
    for row_idx in range(min(sh.nrows, 15)):
        row_headers = [str(sh.cell_value(row_idx, c)).strip() for c in range(sh.ncols)]
        # 必须真正包含"物料名称"表头（精确匹配），防止把数据行误当表头
        if not any(_is_name_header(h) for h in row_headers):
            continue

        col_map = {}
        for key, aliases in _COL_ALIASES.items():
            for c, h in enumerate(row_headers):
                if _match_col(h, aliases):
                    col_map[key] = c
                    break
        return row_idx, col_map

    return -1, {}


def _resolve_quantity_col(sh, col_map, header_row) -> int:
    """确定数量列：
    1) 优先用表头匹配到的"合计数量/需求数量"列
    2) 若匹配到"锁库量"但没匹配到合计数量，则合计数量通常是前一列或后一列
    3) 若表头行位于第8行之后(即华力H3布局)，且存在第10列(J列)，则该列为该行的有效数量列
    """
    if "quantity" in col_map:
        return col_map["quantity"]

    # 华力H3布局：表头在第8行(索引7)及以后，且表格有第10列(J列)
    if header_row >= 7 and sh.ncols >= 10:
        # 检查J列表头是不是一个"介质相关"列名（非标准数量列名）
        header_j = str(sh.cell_value(header_row, 9)).strip()
        # 只要 J 列不是空，且前几列存在物料名称，就把它当数量列
        if header_j:
            return 9

    # 锁库量前一列通常是合计数量
    if "lock_qty" in col_map and col_map["lock_qty"] > 0:
        return col_map["lock_qty"] - 1

    return -1


def _cell_to_num(v):
    """把单元格值转为数字，失败返回0"""
    try:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return 0.0
            return float(v)
        return float(v) if v else 0.0
    except (ValueError, TypeError):
        return 0.0


def parse_bom(file_path: str) -> BOMDocument:
    """解析xls点料表文件

    支持两种布局：
    1. 标准布局：表头在第一行附近，包含"物料名称/规格/合计数量/单位/供应商/U9码"
    2. 华力H3布局：表头在第8行，J列(第10列)为该行有效数量列，数据从第9行开始

    只保留"有效数量 > 0"的物料。
    """
    wb = xlrd.open_workbook(file_path)
    doc = BOMDocument()

    for sheet_name in wb.sheet_names():
        sh = wb.sheet_by_name(sheet_name)
        header_row, col_map = _find_header_row(sh)

        if header_row < 0:
            # 没有标准表头，跳过
            continue

        qty_col = _resolve_quantity_col(sh, col_map, header_row)

        name_col = col_map.get("name", 1)
        sheet = BOMSheet(name=sheet_name)

        # 读取数据行
        for row_idx in range(header_row + 1, sh.nrows):
            name = _raw_cell(sh, row_idx, name_col)
            if not name or name == "None" or len(name) < 2:
                continue

            # 数量=0 的排除（有效数据必须是数量>0）
            qty = _cell_to_num(sh.cell_value(row_idx, qty_col)) if qty_col >= 0 else 0.0
            if qty <= 0:
                continue

            spec_col = col_map.get("spec", -1)
            mat_col = col_map.get("material", -1)
            unit_col = col_map.get("unit", -1)
            sup_col = col_map.get("supplier", -1)
            k3_col = col_map.get("k3_code", -1)
            rem_col = col_map.get("remark", -1)

            mat = Material(
                name=name,
                material=_raw_cell(sh, row_idx, mat_col),
                spec=_raw_cell(sh, row_idx, spec_col),
                quantity=qty,
                unit=_raw_cell(sh, row_idx, unit_col),
                supplier=_raw_cell(sh, row_idx, sup_col),
                k3_code=_raw_cell(sh, row_idx, k3_col),
                remark=_raw_cell(sh, row_idx, rem_col),
            )

            # 分类
            mat.category = classify_material(mat.name)
            # 提取管径
            mat.size, mat.size_mm = extract_size(mat.name)

            sheet.materials.append(mat)
            doc.all_materials.append(mat)

        doc.sheets.append(sheet)

    return doc


def _raw_cell(sh, row, col) -> str:
    """按列索引读取单元格文本值，col<0 返回空"""
    if col < 0 or col >= sh.ncols:
        return ""
    val = sh.cell_value(row, col)
    if isinstance(val, float):
        if val == int(val):
            return str(int(val))
        return str(val)
    return str(val).strip() if val else ""
