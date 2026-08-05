"""
三维模型生成器
============================
优先从学习的STEP参考文件中加载真实零件几何体；
如果零件库中没有匹配的STEP零件，则用简化几何体（圆柱/方块）占位。
按拓扑关系定位组装，导出STEP/GLB。
"""
import math
import os
import json
import numpy as np
import trimesh
from trimesh import creation, transformations
from dataclasses import dataclass, field
from typing import Optional, Dict
from .matcher import MatchResult, MatchedComponent, MatchedPipe


# STEP零件库缓存：路径 -> trimesh.Scene
_step_cache: Dict[str, trimesh.Scene] = {}


@dataclass
class PlacedPart:
    """放置在三维空间中的零件"""
    part_id: str
    part_type: str          # valve/fitting/pipe/sensor/shell
    name: str
    mesh: Optional[trimesh.Trimesh] = None
    position: tuple = (0, 0, 0)     # (x, y, z) in mm
    rotation: tuple = (0, 0, 0, 1)  # quaternion
    scale: tuple = (1, 1, 1)
    size_mm: float = 0.0
    source: str = "generated"  # generated / library

    def to_dict(self) -> dict:
        return {
            "part_id": self.part_id,
            "part_type": self.part_type,
            "name": self.name,
            "position": [round(v, 2) for v in self.position],
            "size_mm": round(self.size_mm, 2),
            "source": self.source,
        }


@dataclass 
class AssemblyResult:
    """三维装配结果"""
    parts: list = field(default_factory=list)      # PlacedPart
    scene: Optional[trimesh.Scene] = None
    combined_mesh: Optional[trimesh.Trimesh] = None
    stats: dict = field(default_factory=dict)
    export_path: str = ""

    def to_dict(self) -> dict:
        return {
            "parts": [p.to_dict() for p in self.parts],
            "stats": self.stats,
            "export_path": self.export_path,
        }


# ---- 管径到几何参数的映射 ----
SIZE_PARAMS = {
    # size_str: (outer_radius_mm, wall_mm, flange_radius_mm)
    '1/4"':  (3.2, 0.8, 6),
    '1/2"':  (6.4, 1.2, 10),
    '3/4"':  (9.5, 1.5, 13),
    '1"':    (12.7, 1.65, 18),
    '1-1/4"':(16.0, 1.8, 22),
    '1-1/2"':(19.1, 2.1, 26),
    '2"':    (25.4, 2.4, 32),
    '15A':   (10, 1.5, 14),
    '20A':   (13.5, 1.5, 17),
    '25A':   (17, 2.0, 21),
    '40A':   (22, 2.0, 28),
    '50A':   (27, 2.5, 35),
}


def get_size_params(size_str: str, size_mm: float = 0) -> tuple:
    """获取管径对应的几何参数"""
    if size_str and size_str in SIZE_PARAMS:
        return SIZE_PARAMS[size_str]
    if size_mm > 0:
        r = size_mm / 2.0
        return (r, max(r * 0.1, 0.8), r + 5)
    return (12.7, 1.65, 18)  # 默认1"


