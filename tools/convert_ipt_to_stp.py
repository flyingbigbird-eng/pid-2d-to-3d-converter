#!/usr/bin/env python3
"""
批量转换.ipt文件为.stp格式
需要安装FreeCAD: https://www.freecadweb.org/

使用方法:
1. 安装FreeCAD
2. 运行: python convert_ipt_to_stp.py
"""

import os
import sys
import shutil

def convert_ipt_to_stp_with_freecad(source_dir, target_dir):
    """使用FreeCAD转换.ipt为.stp"""
    try:
        import FreeCAD
        import Import
    except ImportError:
        print("错误: 需要安装FreeCAD")
        print("下载地址: https://www.freecadweb.org/")
        return 0

    # 创建目标目录
    os.makedirs(target_dir, exist_ok=True)

    count = 0
    ipt_files = [f for f in os.listdir(source_dir) if f.endswith('.ipt')]

    print(f"找到 {len(ipt_files)} 个.ipt文件")

    for ipt_file in ipt_files:
        ipt_path = os.path.join(source_dir, ipt_file)
        stp_file = ipt_file.replace('.ipt', '.stp')
        stp_path = os.path.join(target_dir, stp_file)

        try:
            # FreeCAD打开.ipt并导出为.stp
            Import.import(s=ipt_path, t=stp_path)
            print(f"✓ {ipt_file} -> {stp_file}")
            count += 1
        except Exception as e:
            print(f"✗ {ipt_file}: {e}")

    return count

def convert_with_online_service(source_dir, target_dir):
    """使用在线转换服务的替代方案（需手动操作）"""
    print("=== 在线转换方案 ===")
    print("如果无法安装FreeCAD，可以使用在线转换服务：")
    print("1. https://www.online-convert.com/（支持.ipt转.stp）")
    print("2. https://cad_converter.online/")
    print()
    print("或者：")
    print("1. 安装Autodesk Fusion 360（免费试用）")
    print("2. 批量打开.ipt并导出为.stp")

    return 0

def main():
    # 配置路径
    source_dir = r"E:\data\23D转换\VMB素材\VMB素材\VMB-8A-模型库"
    target_dir = r"E:\data\23D转换\VMB素材\VMB素材\VMB-8A-模型库-STP"

    print("=== .ipt 转 .stp 批量转换工具 ===")
    print(f"源目录: {source_dir}")
    print(f"目标目录: {target_dir}")
    print()

    # 尝试使用FreeCAD转换
    count = convert_ipt_to_stp_with_freecad(source_dir, target_dir)

    if count == 0:
        # FreeCAD不可用，提供替代方案
        convert_with_online_service(source_dir, target_dir)
    else:
        print(f"\n转换完成！共 {count} 个文件")

if __name__ == "__main__":
    main()