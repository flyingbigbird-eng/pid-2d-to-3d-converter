"""
VMB-8A 完整映射构建工具
=======================

基于型号代码建立三层映射：
  1. PID组件类型/管径 -> 型号代码（标准化命名）
  2. 型号代码 -> U9码（从BOM点料表）
  3. U9码 -> STP零件（从模型库文件名）
"""

import json
import re


def normalize_pipe_size(size_str: str) -> str:
    """
    标准化管径表示
    1/4" -> 14
    1/2" -> 12
    3/4" -> 34
    1" -> 1
    """
    size_map = {
        '1/4"': '14',
        '1/2"': '12',
        '3/4"': '34',
        '1"': '1',
        '1-1/4"': '114',
        '1-1/2"': '112',
        '2"': '2'
    }
    return size_map.get(size_str, size_str.replace('/', '').replace('"', ''))


def build_complete_mapping():
    """构建完整的三层映射"""
    
    # 加载U9映射基础数据
    u9_mapping_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_u9_mapping.json"
    with open(u9_mapping_path, 'r', encoding='utf-8') as f:
        u9_data = json.load(f)
    
    d2_name_to_u9 = u9_data['d2_name_to_u9']  # 二维材料清单
    u9_to_model = u9_data['u9_to_model']      # U9->模型库
    
    # 层1: PID组件 -> 型号代码
    pid_to_model = {}
    
    # 从二维材料清单提取规则
    for d2_name, (u9, material) in d2_name_to_u9.items():
        # 提取组件类型和管径
        # 例: "手动隔膜阀 1/2\"" -> type="手动隔膜阀", size="1/2\""
        match = re.match(r'(.+?)\s+(\d+/\d+["\']|\d+["\'])', d2_name)
        if match:
            comp_type = match.group(1).strip()
            pipe_size = match.group(2).strip()
            
            # 标准化管径
            size_abbr = normalize_pipe_size(pipe_size)
            
            # 构建型号代码
            model_code = f"{comp_type} {size_abbr}"
            
            pid_to_model[d2_name] = {
                'component_type': comp_type,
                'pipe_size': pipe_size,
                'size_abbr': size_abbr,
                'model_code': model_code,
                'u9': u9,
                'material': material
            }
        else:
            # 无管径的组件（如壳体、标签等）
            pid_to_model[d2_name] = {
                'component_type': d2_name,
                'pipe_size': '',
                'size_abbr': '',
                'model_code': d2_name,
                'u9': u9,
                'material': material
            }
    
    # 层2: 型号代码 -> U9码（已在上一步完成）
    model_to_u9 = {}
    for d2_name, info in pid_to_model.items():
        model_code = info['model_code']
        model_to_u9[model_code] = {
            'u9': info['u9'],
            'material': info['material'],
            'pid_names': [d2_name]
        }
    
    # 层3: U9码 -> STP零件（从模型库）
    u9_to_stp = {}
    for u9, model_file in u9_to_model.items():
        # 从文件名提取型号
        # 例: "000000121786手动隔膜阀 12.ipt" -> "手动隔膜阀 12"
        name_match = re.match(r'\d{12}(.+)\.ipt$', model_file)
        if name_match:
            stp_name = name_match.group(1).strip()
        else:
            stp_name = model_file
        
        u9_to_stp[u9] = {
            'model_file': model_file,
            'stp_name': stp_name,
            'model_code': stp_name  # 型号代码=STP名称
        }
    
    # 统计
    print("="*70)
    print("VMB-8A 三层映射构建完成")
    print("="*70)
    print(f"层1: PID组件 -> 型号代码: {len(pid_to_model)} 条")
    print(f"层2: 型号代码 -> U9码: {len(model_to_u9)} 个唯一型号")
    print(f"层3: U9码 -> STP零件: {len(u9_to_stp)} 个零件")
    
    # 显示映射样例
    print("\n映射样例（前10个）:")
    for i, (pid_name, info) in enumerate(list(pid_to_model.items())[:10], 1):
        print(f"{i:2d}. {pid_name}")
        print(f"    -> 型号: {info['model_code']}")
        print(f"    -> U9: {info['u9']}")
        stp_info = u9_to_stp.get(info['u9'], {})
        print(f"    -> STP: {stp_info.get('stp_name', 'N/A')}")
    
    # 导出完整映射
    result = {
        'case_name': 'VMB-8A',
        'mapping_strategy': 'PID->型号->U9->STP 三层映射',
        'pid_to_model': pid_to_model,
        'model_to_u9': model_to_u9,
        'u9_to_stp': u9_to_stp,
        'naming_rules': {
            'inch_to_abbr': {
                '1/4"': '14',
                '1/2"': '12',
                '3/4"': '34',
                '1"': '1'
            }
        },
        'stats': {
            'total_pid_components': len(pid_to_model),
            'unique_model_codes': len(model_to_u9),
            'total_stp_parts': len(u9_to_stp)
        }
    }
    
    output_path = r"E:\workbuddy\2026-07-31-15-46-13\23d_converter\data\knowledge\VMB-8A_complete_mapping.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n完整映射已保存: {output_path}")
    
    return result


if __name__ == "__main__":
    result = build_complete_mapping()