def generate_assembly(match_result: MatchResult, output_dir: str = "",
                      step_library_path: str = "") -> AssemblyResult:
    """根据匹配结果生成三维装配体
    
    Args:
        match_result: 组件匹配结果
        output_dir: 输出目录
        step_library_path: STEP零件库路径（一个stp文件或包含stp文件的目录）
                           如果提供，优先从STEP文件中提取真实零件几何体
    """
    result = AssemblyResult()
    scene = trimesh.Scene()
    parts = []

    # 归一化STEP零件库路径 -> 库文件列表（支持传入单个文件、目录、或列表，实现通用库）
    step_paths = []
    if step_library_path:
        if isinstance(step_library_path, (list, tuple)):
            step_paths = [p for p in step_library_path if os.path.exists(p)]
        elif os.path.isdir(step_library_path):
            step_paths = [os.path.join(step_library_path, f) for f in os.listdir(step_library_path)
                          if f.lower().endswith(('.stp', '.step'))]
        elif os.path.isfile(step_library_path):
            step_paths = [step_library_path]

    # 加载STEP零件库（多库合并加载）
    step_parts = {}
    if step_paths:
        step_parts = _load_step_library(step_paths)

    # 1. 生成壳体（底盘）
    shell_mesh = _create_shell(match_result)
    if shell_mesh is not None:
        scene.add_geometry(shell_mesh, node_name="壳体")
        pp = PlacedPart(
            part_id="shell",
            part_type="shell",
            name="壳体/底板",
            mesh=shell_mesh,
            position=(0, 0, 0),
        )
        parts.append(pp)

    # 2. 排列组件（阀门/接头/传感器）在底板上
    # 按PID中的x坐标排序，水平排列
    sorted_components = sorted(
        [c for c in match_result.components if c.matched_material],
        key=lambda c: c.pid_x
    )

    # 归一化PID坐标到合理的3D布局
    if sorted_components:
        min_x = min(c.pid_x for c in sorted_components)
        max_x = max(c.pid_x for c in sorted_components)
        range_x = max_x - min_x if max_x > min_x else 100

    for idx, comp in enumerate(sorted_components):
        # 优先从STEP零件库加载真实零件
        mesh = _load_part_from_step(comp, step_parts)
        if mesh is None:
            # 没有STEP零件，用简化几何体
            mesh = _create_component_mesh(comp)
        if mesh is not None:
            # 计算放置位置
            # PID的x映射到3D的x，PID的y映射到3D的z（高度方向）
            # 归一化到合理范围
            if sorted_components:
                norm_x = (comp.pid_x - min_x) / max(range_x, 1) if range_x > 0 else 0.5
                pos_x = (norm_x - 0.5) * 400  # -200mm 到 +200mm
            else:
                pos_x = idx * 60 - 100

            pos_y = 0
            pos_z = 30  # 底板上方

            # 根据管径微调高度
            params = get_size_params(comp.pid_pipe_size)
            pos_z = params[0] + 10

            mesh.apply_translation([pos_x, pos_y, pos_z])

            scene.add_geometry(mesh, node_name=f"comp_{comp.pid_node_id}_{comp.pid_type}")

            pp = PlacedPart(
                part_id=f"comp_{comp.pid_node_id}",
                part_type=comp.matched_material.category if comp.matched_material else "unknown",
                name=comp.matched_material.name if comp.matched_material else comp.pid_type,
                mesh=mesh,
                position=(pos_x, pos_y, pos_z),
                size_mm=get_size_params(comp.pid_pipe_size)[0] * 2,
                source="library" if comp.library_part else "generated",
            )
            parts.append(pp)

    # 3. 生成管路连接
    for pipe_idx, pipe in enumerate(match_result.pipes):
        if len(pipe.points) >= 2 and pipe.size_mm > 0:
            pipe_mesh = _create_pipe_mesh(pipe)
            if pipe_mesh is not None:
                scene.add_geometry(pipe_mesh, node_name=f"pipe_{pipe_idx}")
                pp = PlacedPart(
                    part_id=f"pipe_{pipe_idx}",
                    part_type="pipe",
                    name=f"管道 {pipe.medium} {pipe.size}",
                    mesh=pipe_mesh,
                    position=(0, 0, 0),
                    size_mm=pipe.size_mm,
                )
                parts.append(pp)

    result.scene = scene
    result.parts = parts

    # 4. 合并网格
    all_meshes = [p.mesh for p in parts if p.mesh is not None]
    if all_meshes:
        try:
            result.combined_mesh = trimesh.util.concatenate(all_meshes)
        except Exception:
            result.combined_mesh = all_meshes[0]

    # 5. 统计
    result.stats = {
        "total_parts": len(parts),
        "valve_count": sum(1 for p in parts if p.part_type == "valve"),
        "fitting_count": sum(1 for p in parts if p.part_type == "fitting"),
        "pipe_count": sum(1 for p in parts if p.part_type == "pipe"),
        "sensor_count": sum(1 for p in parts if p.part_type == "sensor"),
        "shell_count": sum(1 for p in parts if p.part_type == "shell"),
        "total_vertices": sum(len(p.mesh.vertices) for p in parts if p.mesh is not None),
        "total_faces": sum(len(p.mesh.faces) for p in parts if p.mesh is not None),
    }

    # 6. 导出
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        # 导出GLB (Web预览用)
        glb_path = os.path.join(output_dir, "assembly.glb")
        try:
            scene.export(glb_path, file_type='glb')
            result.export_path = glb_path
        except Exception as e:
            if result.combined_mesh:
                result.combined_mesh.export(glb_path, file_type='glb')
                result.export_path = glb_path

        # 导出STL
        stl_path = os.path.join(output_dir, "assembly.stl")
        try:
            if result.combined_mesh:
                result.combined_mesh.export(stl_path)
        except Exception:
            pass

        # 导出OBJ
        obj_path = os.path.join(output_dir, "assembly.obj")
        try:
            if result.combined_mesh:
                result.combined_mesh.export(obj_path)
        except Exception:
            pass

        # 导出3MF (3D打印通用格式)
        threemf_path = os.path.join(output_dir, "assembly.3mf")
        try:
            if result.combined_mesh:
                result.combined_mesh.export(threemf_path)
        except Exception:
            pass

        # 导出STEP: 从STEP库中筛选对应零件（支持多个库合并匹配，实现通用库）
        step_out = os.path.join(output_dir, "assembly.stp")
        try:
            if step_paths:
                from .step_filter import extract_model_codes
                model_codes = []
                code_counts = {}

                # 优先以点料表为最全基准：用 bom_library_models（点料表全部匹配到库型号的物料）
                bom_models = getattr(match_result, 'bom_library_models', None) or []
                if bom_models:
                    for bl in bom_models:
                        # 只处理能映射到库型号、且属于有三维零件的类别
                        if bl.get('matched') and bl.get('model'):
                            cat = bl.get('category', '')
                            if cat in ("valve", "fitting", "sensor"):
                                model = bl['model']
                                if model not in model_codes:
                                    model_codes.append(model)
                                code_counts[model] = code_counts.get(model, 0) + float(bl.get('qty', 0) or 0)
                else:
                    # 兜底：从匹配组件中提取（旧逻辑）
                    for comp in match_result.components:
                        if comp.matched_material:
                            mat = comp.matched_material
                            if mat.category in ("valve", "fitting", "sensor"):
                                codes = extract_model_codes(mat.spec, mat.name)
                                if codes:
                                    code = codes[0]
                                    if code not in model_codes:
                                        model_codes.append(code)
                                    code_counts[code] = code_counts.get(code, 0) + float(mat.quantity or 0)

                if model_codes:
                    print(f"STEP导出(BOM基准): 型号代码={model_codes}, 数量={code_counts}, 库={step_paths}")
                    from .step_filter import filter_step_files
                    filter_step_files(step_paths, model_codes, step_out, code_counts)
                else:
                    print("STEP导出: 未提取到型号代码，回退到STL")
                    if result.combined_mesh:
                        result.combined_mesh.export(
                            os.path.join(output_dir, "assembly.stl")
                        )
            else:
                # 没有STEP零件库，用trimesh尝试导出
                if result.combined_mesh:
                    result.combined_mesh.export(step_out, file_type='step')
        except Exception as e:
            print(f"Warning: STEP export failed: {e}, trying STL fallback")
            try:
                if result.combined_mesh:
                    alt_path = os.path.join(output_dir, "assembly.stl")
                    result.combined_mesh.export(alt_path)
            except Exception:
                pass

    return result


