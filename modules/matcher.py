"""
组件匹配引擎
将PID图组件与点料表物料匹配，关联三维零件库，处理隐形参数，生成完整BOM+拓扑
"""
import re
import json
import os
from dataclasses import dataclass, field
from typing import Optional
from .dxf_parser import PIDDiagram, ComponentNode, PipeSegment
from .bom_parser import BOMDocument, Material


@dataclass
class MatchedComponent:
    """匹配后的组件 - 包含PID信息和BOM信息"""
    pid_node_id: str        # PID部件编号
    pid_type: str           # PID类型代码 AV/MV/DBV
    pid_tag: str            # PID位号
    pid_x: float
    pid_y: float
    pid_pipe_size: str      # PID上的管径
    pid_medium: str         # 介质

    # 匹配的物料信息
    matched_material: Optional[Material] = None
    match_confidence: float = 0.0  # 匹配置信度 0-1
    match_reason: str = ""

    # 隐形参数（经验补充）
    hidden_items: list = field(default_factory=list)  # 需要额外添加的物料

    # 三维零件库映射
    library_part: str = ""  # 对应的零件库文件名

    def to_dict(self) -> dict:
        return {
            "pid_node_id": self.pid_node_id,
            "pid_type": self.pid_type,
            "pid_tag": self.pid_tag,
            "pid_x": round(self.pid_x, 2),
            "pid_y": round(self.pid_y, 2),
            "pid_pipe_size": self.pid_pipe_size,
            "pid_medium": self.pid_medium,
            "matched_material": self.matched_material.to_dict() if self.matched_material else None,
            "match_confidence": round(self.match_confidence, 2),
            "match_reason": self.match_reason,
            "hidden_items": [h.to_dict() if isinstance(h, Material) else h for h in self.hidden_items],
            "library_part": self.library_part,
        }


@dataclass
class MatchedPipe:
    """匹配后的管路段"""
    medium: str
    size: str
    size_mm: float
    points: list  # 2D点序列
    matched_material: Optional[Material] = None
    estimated_length_mm: float = 0.0  # 估算长度

    def to_dict(self) -> dict:
        return {
            "medium": self.medium,
            "size": self.size,
            "size_mm": self.size_mm,
            "point_count": len(self.points),
            "points": [(round(x, 2), round(y, 2)) for x, y in self.points[:20]],
            "matched_material": self.matched_material.to_dict() if self.matched_material else None,
            "estimated_length_mm": round(self.estimated_length_mm, 2),
        }


@dataclass
class MatchResult:
    """完整的匹配结果"""
    components: list = field(default_factory=list)  # MatchedComponent
    pipes: list = field(default_factory=list)       # MatchedPipe
    unmatched_components: list = field(default_factory=list)  # 未匹配的PID组件
    unmatched_materials: list = field(default_factory=list)   # 未匹配的BOM物料
    hidden_bom: list = field(default_factory=list)  # 隐形参数生成的额外BOM
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "components": [c.to_dict() for c in self.components],
            "pipes": [p.to_dict() for p in self.pipes],
            "unmatched_components": self.unmatched_components,
            "unmatched_material_count": len(self.unmatched_materials),
            "hidden_bom": [h.to_dict() if isinstance(h, Material) else h for h in self.hidden_bom],
            "summary": self.summary,
        }


# ---- PID类型代码到BOM类别的映射 ----
PID_TYPE_TO_CATEGORY = {
    'AV': 'valve',   # 自动阀 -> 气动阀
    'MV': 'valve',   # 手动阀
    'DBV': 'valve',  # 隔膜阀
    'PT': 'sensor',  # 压力传感器
    'PI': 'sensor',  # 压力表
    'KS': 'sensor',  # 流量传感器
}

# ---- PID类型到具体阀门类型的映射 ----
PID_TYPE_TO_VALVE_KEYWORD = {
    'AV': '气动',    # AV通常是气动阀
    'MV': '手动',    # MV通常是手动阀
    'DBV': '隔膜阀',
}

# ---- 隐形参数规则 ----
# 每个阀门/接头需要的隐形配件
HIDDEN_RULES = {
    'valve': [
        # 阀门通常需要垫片
        {"name_template": "垫片{size}", "category": "fitting", "qty_per_valve": 2},
    ],
    'fitting_flange': [
        # 法兰需要螺栓螺母
        {"name_template": "螺栓螺母{size}", "category": "accessory", "qty_per_flange": 4},
    ],
}


