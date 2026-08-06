"""
学习引擎
==================
学习阶段：输入 2D(DXF) + 点料表(xls) + 三维参考(STP/零件库)，提取映射规则和装配知识
使用阶段：输入 2D(DXF) + 点料表(xls)，利用已学规则生成三维模型

核心思路：
  - 用已有案例（如海神VMB）学习：PID组件 -> BOM物料 -> 三维零件 的映射关系
  - 学习管路拓扑规则（管径、走向、间距、方向）
  - 学习隐形参数规则（垫片、螺栓等隐含配件）
  - 新项目只需 2D + 点料表，按学到的规则自动匹配和生成
"""
import json
import os
import math
import re
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from .dxf_parser import PIDDiagram, parse_dxf, ComponentNode, PipeSegment
from .bom_parser import BOMDocument, parse_bom, Material
from .matcher import MatchResult, match_components, MatchedComponent
from .step_filter import parse_step_file, _decode_step_name


@dataclass
class PartMapping:
    """单个零件的映射规则（从学习中提取）"""
    pid_type: str           # PID类型代码 AV/MV/DBV
    pid_pipe_size: str      # 管径
    bom_name_pattern: str   # BOM物料名称模式
    bom_supplier: str       # 品牌供应商
    library_part: str       # 对应的三维零件文件/型号
    library_part_path: str  # 零件文件路径
    match_confidence: float = 0.0
    occurrence_count: int = 1  # 在历史案例中出现次数
    u9_code: str = ""       # 物料U9码（点料表唯一标识）

    def to_dict(self) -> dict:
        return {
            "pid_type": self.pid_type,
            "pid_pipe_size": self.pid_pipe_size,
            "bom_name_pattern": self.bom_name_pattern,
            "bom_supplier": self.bom_supplier,
            "library_part": self.library_part,
            "library_part_path": self.library_part_path,
            "match_confidence": round(self.match_confidence, 2),
            "occurrence_count": self.occurrence_count,
            "u9_code": self.u9_code,
        }


@dataclass
class TopologyRule:
    """管路拓扑规则"""
    medium: str              # 介质
    pipe_size: str           # 管径
    size_mm: float            # 毫米
    typical_length_mm: float = 0.0  # 典型长度
    direction: str = "horizontal"   # 走向
    branch_count: int = 0           # 分支数

    def to_dict(self) -> dict:
        return {
            "medium": self.medium,
            "pipe_size": self.pipe_size,
            "size_mm": self.size_mm,
            "typical_length_mm": round(self.typical_length_mm, 2),
            "direction": self.direction,
            "branch_count": self.branch_count,
        }


@dataclass
class AssemblyRule:
    """装配规则 - 零件之间的空间关系"""
    component_type: str      # 组件类型
    spacing_mm: float        # 间距
    orientation: str         # 方向
    base_offset: tuple = (0, 0, 0)  # 基础偏移

    def to_dict(self) -> dict:
        return {
            "component_type": self.component_type,
            "spacing_mm": round(self.spacing_mm, 2),
            "orientation": self.orientation,
            "base_offset": [round(v, 2) for v in self.base_offset],
        }


@dataclass
class HiddenParamRule:
    """隐形参数规则"""
    trigger_category: str   # 触发类别 (valve/fitting等)
    trigger_size: str       # 触发管径
    hidden_name: str        # 隐形配件名称
    hidden_category: str
    hidden_qty: int
    hidden_material: str = ""

    def to_dict(self) -> dict:
        return {
            "trigger_category": self.trigger_category,
            "trigger_size": self.trigger_size,
            "hidden_name": self.hidden_name,
            "hidden_category": self.hidden_category,
            "hidden_qty": self.hidden_qty,
            "hidden_material": self.hidden_material,
        }


@dataclass
class LearnedKnowledge:
    """从历史案例中学到的全部知识"""
    case_name: str = ""
    part_mappings: list = field(default_factory=list)     # PartMapping
    topology_rules: list = field(default_factory=list)    # TopologyRule
    assembly_rules: list = field(default_factory=list)   # AssemblyRule
    hidden_param_rules: list = field(default_factory=list)  # HiddenParamRule
    pid_summary: dict = field(default_factory=dict)
    bom_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "case_name": self.case_name,
            "part_mappings": [p.to_dict() for p in self.part_mappings],
            "topology_rules": [t.to_dict() for t in self.topology_rules],
            "assembly_rules": [a.to_dict() for a in self.assembly_rules],
            "hidden_param_rules": [h.to_dict() for h in self.hidden_param_rules],
            "pid_summary": self.pid_summary,
            "bom_summary": self.bom_summary,
        }