def _create_shell(match_result: MatchResult) -> Optional[trimesh.Trimesh]:
    """创建壳体（简化为矩形板）"""
    # 根据组件数量估算壳体大小
    comp_count = len(match_result.components)
    if comp_count == 0:
        return None

    width = max(300, comp_count * 60 + 100)
    depth = 200
    height = 8  # 板厚

    mesh = creation.box(extents=[width, depth, height])
    mesh.apply_translation([0, 0, -height / 2])

    # 给壳体一个浅灰色
    mesh.visual.face_colors = [200, 200, 200, 255]
    return mesh


def _create_component_mesh(comp: MatchedComponent) -> Optional[trimesh.Trimesh]:
    """为单个组件创建三维网格"""
    category = comp.matched_material.category if comp.matched_material else ""
    params = get_size_params(comp.pid_pipe_size)
    outer_r, wall, flange_r = params

    if category == "valve":
        return _create_valve_mesh(outer_r, flange_r, comp.pid_type)
    elif category == "fitting":
        return _create_fitting_mesh(outer_r, flange_r, comp.matched_material.name if comp.matched_material else "")
    elif category == "sensor":
        return _create_sensor_mesh(outer_r)
    else:
        return _create_generic_mesh(outer_r)


def _create_valve_mesh(outer_r: float, flange_r: float, valve_type: str) -> trimesh.Trimesh:
    """创建阀门网格"""
    # 阀门简化为：圆柱体主体 + 两个法兰 + 顶部手柄/执行器
    body_length = outer_r * 3
    body = creation.cylinder(radius=outer_r * 1.2, height=body_length)
    body.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    body.apply_translation([0, 0, outer_r])

    # 两个法兰
    flange1 = creation.cylinder(radius=flange_r, height=4)
    flange1.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    flange1.apply_translation([0, -body_length / 2, outer_r])

    flange2 = creation.cylinder(radius=flange_r, height=4)
    flange2.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    flange2.apply_translation([0, body_length / 2, outer_r])

    parts = [body, flange1, flange2]

    # AV（气动阀）顶部加执行器方块
    if valve_type == "AV":
        actuator = creation.box(extents=[outer_r * 2, outer_r * 2, outer_r * 3])
        actuator.apply_translation([0, 0, outer_r * 2.5])
        actuator.visual.face_colors = [100, 150, 200, 255]
        parts.append(actuator)
    # MV（手动阀）顶部加手轮
    elif valve_type == "MV":
        wheel = creation.torus(major_radius=outer_r * 1.5, minor_radius=2)
        wheel.apply_translation([0, 0, outer_r * 3])
        wheel.visual.face_colors = [150, 150, 150, 255]
        parts.append(wheel)

    body.visual.face_colors = [180, 180, 180, 255]
    flange1.visual.face_colors = [160, 160, 160, 255]
    flange2.visual.face_colors = [160, 160, 160, 255]

    return trimesh.util.concatenate(parts)