def match_components(pid: PIDDiagram, bom: BOMDocument) -> MatchResult:
    """将PID组件与BOM物料匹配"""
    result = MatchResult()

    # 构建BOM索引：按类别+管径分组
    bom_by_category = {}
    for mat in bom.all_materials:
        cat = mat.category
        if cat not in bom_by_category:
            bom_by_category[cat] = []
        bom_by_category[cat].append(mat)

    # 匹配组件
    used_materials = set()  # 已使用的物料索引

    for comp in pid.components:
        matched = MatchedComponent(
            pid_node_id=comp.node_id,
            pid_type=comp.component_type,
            pid_tag=comp.tag,
            pid_x=comp.x,
            pid_y=comp.y,
            pid_pipe_size=comp.pipe_size,
            pid_medium=comp.medium,
        )

        # 确定要匹配的BOM类别
        target_category = PID_TYPE_TO_CATEGORY.get(comp.component_type, "")

        if target_category and target_category in bom_by_category:
            # 在对应类别中找最匹配的物料
            best_mat = None
            best_score = 0.0
            best_reason = ""

            for mat in bom_by_category[target_category]:
                if id(mat) in used_materials:
                    continue

                score, reason = _score_match(comp, mat)
                if score > best_score:
                    best_score = score
                    best_mat = mat
                    best_reason = reason

            if best_mat and best_score > 0.3:
                matched.matched_material = best_mat
                matched.match_confidence = best_score
                matched.match_reason = best_reason
                used_materials.add(id(best_mat))

                # 查找对应的零件库文件
                matched.library_part = _find_library_part(best_mat)

                # 添加隐形参数
                matched.hidden_items = _generate_hidden_items(best_mat, comp.pipe_size)
            else:
                result.unmatched_components.append({
                    "node_id": comp.node_id,
                    "type": comp.component_type,
                    "pipe_size": comp.pipe_size,
                    "reason": "未找到匹配的物料",
                })
        else:
            if comp.component_type:
                result.unmatched_components.append({
                    "node_id": comp.node_id,
                    "type": comp.component_type,
                    "pipe_size": comp.pipe_size,
                    "reason": f"未知的组件类型: {comp.component_type}",
                })

        result.components.append(matched)

    # 匹配管路
    for pipe in pid.pipes:
        matched_pipe = MatchedPipe(
            medium=pipe.medium,
            size=pipe.size,
            size_mm=pipe.size_mm,
            points=pipe.points,
        )

        # 估算管路长度（基于2D坐标，假设比例约1:1，单位mm）
        length = 0.0
        for i in range(len(pipe.points) - 1):
            dx = pipe.points[i + 1][0] - pipe.points[i][0]
            dy = pipe.points[i + 1][1] - pipe.points[i][1]
            length += (dx * dx + dy * dy) ** 0.5
        matched_pipe.estimated_length_mm = length

        # 匹配管道物料
        if pipe.size:
            for mat in bom_by_category.get("pipe", []):
                if id(mat) in used_materials:
                    continue
                if _size_match(pipe.size, mat.size):
                    matched_pipe.matched_material = mat
                    used_materials.add(id(mat))
                    break

        result.pipes.append(matched_pipe)

    # 收集未匹配的物料
    for mat in bom.all_materials:
        if id(mat) not in used_materials:
            result.unmatched_materials.append(mat)

    # 生成隐形BOM汇总
    result.hidden_bom = _aggregate_hidden_bom(result.components)

    # 汇总统计
    matched_count = sum(1 for c in result.components if c.matched_material)
    result.summary = {
        "total_pid_components": len(result.components),
        "matched_components": matched_count,
        "unmatched_components": len(result.unmatched_components),
        "total_pipes": len(result.pipes),
        "total_bom_materials": len(bom.all_materials),
        "used_bom_materials": len(used_materials),
        "unmatched_bom_materials": len(result.unmatched_materials),
        "hidden_items_generated": len(result.hidden_bom),
        "match_rate": round(matched_count / max(len(result.components), 1), 2),
    }

    return result


def _score_match(comp: ComponentNode, mat: Material) -> tuple:
    """计算PID组件与BOM物料的匹配置信度"""
    score = 0.0
    reasons = []

    # 管径匹配
    if comp.pipe_size and mat.size:
        if _size_match(comp.pipe_size, mat.size):
            score += 0.4
            reasons.append(f"管径匹配: {comp.pipe_size}={mat.size}")
        elif _size_partial_match(comp.pipe_size, mat.size):
            score += 0.2
            reasons.append(f"管径部分匹配: {comp.pipe_size}~{mat.size}")

    # 类型匹配
    comp_type = comp.component_type
    if comp_type == 'AV' and '气动' in mat.name:
        score += 0.35
        reasons.append("类型匹配: AV->气动阀")
    elif comp_type == 'MV' and '手动' in mat.name:
        score += 0.35
        reasons.append("类型匹配: MV->手动阀")
    elif comp_type == 'DBV' and '隔膜阀' in mat.name:
        score += 0.35
        reasons.append("类型匹配: DBV->隔膜阀")
    elif comp_type in ('PT', 'PI') and ('压力' in mat.name or '传感器' in mat.name or 'PT' in mat.name.upper()):
        score += 0.35
        reasons.append(f"类型匹配: {comp_type}->传感器")
    elif comp_type == 'KS' and ('流量' in mat.name or 'KS' in mat.name.upper()):
        score += 0.35
        reasons.append("类型匹配: KS->流量传感器")

    # 介质匹配
    if comp.medium:
        if 'Chemical' in comp.medium and ('PFA' in mat.material or '化学' in mat.name):
            score += 0.15
            reasons.append("介质匹配: Chemical->PFA")
        elif 'CDA' in comp.medium and 'SS316' in mat.material:
            score += 0.15
            reasons.append("介质匹配: CDA->SS316")

    # 品牌偏好（如果有位号信息）
    if comp.tag and mat.supplier:
        score += 0.1
        reasons.append(f"有品牌信息: {mat.supplier}")

    return score, "; ".join(reasons)