def learn_from_case(
    case_name: str,
    dxf_path: str = "",
    bom_path: str = "",
    ref_3d_dir: str = "",
    output_dir: str = "",
) -> LearnedKnowledge:
    """
    学习阶段：从一个完整案例中提取知识
    输入：2D(DXF) + 点料表(xls) + 三维参考(目录)
    输出：LearnedKnowledge 保存到 output_dir

    dxf_path 可选：如果没有dxf（只有dwg），仍可从bom+stp学习器件库映射
    """
    knowledge = LearnedKnowledge(case_name=case_name)

    # 1. 解析2D（如果有dxf）
    pid = None
    if dxf_path and os.path.exists(dxf_path):
        try:
            pid = parse_dxf(dxf_path)
        except Exception as e:
            print(f"Warning: DXF parsing failed: {e}")

    # 2. 解析点料表
    bom = parse_bom(bom_path) if bom_path and os.path.exists(bom_path) else BOMDocument()

    # 3. 匹配组件（如果有pid）
    result = None
    if pid and bom.all_materials:
        result = match_components(pid, bom)

    # 4. 扫描三维参考目录，建立零件库索引
    library_index = _scan_3d_library(ref_3d_dir) if ref_3d_dir else {"parts": [], "assemblies": [], "step_files": [], "dir": ""}

    # 5. 提取映射规则
    knowledge.part_mappings = _extract_part_mappings(result, library_index) if result else []
    knowledge.topology_rules = _extract_topology_rules(pid) if pid else []
    knowledge.assembly_rules = _extract_assembly_rules(pid, result) if (pid and result) else []
    knowledge.hidden_param_rules = _extract_hidden_rules(result) if result else []

    knowledge.pid_summary = {
        "component_count": len(pid.components) if pid else 0,
        "pipe_count": len(pid.pipes) if pid else 0,
        "extents": pid.extents if pid else (0, 0, 0, 0),
    }
    knowledge.bom_summary = {
        "total_materials": len(bom.all_materials),
        "category_stats": {},
    }
    for mat in bom.all_materials:
        cat = mat.category or "other"
        if cat not in knowledge.bom_summary["category_stats"]:
            knowledge.bom_summary["category_stats"][cat] = 0
        knowledge.bom_summary["category_stats"][cat] += 1

    # 6. 保存
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{case_name}_knowledge.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(knowledge.to_dict(), f, ensure_ascii=False, indent=2)

    # 同时保存原始匹配结果作为案例
    case_data = {
        "name": case_name,
        "pid": pid.to_dict() if pid else {},
        "bom": bom.to_dict(),
        "match_result": result.to_dict() if result else {},
        "library_index": library_index,
    }
    case_path = os.path.join(output_dir, f"{case_name}_case.json")
    with open(case_path, 'w', encoding='utf-8') as f:
        json.dump(case_data, f, ensure_ascii=False, indent=2)

    return knowledge


def _scan_3d_library(ref_3d_dir: str) -> dict:
    """扫描三维参考目录，建立零件索引"""
    index = {
        "parts": [],  # 零件文件列表
        "assemblies": [],  # 装配文件列表
        "step_files": [],  # STEP文件列表
        "stp_models": {},  # 型号代码 -> [STP文件全路径] (从STEP PRODUCT名称解析)
        "dir": ref_3d_dir,
    }

    if not os.path.exists(ref_3d_dir):
        return index

    for root, dirs, files in os.walk(ref_3d_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, ref_3d_dir)

            if ext == '.ipt':
                index["parts"].append({
                    "name": fname,
                    "path": rel_path,
                    "full_path": fpath,
                })
            elif ext == '.iam':
                index["assemblies"].append({
                    "name": fname,
                    "path": rel_path,
                    "full_path": fpath,
                })
            elif ext in ('.stp', '.step'):
                index["step_files"].append({
                    "name": fname,
                    "path": rel_path,
                    "full_path": fpath,
                })
                # 解析STP里的PRODUCT型号，建立 型号->库文件 索引
                _scan_stp_models(fpath, index["stp_models"])

    return index


def _scan_stp_models(stp_path: str, model_index: dict) -> None:
    """解析单个STP文件的PRODUCT型号，登记到 index["stp_models"]

    stp_models: {型号代码(大写): [STP文件全路径]}
    型号从STEP PRODUCT名称字段提取，如 PLPV-K08, G2FATW08, MMD303RN。
    """
    try:
        entities = parse_step_file(stp_path)
    except Exception:
        return

    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT':
            continue
        name_matches = re.findall(r"'([^']*)'", line)
        for nm in name_matches:
            decoded = _decode_step_name(nm)
            code = _extract_step_model_code(decoded)
            if code:
                model_index.setdefault(code, [])
                if stp_path not in model_index[code]:
                    model_index[code].append(stp_path)


