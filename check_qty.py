import sys, os
sys.path.insert(0, '/home/ecs-user/pid_3d_converter')
from modules.learning_engine import load_knowledge, apply_knowledge
from modules.step_filter import extract_model_codes

BASE = '/home/ecs-user/pid_3d_converter'
kl = load_knowledge(BASE + '/data/knowledge')

for label, dxf, bom in [
    ('8A', BASE+'/data/uploads/8A/VMB-8A-PID文件.dxf', BASE+'/data/uploads/8A/VMB-8A-点料表.xls'),
    ('8A测试', BASE+'/data/uploads/8A测试/VMB-8A-PID文件(改版1).dxf', BASE+'/data/uploads/8A测试/VMB-8A-点料表(改版1).xls')]:
    mr = apply_knowledge(dxf, bom, kl)
    print('='*55)
    print(label, '| 匹配组件:', mr.summary.get('matched_components'))
    # 汇总每种型号代码的总物料数量
    from collections import defaultdict
    code_qty = defaultdict(float)
    code_comp_count = defaultdict(int)
    for comp in mr.components:
        if comp.matched_material:
            m = comp.matched_material
            if m.category in ('valve','fitting','sensor'):
                codes = extract_model_codes(m.spec or '', m.name or '')
                if codes:
                    code = codes[0]
                    code_qty[code] += m.quantity  # 点料表数量
                    code_comp_count[code] += 1    # PID组件数
    print('  型号 -> 点料表数量 / PID组件数')
    for code in sorted(code_qty):
        print('    %-14s %7.0f / %d' % (code, code_qty[code], code_comp_count[code]))
