#!/usr/bin/env python3
"""
VMB-8A学习脚本（新版）
建立三层映射：PID组件 → 型号代码 → U9码 → STP零件
"""

import os
import json
import re
from pathlib import Path

# 配置路径
BASE_DIR = Path(r"E:\data\23D转换\VMB资料new")
BOM_FILE = BASE_DIR / "VMB-8A-BOM点料表.xls"
D2_DIR = BASE_DIR / "VMB-8A-二维图及材料"
D3_DIR = BASE_DIR / "VMB-8A-三维图及材料"
MODEL_DIR = BASE_DIR / "VMB-8A-模型库"

OUTPUT_DIR = Path(r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge")

def parse_bom_excel(filepath):
    """解析BOM点料表，提取U9码和物料信息"""
    import xlrd
    
    wb = xlrd.open_workbook(filepath, encoding_override='gbk')
    ws = wb.sheet_by_index(0)
    
    bom_data = {}
    
    # 跳过表头，从第2行开始（索引1）
    # 列顺序：序号、物料名称、材质、单位、供应商、U9码（36）、数量
    for row_idx in range(1, ws.nrows):
        row = ws.row_values(row_idx)
        if not row or not row[0]:
            continue
        
        # 提取U9码（第6列，索引5）
        u9_code = str(row[5]).strip() if len(row) > 5 and row[5] else None
        name = str(row[1]).strip() if len(row) > 1 else None
        material = str(row[2]).strip() if len(row) > 2 else ""
        quantity = int(row[6]) if len(row) > 6 and row[6] else 0
        
        if u9_code and u9_code.isdigit() and len(u9_code) == 12:
            # 提取型号信息（从名称中）
            model_code = extract_model_code(name, "")
            bom_data[u9_code] = {
                'name': name,
                'material': material,
                'quantity': quantity,
                'model_code': model_code
            }
    
    print(f"✓ BOM点料表解析完成：{len(bom_data)}个物料")
    return bom_data

def parse_2d_material_list(filepath):
    """解析二维材料清单（盘面物料）"""
    import xlrd
    
    wb = xlrd.open_workbook(filepath, encoding_override='gbk')
    ws = wb.sheet_by_index(0)
    
    d2_materials = {}
    
    # 跳过表头，从第2行开始（索引1）
    # 列顺序：序号、物料名称、材质、规格+订货号+描述、单位、供应商、U9码（36）、数量
    for row_idx in range(1, ws.nrows):
        row = ws.row_values(row_idx)
        if not row or not row[0]:
            continue
        
        name = str(row[1]).strip() if len(row) > 1 else None
        material = str(row[2]).strip() if len(row) > 2 else ""
        spec = str(row[3]).strip() if len(row) > 3 else ""
        u9_code = str(row[6]).strip() if len(row) > 6 and row[6] else None
        
        if u9_code and u9_code.isdigit() and len(u9_code) == 12:
            model_code = extract_model_code(name, spec)
            d2_materials[name] = {
                'spec': spec,
                'material': material,
                'u9_code': u9_code,
                'model_code': model_code
            }
    
    print(f"✓ 二维材料清单解析完成：{len(d2_materials)}个盘面物料")
    return d2_materials

def parse_3d_material_list(filepath):
    """解析三维材料清单（零件实例）"""
    import openpyxl
    
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    
    d3_parts = {}
    
    # 跳过表头，从第2行开始
    # 列顺序：项目、零件代号、缩略图、数量、库存编号（32）、改名
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        
        part_code = str(row[1]).strip() if len(row) > 1 and row[1] else None
        quantity = int(row[3]) if len(row) > 3 and row[3] else 1
        u9_code = str(row[4]).strip() if len(row) > 4 and row[4] else None
        
        if u9_code and u9_code.isdigit() and len(u9_code) == 12:
            d3_parts[part_code] = {
                'name': part_code,  # 零件代号就是名称
                'qty': quantity,
                'u9_code': u9_code
            }
    
    print(f"✓ 三维材料清单解析完成：{len(d3_parts)}个零件实例")
    return d3_parts

def build_u9_to_model_mapping(model_dir):
    """从模型库文件名提取U9码映射"""
    u9_to_model = {}
    
    for ipt_file in Path(model_dir).glob("*.ipt"):
        # 文件名格式：U9码+名称.ipt
        filename = ipt_file.name
        match = re.match(r'^(\d{12})', filename)
        if match:
            u9_code = match.group(1)
            # 去掉.ipt后缀
            model_name = filename.replace('.ipt', '')
            u9_to_model[u9_code] = model_name
    
    print(f"✓ 模型库映射完成：{len(u9_to_model)}个.ipt文件")
    return u9_to_model

def extract_model_code(name, spec):
    """从名称中提取型号代码"""
    if not name:
        return None
    
    # 管径标准化映射
    size_map = {
        '1/4"': '14',
        '1/2"': '12',
        '3/4"': '34',
        '1"': '1',
        '1-1/4"': '114',
        '1-1/2"': '112',
        '2"': '2'
    }
    
    # 特殊处理：直接从名称提取型号（去掉引号和斜杠）
    # 例如："手动隔膜阀 1/2"" → "手动隔膜阀 12"
    # "等径三通1/2"x1/2"x1/2" → "等径三通12x12x12"
    
    result = name.strip()
    
    # 替换所有管径格式
    for old_size, new_size in size_map.items():
        result = result.replace(old_size, new_size)
    
    # 去掉剩余的引号
    result = result.replace('"', '').replace("'", '')
    
    return result

def build_pid_to_model_rules(d2_materials):
    """建立PID组件→型号代码的映射规则"""
    # 常见PID图标缩写
    pid_abbr_map = {
        'MV': '手动隔膜阀',
        'AV': '气动隔膜阀',
        'BV': '球阀',
        'CV': '单向阀',
        'PIPE': '管道',
        'TEE': '三通',
        'ELBOW': '弯头'
    }
    
    pid_rules = {}
    
    for d2_name, info in d2_materials.items():
        model_code = info['model_code']
        u9_code = info['u9_code']
        
        # 从名称推断PID类型
        for abbr, full_name in pid_abbr_map.items():
            if full_name in d2_name:
                # 从名称中提取管径信息
                # 规则：1/2"→12, 1/4"→14, 3/4"→34, 1"→1
                size = None
                
                if '1/2"' in d2_name or '1/2' in d2_name:
                    size = '12'
                elif '1/4"' in d2_name or '1/4' in d2_name:
                    size = '14'
                elif '3/4"' in d2_name or '3/4' in d2_name:
                    size = '34'
                elif '1"' in d2_name:
                    size = '1'
                
                # 特殊处理：三通和弯头需要完整尺寸
                # 例如："等径三通1/2"x1/2"x1/2"" → "等径三通12x12x12"
                # "异径三通1"x1/2"x1"" → "异径三通1x12x1"
                if abbr in ['TEE', 'ELBOW']:
                    # 从model_code提取完整尺寸作为key
                    # 例如："等径三通12x12x12" → "TEE 12x12x12"
                    # "异径三通1x12x1" → "TEE 1x12x1"
                    size_parts = model_code.replace('等径三通', '').replace('异径三通', '').replace('等径弯头', '').replace('异径弯头', '')
                    if size_parts:
                        pid_key = f"{abbr} {size_parts}"
                    else:
                        pid_key = f"{abbr} {size}" if size else abbr
                else:
                    # PID规则：MV 12 → 手动隔膜阀 12
                    if size:
                        pid_key = f"{abbr} {size}"
                    else:
                        pid_key = abbr
                
                pid_rules[pid_key] = {
                    'model_code': model_code,
                    'u9_code': u9_code,
                    'full_name': d2_name
                }
                break
    
    print(f"✓ PID规则建立完成：{len(pid_rules)}条规则")
    return pid_rules

def extract_size_from_spec(spec):
    """从规格中提取管径"""
    if not spec:
        return None
    
    # 匹配管径模式
    patterns = [
        r'(\d+["\'])',  # 1", 1/2", 3/4"
        r'(\d+/\d+["\'])',  # 1/2", 3/4"
        r'DN(\d+)',  # DN20, DN25
        r'(\d+)',  # 纯数字
    ]
    
    for pattern in patterns:
        match = re.search(pattern, spec)
        if match:
            return match.group(1).replace('"', '').replace("'", '')
    
    return None

def build_complete_mapping(bom_data, d2_materials, d3_parts, u9_to_model):
    """构建完整的三层映射"""
    complete_mapping = {
        'pid_rules': {},
        'model_to_u9': {},
        'u9_to_stp': {},
        'stats': {
            'bom_count': len(bom_data),
            'd2_count': len(d2_materials),
            'd3_count': len(d3_parts),
            'model_count': len(u9_to_model)
        }
    }
    
    # 层1：PID组件→型号代码
    pid_rules = build_pid_to_model_rules(d2_materials)
    complete_mapping['pid_rules'] = pid_rules
    
    # 层2：型号代码→U9码
    for u9_code, info in bom_data.items():
        model_code = info['model_code']
        if model_code:
            complete_mapping['model_to_u9'][model_code] = u9_code
    
    # 层3：U9码→STP零件
    complete_mapping['u9_to_stp'] = u9_to_model
    
    return complete_mapping

def main():
    print("=" * 60)
    print("VMB-8A学习脚本（新版）")
    print("=" * 60)
    
    # 1. 解析BOM点料表
    print("\n[1/5] 解析BOM点料表...")
    bom_data = parse_bom_excel(BOM_FILE)
    
    # 2. 解析二维材料清单
    print("\n[2/5] 解析二维材料清单...")
    d2_material_list = D2_DIR / "VMB-8A-二维材料清单.xls"
    d2_materials = parse_2d_material_list(d2_material_list)
    
    # 3. 解析三维材料清单
    print("\n[3/5] 解析三维材料清单...")
    d3_material_list = D3_DIR / "VMB-8A-三维材料清单.xlsx"
    d3_parts = parse_3d_material_list(d3_material_list)
    
    # 4. 建立模型库映射
    print("\n[4/5] 建立模型库映射...")
    u9_to_model = build_u9_to_model_mapping(MODEL_DIR)
    
    # 5. 构建完整映射
    print("\n[5/5] 构建完整三层映射...")
    complete_mapping = build_complete_mapping(bom_data, d2_materials, d3_parts, u9_to_model)
    
    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "VMB-8A_new_mapping.json"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(complete_mapping, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 学习完成！结果保存到：{output_file}")
    print(f"\n统计信息：")
    print(f"  - BOM点料表：{complete_mapping['stats']['bom_count']}个物料")
    print(f"  - 二维材料清单：{complete_mapping['stats']['d2_count']}个盘面物料")
    print(f"  - 三维材料清单：{complete_mapping['stats']['d3_count']}个零件实例")
    print(f"  - 模型库：{complete_mapping['stats']['model_count']}个.ipt文件")
    print(f"  - PID规则：{len(complete_mapping['pid_rules'])}条")
    print(f"  - 型号-U9映射：{len(complete_mapping['model_to_u9'])}个")

if __name__ == "__main__":
    main()