def _extract_step_model_code(text: str) -> str:
    """从STEP PRODUCT名称文本中提取商品型号代码（与spec端用同一规则）"""
    if not text:
        return ""
    from .step_filter import _extract_codes_from_text
    codes = _extract_codes_from_text(text)
    # 取第一个候选（STP PRODUCT名通常是"中文名|型号名|..."，型号名在字段里）
    for c in codes:
        # 排除明显非型号（中文解码残留、超长hex、通用词）
        if len(c) >= 4 and not re.fullmatch(r'[0-9A-F]{6,}', c):
            return c
    return ""


def _extract_part_mappings(result: MatchResult, library_index: dict) -> list:
    """从匹配结果中提取零件映射规则

    现在优先用 BOM 物料的 spec 型号代码 匹配 STP 库中的型号 (stp_models)，
    建立"物料 -> 三维型号 -> 库文件"的映射。U9码同时记录在 mapping 里，
    供后续以 U9 为基准做 1:1 匹配。
    """
    mappings = []
    seen = set()

    # 建零件名到路径的索引（.ipt 旧逻辑保留）
    part_name_to_path = {}
    for p in library_index.get("parts", []):
        part_name_to_path[p["name"]] = p["full_path"]

    # STP 型号索引: 型号代码 -> 全路径（新逻辑核心）
    stp_models = library_index.get("stp_models", {})

    from .step_filter import extract_model_codes

    for comp in result.components:
        if not comp.matched_material:
            continue

        mat = comp.matched_material

        # 从物料 spec/name 提取型号代码
        spec_codes = extract_model_codes(mat.spec, mat.name)

        # 匹配 STP 库型号
        matched_stp_model = ""
        matched_stp_path = ""
        for code in spec_codes:
            code_up = code.upper()
            if code_up in stp_models:
                matched_stp_model = code_up
                matched_stp_path = stp_models[code_up][0]
                break
        # 兜底：模糊匹配（型号前缀包含关系，要求 code 够长且含数字，避免错误前缀匹配）
        if not matched_stp_model:
            for code in spec_codes:
                code_up = code.upper()
                if len(code_up) < 5 or not re.search(r'\d', code_up):
                    continue
                for model_name in stp_models:
                    if code_up in model_name or model_name in code_up:
                        matched_stp_model = model_name
                        matched_stp_path = stp_models[model_name][0]
                        break
                if matched_stp_model:
                    break

        # 去重键（含型号）
        key = f"{comp.pid_type}_{comp.pid_pipe_size}_{mat.name}_{matched_stp_model}"
        if key in seen:
            for m in mappings:
                if (f"{m.pid_type}_{m.pid_pipe_size}_{m.bom_name_pattern}_{m.library_part}" == key):
                    m.occurrence_count += 1
                    break
            continue
        seen.add(key)

        # 兼容旧 .ipt 匹配
        library_part = comp.library_part
        if not matched_stp_model and library_part and library_part in part_name_to_path:
            matched_stp_path = part_name_to_path[library_part]
            matched_stp_model = library_part

        mapping = PartMapping(
            pid_type=comp.pid_type,
            pid_pipe_size=comp.pid_pipe_size,
            bom_name_pattern=mat.name,
            bom_supplier=mat.supplier,
            library_part=matched_stp_model or library_part,
            library_part_path=matched_stp_path,
            match_confidence=comp.match_confidence,
            occurrence_count=1,
        )
        # 额外记录 U9 码
        mapping.u9_code = mat.k3_code
        mappings.append(mapping)

    return mappings


def _extract_topology_rules(pid: PIDDiagram) -> list:
    """提取管路拓扑规则"""
    rules = []
    seen = set()

    for pipe in pid.pipes:
        key = f"{pipe.medium}_{pipe.size}"
        if key in seen:
            continue
        seen.add(key)

        # 计算典型长度
        length = 0.0
        for i in range(len(pipe.points) - 1):
            dx = pipe.points[i + 1][0] - pipe.points[i][0]
            dy = pipe.points[i + 1][1] - pipe.points[i][1]
            length += math.sqrt(dx * dx + dy * dy)

        # 判断走向
        direction = "horizontal"
        if len(pipe.points) >= 2:
            dx = abs(pipe.points[-1][0] - pipe.points[0][0])
            dy = abs(pipe.points[-1][1] - pipe.points[0][1])
            if dx > dy * 3:
                direction = "horizontal"
            elif dy > dx * 3:
                direction = "vertical"
            else:
                direction = "mixed"

        rule = TopologyRule(
            medium=pipe.medium,
            pipe_size=pipe.size,
            size_mm=pipe.size_mm,
            typical_length_mm=length,
            direction=direction,
        )
        rules.append(rule)

    return rules