def _create_fitting_mesh(outer_r: float, flange_r: float, name: str) -> trimesh.Trimesh:
    """创建接头网格"""
    if "三通" in name:
        # T形三通
        h = creation.cylinder(radius=outer_r, height=outer_r * 4)
        h.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        v = creation.cylinder(radius=outer_r, height=outer_r * 4)
        v.apply_translation([0, 0, outer_r])
        h.visual.face_colors = [200, 180, 160, 255]
        v.visual.face_colors = [200, 180, 160, 255]
        return trimesh.util.concatenate([h, v])
    elif "弯头" in name:
        # 弯头 - 用圆环段近似
        elbow = creation.torus(major_radius=outer_r * 3, minor_radius=outer_r, major_segments=16, minor_sections=12)
        elbow.visual.face_colors = [200, 180, 160, 255]
        return elbow
    elif "法兰" in name:
        # 法兰盘
        flange = creation.cylinder(radius=flange_r, height=8)
        flange.visual.face_colors = [160, 160, 160, 255]
        return flange
    else:
        # 直通接头
        fitting = creation.cylinder(radius=outer_r, height=outer_r * 3)
        fitting.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        fitting.visual.face_colors = [200, 180, 160, 255]
        return fitting


def _create_sensor_mesh(outer_r: float) -> trimesh.Trimesh:
    """创建传感器网格"""
    # 简化为一个小方块 + 连接管
    body = creation.box(extents=[outer_r * 2, outer_r * 2, outer_r * 2])
    body.apply_translation([0, 0, outer_r * 3])
    body.visual.face_colors = [100, 200, 100, 255]

    connector = creation.cylinder(radius=outer_r * 0.5, height=outer_r * 2)
    connector.apply_translation([0, 0, outer_r])

    return trimesh.util.concatenate([connector, body])


