"""
解析VMB-8A三维装配体STP文件（增强版）
========================================

支持解码STEP文件中的中文名称：
  - \X\BF\X\C7\... 格式（单字节十六进制）
  - \X2\...\X0\ 格式（双字节十六进制）
"""

import sys
import os
import re
import json


def decode_step_name_v2(encoded_name: str) -> str:
    """
    解码STEP文件中的名称（支持多种编码格式）

    格式1: \X\XX 单字节十六进制（如\X\BF）
    格式2: \X2\XXXX\X0\ 双字节十六进制（如\X2\8FDB\X0\）
    """
    result = encoded_name

    # 先处理 \X2\...\X0\ 格式（双字节十六进制）
    pattern_x2 = r'\\X2\\([0-9A-Fa-f]{4})\\X0\\'
    while True:
        match = re.search(pattern_x2, result)
        if not match:
            break
        hex_code = match.group(1)
        try:
            char_code = int(hex_code, 16)
            char = chr(char_code)
            result = result[:match.start()] + char + result[match.end():]
        except:
            break

    # 再处理 \X\XX 格式（单字节十六进制）
    pattern_x = r'\\X\\([0-9A-Fa-f]{2})'
    while True:
        match = re.search(pattern_x, result)
        if not match:
            break
        hex_code = match.group(1)
        try:
            char_code = int(hex_code, 16)
            # 单字节字符需要判断是否为ASCII
            if char_code < 128:
                char = chr(char_code)
            else:
                # GBK编码（中文通常2字节）
                char = f'\\x{hex_code.lower()}'
            result = result[:match.start()] + char + result[match.end():]
        except:
            break

    # 处理GBK双字节字符（连续的\xNN\xNN）
    try:
        # 尝试解码为GBK
        result_bytes = result.encode('latin-1')
        result = result_bytes.decode('gbk', errors='ignore')
    except:
        pass

    return result


def extract_all_products(stp_path: str):
    """从STP文件提取所有PRODUCT实体"""
    products = []

    with open(stp_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # 匹配PRODUCT定义行
            # 格式: #123=PRODUCT('名称','描述',...);
            match = re.match(r'^#(\d+)\s*=\s*PRODUCT\s*\(\s*\'(.+?)\'', line)
            if match:
                prod_id = match.group(1)
                raw_name = match.group(2)
                decoded_name = decode_step_name_v2(raw_name)

                products.append({
                    'id': prod_id,
                    'raw_name': raw_name,
                    'decoded_name': decoded_name
                })

    return products


def analyze_vmb8a_assembly():
    """分析VMB-8A装配体"""
    stp_path = r"E:\data\23D转换\VMB素材\VMB素材\VMB-8A-三维图及材料\VMB-8A-三维图.stp"
    mapping_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_u9_mapping.json"
    output_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_assembly_products.json"

    print("="*70)
    print("解析VMB-8A三维装配体STP文件")
    print("="*70)

    # 1. 提取所有PRODUCT实体
    print(f"\n正在解析: {stp_path}")
    products = extract_all_products(stp_path)
    print(f"找到 {len(products)} 个PRODUCT实体\n")

    # 2. 显示前30个PRODUCT
    print("前30个PRODUCT:")
    for i, p in enumerate(products[:30], 1):
        print(f"{i:3d}. [{p['id']}] {p['decoded_name']}")

    if len(products) > 30:
        print(f"... 还有 {len(products)-30} 个PRODUCT")

    # 3. 加载三维材料清单
    with open(mapping_path, 'r', encoding='utf-8') as f:
        u9_mapping = json.load(f)

    d3_parts = u9_mapping['d3_part_to_u9']
    print(f"\n三维材料清单零件数: {len(d3_parts)}")

    # 4. 尝试匹配（基于名称相似度）
    matches = {}
    for prod in products:
        prod_name = prod['decoded_name']

        # 尝试匹配规则
        # 规则1: 精确匹配
        if prod_name in d3_parts:
            matches[prod_name] = {
                'product_id': prod['id'],
                'u9': d3_parts[prod_name]
            }
            continue

        # 规则2: 包含关系（处理1/2" -> 12等转换）
        for part_name in d3_parts.keys():
            # 标准化比较
            prod_normalized = prod_name.replace('/', '').replace('"', '').replace(' ', '')
            part_normalized = part_name.replace('/', '').replace('"', '').replace(' ', '')

            if prod_normalized == part_normalized or \
               prod_normalized in part_normalized or \
               part_normalized in prod_normalized:
                matches[part_name] = {
                    'product_id': prod['id'],
                    'u9': d3_parts[part_name],
                    'matched_product': prod_name
                }
                break

    print(f"\n匹配结果: {len(matches)} / {len(d3_parts)}")

    # 5. 显示匹配结果
    print("\n匹配样例（前20个）:")
    for i, (part_name, match_info) in enumerate(list(matches.items())[:20], 1):
        prod_name = match_info.get('matched_product', part_name)
        print(f"{i:3d}. {part_name} -> [{match_info['product_id']}] {prod_name}")

    # 6. 导出结果
    result = {
        'total_products': len(products),
        'total_parts': len(d3_parts),
        'matched': len(matches),
        'products': products,
        'matches': matches
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存: {output_path}")

    return result


if __name__ == "__main__":
    result = analyze_vmb8a_assembly()