def _extract_assembly_rules(pid: PIDDiagram, result: MatchResult) -> list:
    """提取装配规则 - 组件之间的间距"""
    rules = []
    seen_types = set()

    # 按类型分组组件，计算间距
    type_groups = {}
    for comp in result.components:
        if comp.pid_type and comp.matched_material:
            if comp.pid_type not in type_groups:
                type_groups[comp.pid_type] = []
            type_groups[comp.pid_type].append(comp)

    for comp_type, comps in type_groups.items():
        if comp_type in seen_types or len(comps) < 1:
            continue
        seen_types.add(comp_type)

        # 计算平均间距
        if len(comps) >= 2:
            # 按x坐标排序
            sorted_comps = sorted(comps, key=lambda c: c.pid_x)
            spacings = []
            for i in range(len(sorted_comps) - 1):
                dx = sorted_comps[i + 1].pid_x - sorted_comps[i].pid_x
                dy = sorted_comps[i + 1].pid_y - sorted_comps[i].pid_y
                spacings.append(math.sqrt(dx * dx + dy * dy))
            avg_spacing = sum(spacings) / len(spacings) if spacings else 100.0
        else:
            avg_spacing = 100.0  # 默认间距

        rule = AssemblyRule(
            component_type=comp_type,
            spacing_mm=avg_spacing,
            orientation="horizontal",
        )
        rules.append(rule)

    return rules


def _extract_hidden_rules(result: MatchResult) -> list:
    """提取隐形参数规则"""
    rules = []
    seen = set()

    for comp in result.components:
        for item in comp.hidden_items:
            key = f"{comp.matched_material.category if comp.matched_material else ''}_{comp.pid_pipe_size}_{item.name}"
            if key in seen:
                continue
            seen.add(key)

            rule = HiddenParamRule(
                trigger_category=comp.matched_material.category if comp.matched_material else "",
                trigger_size=comp.pid_pipe_size,
                hidden_name=item.name,
                hidden_category=item.category,
                hidden_qty=int(item.quantity),
                hidden_material=item.material,
            )
            rules.append(rule)

    return rules


