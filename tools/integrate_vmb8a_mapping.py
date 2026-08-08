"""
VMB-8A学习集成测试
==================

测试完整的匹配链路：
  PID组件 -> 型号代码 -> U9码 -> STP零件
"""

import json
import sys
import os

# 添加模块路径
sys.path.insert(0, r"E:\workbuddy\2026-07-31-15-46-13\23d_converter")

from tools.enhanced_matcher import load_vmb8a_mapping, match_component_with_u9


def test_full_pipeline():
    """测试完整匹配链路"""
    print("="*80)
    print("VMB-8A学习集成测试")
    print("="*80)

    # 加载映射数据
    print("\n1. 加载映射数据...")
    vmb8a_mapping = load_vmb8a_mapping()

    if not vmb8a_mapping:
        print("   ✗ 加载失败")
        return

    stats = vmb8a_mapping['stats']
    print(f"   ✓ 加载成功")
    print(f"   - PID组件规则: {stats['total_pid_components']} 条")
    print(f"   - 唯一型号数: {stats['unique_model_codes']} 个")
    print(f"   - STP零件数: {stats['total_stp_parts']} 个")

    # 测试匹配链路
    print("\n2. 测试匹配链路...")

    test_cases = [
        # (PID类型, 管径, 期望型号, 期望U9)
        ('MV', '1/2"', '手动隔膜阀 12', '000000121786'),
        ('MV', '1"', '手动隔膜阀 1', '000000125108'),
        ('AV', '1/2"', '气动隔膜阀 12', '000000123141'),
        ('AV', '1"', '气动隔膜阀 1', '000000122589'),
        ('MV', '1/4"', '手动隔膜阀 14', '000000122588'),
    ]

    success_count = 0

    for pid_type, pipe_size, expected_model, expected_u9 in test_cases:
        result = match_component_with_u9(pid_type, pipe_size, vmb8a_mapping)

        if result:
            # 验证匹配结果
            model_match = result['model_code'] == expected_model
            u9_match = result['u9'] == expected_u9
            stp_exists = result['stp_name'] is not None

            if model_match and u9_match and stp_exists:
                print(f"   ✓ {pid_type} {pipe_size}")
                print(f"      型号: {result['model_code']}")
                print(f"      U9: {result['u9']}")
                print(f"      STP: {result['stp_name']}")
                success_count += 1
            else:
                print(f"   ✗ {pid_type} {pipe_size} - 匹配错误")
                print(f"      期望型号: {expected_model}, 实际: {result['model_code']}")
                print(f"      期望U9: {expected_u9}, 实际: {result['u9']}")
        else:
            print(f"   ✗ {pid_type} {pipe_size} - 未匹配")

    print(f"\n3. 测试结果:")
    print(f"   成功匹配: {success_count}/{len(test_cases)}")

    # 显示映射样例
    print("\n4. 映射样例展示:")

    pid_to_model = vmb8a_mapping['pid_to_model']
    u9_to_stp = vmb8a_mapping['u9_to_stp']

    print("\n   PID组件 -> 型号代码 -> U9码 -> STP零件")
    print("   " + "-"*60)

    for i, (pid_name, info) in enumerate(list(pid_to_model.items())[:5], 1):
        model_code = info['model_code']
        u9 = info['u9']
        stp_info = u9_to_stp.get(u9, {})

        print(f"   {i}. {pid_name}")
        print(f"      -> {model_code}")
        print(f"      -> {u9}")
        print(f"      -> {stp_info.get('stp_name', 'N/A')}")

    print("\n" + "="*80)
    print("集成测试完成")
    print("="*80)


def show_mapping_statistics():
    """显示映射统计信息"""
    vmb8a_mapping = load_vmb8a_mapping()

    if not vmb8a_mapping:
        return

    print("\n映射统计:")
    print(f"  PID组件规则: {len(vmb8a_mapping['pid_to_model'])} 条")
    print(f"  型号代码数: {len(vmb8a_mapping['model_to_u9'])} 个")
    print(f"  U9码零件数: {len(vmb8a_mapping['u9_to_stp'])} 个")

    # 按类型统计
    pid_to_model = vmb8a_mapping['pid_to_model']

    type_count = {}
    for info in pid_to_model.values():
        comp_type = info['component_type']
        type_count[comp_type] = type_count.get(comp_type, 0) + 1

    print("\n组件类型分布:")
    for comp_type, count in sorted(type_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  {comp_type}: {count} 个")


if __name__ == "__main__":
    test_full_pipeline()
    show_mapping_statistics()