def _size_match(size1: str, size2: str) -> bool:
    """判断两个管径是否匹配"""
    if not size1 or not size2:
        return False
    # 标准化比较
    s1 = _normalize_size(size1)
    s2 = _normalize_size(size2)
    return s1 == s2


def _size_partial_match(size1: str, size2: str) -> bool:
    """管径部分匹配（如1/2"和1/2）"""
    s1 = size1.lower().replace('"', '').replace(' ', '')
    s2 = size2.lower().replace('"', '').replace(' ', '')
    return s1 in s2 or s2 in s1


def _normalize_size(size: str) -> str:
    """标准化管径表示"""
    s = size.strip()
    # 统一格式: 1/2", 1", 40A 等
    s = s.replace(' ', '').replace('，', ',')
    return s.lower()


def _find_library_part(mat: Material) -> str:
    """根据物料查找对应的零件库文件"""
    # 基于物料名称和规格匹配零件库
    # 这里先返回基于命名规则的推测
    name = mat.name.lower()
    spec = mat.spec.lower()

    # 阀门类
    if mat.category == 'valve':
        if 'ckd' in mat.supplier.lower() or 'ckd' in spec:
            if '1/2' in mat.size:
                return "PLMV-K08.ipt"  # 手动阀1/2"
            elif '1"' in mat.size or '3/4' in mat.size:
                return "PLMV-K16.ipt"
        if 'entegris' in mat.supplier.lower():
            if '1-1/2' in mat.size:
                return "PLPV-K08.ipt"  # 气动阀

    # 接头类
    if mat.category == 'fitting':
        if '三通' in name or 'union三通' in name:
            if '1-1/2' in mat.size:
                return "G2FATW16.ipt"
        if '弯头' in name:
            if '1' in mat.size:
                return "G2FAEW16.ipt"

    # 法兰类
    if '法兰' in name and '50a' in mat.size.lower():
        return "50A法兰-WP32P_CPY.ipt"

    return ""


def _generate_hidden_items(material: Material, pipe_size: str) -> list:
    """根据隐形参数规则生成额外物料"""
    hidden = []

    if material.category == 'valve':
        # 阀门需要垫片
        gasket = Material(
            name=f"垫片{pipe_size or material.size}",
            material="PTFE",
            spec=f"PTFE垫片 {pipe_size or material.size}",
            quantity=2,
            unit="PCS",
            supplier="配套",
            k3_code="",
            category="fitting",
            size=pipe_size or material.size,
            size_mm=material.size_mm,
        )
        hidden.append(gasket)

    return hidden


def _aggregate_hidden_bom(components: list) -> list:
    """汇总所有隐形参数生成的BOM"""
    aggregated = {}

    for comp in components:
        for item in comp.hidden_items:
            key = f"{item.name}_{item.size}"
            if key in aggregated:
                aggregated[key].quantity += item.quantity
            else:
                # 创建副本
                aggregated[key] = Material(
                    name=item.name,
                    material=item.material,
                    spec=item.spec,
                    quantity=item.quantity,
                    unit=item.unit,
                    supplier=item.supplier,
                    k3_code=item.k3_code,
                    category=item.category,
                    size=item.size,
                    size_mm=item.size_mm,
                )

    return list(aggregated.values())


def load_learning_cases(cases_dir: str) -> list:
    """加载已学习的历史案例"""
    cases = []
    if not os.path.exists(cases_dir):
        return cases

    for fname in os.listdir(cases_dir):
        if fname.endswith('.json'):
            fpath = os.path.join(cases_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    case = json.load(f)
                    cases.append(case)
            except (json.JSONDecodeError, IOError):
                pass

    return cases


def save_learning_case(cases_dir: str, case_name: str, pid: PIDDiagram, bom: BOMDocument, result: MatchResult):
    """保存一个学习案例"""
    os.makedirs(cases_dir, exist_ok=True)

    case_data = {
        "name": case_name,
        "pid": pid.to_dict(),
        "bom": bom.to_dict(),
        "match_result": result.to_dict(),
    }

    fpath = os.path.join(cases_dir, f"{case_name}.json")
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(case_data, f, ensure_ascii=False, indent=2)

    return fpath