def load_knowledge(knowledge_dir: str) -> list:
    """加载所有已学知识"""
    knowledge_list = []
    if not os.path.exists(knowledge_dir):
        return knowledge_list

    for fname in os.listdir(knowledge_dir):
        if fname.endswith('_knowledge.json'):
            fpath = os.path.join(knowledge_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    knowledge_list.append(data)
            except (json.JSONDecodeError, IOError):
                pass

    return knowledge_list


def apply_knowledge(
    dxf_path: str,
    bom_path: str,
    knowledge_list: list,
) -> MatchResult:
    """
    使用阶段：输入2D+点料表，利用已学知识进行匹配
    返回增强后的MatchResult（包含library_part映射 + 点料表全量U9库型号映射）
    """
    # 解析2D和点料表
    pid = parse_dxf(dxf_path)
    bom = parse_bom(bom_path)

    # 基础匹配
    result = match_components(pid, bom)

    # 用学到的知识增强匹配结果
    _enhance_with_knowledge(result, knowledge_list)

    # 以点料表为最全基准：遍历全部有U9码的物料，匹配库型号（未匹配的标红备用）
    result.bom_library_models = _build_bom_library_models(bom, knowledge_list)

    return result


def _build_bom_library_models(bom, knowledge_list: list) -> list:
    """遍历点料表全部有U9码的物料，用知识库的U9->型号映射匹配库模型

    返回 [{u9, name, spec, qty, category, model, matched, source}]
    source: 'library'=精确配库, 'pid'=来自PID组件, ''=未匹配
    """
    # 知识索引：u9_code -> library_part (型号)，以及 pid_type_pipe -> model 兜底
    u9_to_model = {}
    pid_to_model = {}
    for knowledge in knowledge_list:
        for mapping in knowledge.get("part_mappings", []):
            u9 = mapping.get("u9_code") or ""
            model = mapping.get("library_part") or ""
            if u9 and model and "." not in model:  # 排除.ipt旧兜底
                u9_to_model[u9] = model
            pid_key = f"{mapping.get('pid_type','')}_{mapping.get('pid_pipe_size','')}"
            if model and "." not in model:
                pid_to_model.setdefault(pid_key, model)

    from .step_filter import extract_model_codes
    import os
    import glob

    # 动态扫描STP库（绕过旧知识库缺少stp_models的问题）
    stp_models = {}
    lib_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'library')
    if os.path.exists(lib_dir):
        for stp_file in glob.glob(os.path.join(lib_dir, '*.stp')) + glob.glob(os.path.join(lib_dir, '*.STP')):
            try:
                from .step_filter import parse_step_file
                entities = parse_step_file(stp_file)
                for eid, (etype, line) in entities.items():
                    if etype != 'PRODUCT':
                        continue
                    import re
                    name_matches = re.findall(r"'([^']*)'", line)
                    for nm in name_matches:
                        decoded = _decode_step_name(nm)
                        code = _extract_step_model_code(decoded)
                        if code:
                            stp_models.setdefault(code, [])
                            if stp_file not in stp_models[code]:
                                stp_models[code].append(stp_file)
            except Exception:
                pass
    from .bom_parser import BOMDocument

    results = []
    seen_spec = set()  # 按 (型号,名称) 去重，避免同物料重复
    for mat in bom.all_materials:
        u9 = (mat.k3_code or "").strip()
        if not u9:
            continue

        # 1) U9精确匹配
        model = u9_to_model.get(u9, "")
        matched_source = "library" if model else ""

        # 2) 兜底：spec型号匹配知识库pid映射 或 U9映射的型号前缀
        if not matched_source:
            codes = extract_model_codes(mat.spec, mat.name)
            for c in codes:
                if c in u9_to_model.values():
                    model = c
                    matched_source = "library"
                    break
            if not matched_source:
                # 检查知识库是否存在该型号映射
                for m_model in set(u9_to_model.values()):
                    for c in codes:
                        if c in m_model or m_model in c:
                            model = m_model
                            matched_source = "library"
                            break
                    if model:
                        break

        # 3) 终极兜底：直接用spec型号匹配STP库型号（绕过旧知识库缺失问题）
        if not matched_source and stp_models:
            codes = extract_model_codes(mat.spec, mat.name)
            for c in codes:
                c_up = c.upper()
                if c_up in stp_models:
                    model = c_up
                    matched_source = "library"
                    break

        results.append({
            "u9": u9,
            "name": mat.name,
            "spec": mat.spec,
            "qty": mat.quantity,
            "category": mat.category,
            "model": model,
            "matched": bool(model),
            "source": matched_source,
        })

    return results


def _enhance_with_knowledge(result: MatchResult, knowledge_list: list):
    """用学到的知识增强匹配结果"""
    # 构建知识索引：(pid_type, pipe_size) -> library_part
    knowledge_index = {}
    for knowledge in knowledge_list:
        for mapping in knowledge.get("part_mappings", []):
            key = f"{mapping['pid_type']}_{mapping['pid_pipe_size']}"
            if key not in knowledge_index:
                knowledge_index[key] = mapping

    # 应用到匹配结果
    for comp in result.components:
        if comp.pid_type:
            key = f"{comp.pid_type}_{comp.pid_pipe_size}"
            if key in knowledge_index:
                learned = knowledge_index[key]
                if not comp.library_part and learned.get("library_part"):
                    comp.library_part = learned["library_part"]
                    comp.match_reason += f"; 知识库匹配: {learned['library_part']}"
                    comp.match_confidence = max(comp.match_confidence, 0.7)

    # 应用隐形参数规则
    for knowledge in knowledge_list:
        for hidden_rule in knowledge.get("hidden_param_rules", []):
            for comp in result.components:
                if (comp.matched_material and
                    comp.matched_material.category == hidden_rule.get("trigger_category") and
                    comp.pid_pipe_size == hidden_rule.get("trigger_size")):
                    # 检查是否已添加
                    already_has = any(
                        h.name == hidden_rule.get("hidden_name")
                        for h in comp.hidden_items
                    )
                    if not already_has:
                        hidden_mat = Material(
                            name=hidden_rule.get("hidden_name", ""),
                            material=hidden_rule.get("hidden_material", ""),
                            spec=hidden_rule.get("hidden_name", ""),
                            quantity=hidden_rule.get("hidden_qty", 1),
                            unit="PCS",
                            category=hidden_rule.get("hidden_category", "fitting"),
                            size=hidden_rule.get("trigger_size", ""),
                        )
                        comp.hidden_items.append(hidden_mat)