def _create_generic_mesh(outer_r: float) -> trimesh.Trimesh:
    """创建通用零件网格"""
    mesh = creation.cylinder(radius=outer_r, height=outer_r * 3)
    mesh.apply_transform(transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    mesh.visual.face_colors = [180, 180, 180, 255]
    return mesh


def _create_pipe_mesh(pipe: MatchedPipe) -> Optional[trimesh.Trimesh]:
    """创建管路网格"""
    if len(pipe.points) < 2 or pipe.size_mm <= 0:
        return None

    params = get_size_params(pipe.size, pipe.size_mm)
    outer_r, wall, _ = params

    # PID坐标到3D坐标的映射
    # x -> x, y -> y (在底板上方一定高度)
    z_height = outer_r + 15

    segments = []
    for i in range(len(pipe.points) - 1):
        p1 = pipe.points[i]
        p2 = pipe.points[i + 1]

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx * dx + dy * dy)

        if length < 1:
            continue

        # 归一化长度到合理范围
        scale = 1.0  # PID单位假设为mm
        length_3d = length * scale

        # 创建管段
        seg = creation.cylinder(radius=outer_r, height=length_3d, sections=16)
        # 旋转到正确方向
        angle = math.atan2(dy, dx)
        seg.apply_transform(transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        seg.apply_transform(transformations.rotation_matrix(angle, [0, 0, 1]))

        # 平移到中点
        mid_x = (p1[0] + p2[0]) / 2 * scale
        mid_y = (p1[1] + p2[1]) / 2 * scale
        seg.apply_translation([mid_x, mid_y, z_height])
        seg.visual.face_colors = [150, 150, 150, 200]

        segments.append(seg)

    if not segments:
        return None

    return trimesh.util.concatenate(segments)


# ============= STEP零件库加载 =============

def _load_step_library(path) -> dict:
    """加载STEP零件库，返回 {零件名: Trimesh} 字典

    path可以是单个stp文件、包含stp文件的目录、或stp文件列表（多库通用）。
    STEP文件中可能包含多个几何体，每个SOLID作为一个零件。
    """
    parts = {}

    # 归一化为文件列表
    if isinstance(path, (list, tuple)):
        stp_files = list(path)
    else:
        stp_files = []
        if os.path.isfile(path) and path.lower().endswith(('.stp', '.step')):
            stp_files.append(path)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(('.stp', '.step')):
                        stp_files.append(os.path.join(root, f))

    for stp_path in stp_files:
        try:
            # 缓存
            if stp_path in _step_cache:
                scene = _step_cache[stp_path]
            else:
                scene = trimesh.load(stp_path)
                _step_cache[stp_path] = scene

            # 提取每个几何体作为独立零件
            if isinstance(scene, trimesh.Scene):
                for name, geom in scene.geometry.items():
                    if isinstance(geom, trimesh.Trimesh) and len(geom.vertices) > 0:
                        # 用文件名+几何体名作为key
                        fname = os.path.splitext(os.path.basename(stp_path))[0]
                        key = f"{fname}_{name}"
                        parts[key] = geom
                        # 也用纯名称索引
                        parts[name] = geom
            elif isinstance(scene, trimesh.Trimesh) and len(scene.vertices) > 0:
                fname = os.path.splitext(os.path.basename(stp_path))[0]
                parts[fname] = scene

        except Exception as e:
            print(f"Warning: Failed to load STEP file {stp_path}: {e}")

    return parts


def _load_part_from_step(comp: MatchedComponent, step_parts: dict) -> Optional[trimesh.Trimesh]:
    """从STEP零件库中查找匹配的零件"""
    if not step_parts or not comp.matched_material:
        return None

    mat_name = comp.matched_material.name
    library_part = getattr(comp, 'library_part', '')
    size = comp.pid_pipe_size

    # 1. 用零件库文件名匹配
    if library_part:
        # 去掉扩展名
        base_name = os.path.splitext(library_part)[0]
        for key, mesh in step_parts.items():
            if base_name.lower() in key.lower():
                return mesh.copy()

    # 2. 用物料名称关键词匹配
    keywords = []
    if '隔膜阀' in mat_name:
        if '气动' in mat_name or comp.pid_type == 'AV':
            keywords.extend(['PLPV', 'PLMV', 'valve', 'AMD'])
        elif '手动' in mat_name or comp.pid_type == 'MV':
            keywords.extend(['PLMV', 'MMD', 'valve', 'MV'])
    elif '球阀' in mat_name:
        keywords.extend(['SBV', 'ball'])
    elif '针阀' in mat_name:
        keywords.extend(['NV', 'needle'])
    elif '调压阀' in mat_name:
        keywords.extend(['64-', 'regulator'])

    if '三通' in mat_name:
        keywords.extend(['tee', '三通', 'PT-'])
    elif '弯头' in mat_name:
        keywords.extend(['elbow', '弯头', 'EW'])
    elif '法兰' in mat_name:
        keywords.extend(['flange', '法兰', 'WP32', 'WP50'])
    elif 'union' in mat_name.lower() or '直通' in mat_name:
        keywords.extend(['union', 'PS-', 'SU-'])

    # 管径关键词
    if size:
        size_clean = size.replace('"', '').replace('/', '-')
        keywords.append(size_clean)

    # 尝试匹配
    for key, mesh in step_parts.items():
        key_lower = key.lower()
        for kw in keywords:
            if kw.lower() in key_lower:
                return mesh.copy()

    return None


def export_assembly_scene(result: AssemblyResult, output_dir: str, fmt: str = "glb") -> str:
    """导出装配体"""
    os.makedirs(output_dir, exist_ok=True)

    if fmt == "glb" and result.scene:
        path = os.path.join(output_dir, "assembly.glb")
        result.scene.export(path, file_type='glb')
        return path
    elif fmt == "obj" and result.combined_mesh:
        path = os.path.join(output_dir, "assembly.obj")
        result.combined_mesh.export(path)
        return path
    elif fmt == "stl" and result.combined_mesh:
        path = os.path.join(output_dir, "assembly.stl")
        result.combined_mesh.export(path)
        return path

    return ""
