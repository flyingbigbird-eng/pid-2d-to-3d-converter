"""
解析VMB-8A三维装配体STP文件
===============================

目标：
  1. 提取所有PRODUCT实体及其名称
  2. 分析装配体层级结构
  3. 建立零件代号与PRODUCT的映射关系
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.step_filter import parse_step_file, _decode_step_name
import json
import re


def analyze_assembly_structure(stp_path: str):
    """分析装配体结构，提取所有PRODUCT"""
    print(f"解析装配体: {stp_path}")

    # 解析STEP文件
    step_data = parse_step_file(stp_path)

    products = step_data.get('products', {})
    entities = step_data.get('entities', {})

    print(f"\n找到 {len(products)} 个PRODUCT实体")

    # 提取所有PRODUCT名称
    product_list = []
    for prod_id, prod_info in products.items():
        name = prod_info.get('name', '')
        decoded_name = _decode_step_name(name)
        product_list.append({
            'id': prod_id,
            'raw_name': name,
            'decoded_name': decoded_name
        })

    # 按名称排序
    product_list.sort(key=lambda x: x['decoded_name'])

    print(f"\n前20个PRODUCT:")
    for i, p in enumerate(product_list[:20], 1):
        print(f"{i}. {p['decoded_name']}")

    if len(product_list) > 20:
        print(f"... 还有 {len(product_list)-20} 个PRODUCT")

    return product_list


def match_with_material_list(product_list, d3_material_list):
    """
    将PRODUCT名称与三维材料清单匹配

    Args:
        product_list: PRODUCT列表
        d3_material_list: 三维材料清单 {零件代号: U9码}

    Returns:
        匹配结果 {零件代号: PRODUCT名称}
    """
    matches = {}
    unmatched_parts = []

    # 反向映射：从U9码找零件代号
    u9_to_parts = {}
    for part_name, u9 in d3_material_list.items():
        if u9 not in u9_to_parts:
            u9_to_parts[u9] = []
        u9_to_parts[u9].append(part_name)

    # 遍历所有PRODUCT，尝试匹配
    for prod in product_list:
        prod_name = prod['decoded_name']

        # 尝试各种匹配规则
        # 规则1: 精确匹配（壳体SS1, 壳体PP1等）
        if prod_name in d3_material_list:
            matches[prod_name] = prod_name
            continue

        # 规则2: 包含关系（"手动隔膜阀 1/2"" vs "手动隔膜阀 12"）
        for part_name in d3_material_list.keys():
            # 处理1/2" -> 12的转换
            part_normalized = part_name.replace('1/2"', '12').replace('1/4"', '14').replace('1"', '1')
            if part_normalized in prod_name or prod_name in part_normalized:
                matches[part_name] = prod_name
                break

    # 找出未匹配的零件
    for part_name in d3_material_list.keys():
        if part_name not in matches:
            unmatched_parts.append(part_name)

    print(f"\n匹配结果:")
    print(f"  成功匹配: {len(matches)} 个")
    print(f"  未匹配: {len(unmatched_parts)} 个")

    if unmatched_parts:
        print(f"\n未匹配的零件:")
        for part in unmatched_parts[:10]:
            print(f"  - {part}")
        if len(unmatched_parts) > 10:
            print(f"  ... 还有 {len(unmatched_parts)-10} 个")

    return matches, unmatched_parts


def main():
    """主函数"""
    # 路径配置
    stp_path = r"E:\data\23D转换\VMB素材\VMB素材\VMB-8A-三维图及材料\VMB-8A-三维图.stp"
    mapping_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_u9_mapping.json"
    output_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_assembly_mapping.json"

    # 1. 解析装配体STP
    product_list = analyze_assembly_structure(stp_path)

    # 2. 加载三维材料清单映射
    with open(mapping_path, 'r', encoding='utf-8') as f:
        u9_mapping = json.load(f)

    d3_material_list = u9_mapping['d3_part_to_u9']

    # 3. 匹配PRODUCT与零件代号
    matches, unmatched = match_with_material_list(product_list, d3_material_list)

    # 4. 导出完整映射
    result = {
        'stp_path': stp_path,
        'total_products': len(product_list),
        'total_parts': len(d3_material_list),
        'matched': len(matches),
        'unmatched': len(unmatched),
        'product_list': product_list,
        'part_to_product': matches,
        'unmatched_parts': unmatched
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n映射结果已保存: {output_path}")

    return result


if __name__ == "__main__":
    result = main()