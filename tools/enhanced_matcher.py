"""
增强版组件匹配引擎
==================

集成VMB-8A三层映射：
  1. PID组件类型/管径 -> 型号代码
  2. 型号代码 -> U9码
  3. U9码 -> STP零件
"""

import json
import re
import os
from typing import Dict, Optional


def normalize_pipe_size(size_str: str) -> str:
    """
    标准化管径表示
    1/4" -> 14
    1/2" -> 12
    """
    size_map = {
        '1/4"': '14',
        '1/2"': '12',
        '3/4"': '34',
        '1"': '1',
        '1-1/4"': '114'
    }
    return size_map.get(size_str, size_str.replace('/', '').replace('"', ''))


def build_model_code(component_type: str, pipe_size: str) -> str:
    """
    构建型号代码

    Args:
        component_type: 组件类型（如"手动隔膜阀"）
        pipe_size: 管径（如"1/2\""）

    Returns:
        型号代码（如"手动隔膜阀 12"）
    """
    size_abbr = normalize_pipe_size(pipe_size)
    return f"{component_type} {size_abbr}"


def load_vmb8a_mapping():
    """加载VMB-8A完整映射"""
    mapping_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_complete_mapping.json"

    if os.path.exists(mapping_path):
        with open(mapping_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def match_component_with_u9(
    pid_type: str,
    pid_pipe_size: str,
    mapping_data: dict
) -> Optional[dict]:
    """
    使用U9码匹配组件

    Args:
        pid_type: PID组件类型（如"MV", "AV"）
        pid_pipe_size: 管径（如"1/2\""）
        mapping_data: VMB-8A映射数据

    Returns:
        匹配结果 {model_code, u9, stp_name}
    """
    # PID类型到中文类型映射
    pid_type_map = {
        'AV': '气动隔膜阀',
        'MV': '手动隔膜阀',
        'DBV': '隔膜阀',
        'PIPE': '管道'
    }

    # 构建型号代码
    comp_type = pid_type_map.get(pid_type, pid_type)
    model_code = build_model_code(comp_type, pid_pipe_size)

    # 查找映射
    pid_to_model = mapping_data.get('pid_to_model', {})
    model_to_u9 = mapping_data.get('model_to_u9', {})
    u9_to_stp = mapping_data.get('u9_to_stp', {})

    # 尝试匹配
    # 步骤1: 查找型号代码
    matched_model = None
    for pid_name, info in pid_to_model.items():
        if info['model_code'] == model_code:
            matched_model = info
            break

    if not matched_model:
        return None

    # 步骤2: 获取U9码
    u9 = matched_model['u9']

    # 步骤3: 查找STP零件
    stp_info = u9_to_stp.get(u9)

    if stp_info:
        return {
            'model_code': model_code,
            'u9': u9,
            'stp_name': stp_info['stp_name'],
            'model_file': stp_info['model_file'],
            'match_confidence': 1.0,
            'match_reason': f'通过型号代码"{model_code}"匹配到U9码"{u9}"'
        }

    return None


def enhanced_match_components(pid, bom):
    """
    增强版组件匹配（集成U9码映射）

    Args:
        pid: PID图数据
        bom: BOM点料表数据

    Returns:
        匹配结果列表
    """
    # 加载VMB-8A映射
    vmb8a_mapping = load_vmb8a_mapping()

    if not vmb8a_mapping:
        print("警告：未找到VMB-8A映射数据，使用默认匹配")
        return []

    matched_results = []

    # 遍历所有PID组件
    for comp in pid.components:
        # 尝试使用U9码匹配
        match_result = match_component_with_u9(
            comp.component_type,
            comp.pipe_size,
            vmb8a_mapping
        )

        if match_result:
            matched_results.append({
                'pid_node_id': comp.node_id,
                'pid_type': comp.component_type,
                'pid_pipe_size': comp.pipe_size,
                'model_code': match_result['model_code'],
                'u9': match_result['u9'],
                'stp_name': match_result['stp_name'],
                'model_file': match_result['model_file'],
                'match_confidence': match_result['match_confidence'],
                'match_reason': match_result['match_reason']
            })
        else:
            # 未匹配
            matched_results.append({
                'pid_node_id': comp.node_id,
                'pid_type': comp.component_type,
                'pid_pipe_size': comp.pipe_size,
                'model_code': None,
                'u9': None,
                'stp_name': None,
                'model_file': None,
                'match_confidence': 0.0,
                'match_reason': '未找到匹配的型号'
            })

    return matched_results


def test_enhanced_matcher():
    """测试增强版匹配器"""
    print("="*70)
    print("测试增强版组件匹配器（基于U9码）")
    print("="*70)

    # 模拟PID组件
    test_cases = [
        ('MV', '1/2"', '手动隔膜阀 12'),
        ('AV', '1"', '气动隔膜阀 1'),
        ('PIPE', '1/4"', '管道14'),
    ]

    # 加载映射
    vmb8a_mapping = load_vmb8a_mapping()

    if not vmb8a_mapping:
        print("错误：未找到VMB-8A映射数据")
        return

    print(f"\n加载映射数据: {vmb8a_mapping['stats']}")

    # 测试匹配
    print("\n测试匹配:")
    for pid_type, pipe_size, expected_model in test_cases:
        result = match_component_with_u9(pid_type, pipe_size, vmb8a_mapping)

        if result:
            status = "✓" if result['model_code'] == expected_model else "✗"
            print(f"{status} {pid_type} {pipe_size}")
            print(f"  -> 型号: {result['model_code']}")
            print(f"  -> U9: {result['u9']}")
            print(f"  -> STP: {result['stp_name']}")
        else:
            print(f"✗ {pid_type} {pipe_size} -> 未匹配")


if __name__ == "__main__":
    test_enhanced_matcher()