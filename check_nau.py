import re, sys, os
sys.path.insert(0, '/home/ecs-user/pid_3d_converter')
from modules.step_filter import parse_step_file, _decode_step_name, _extract_refs

# 检查8A-三维库里 PLMV-K16 相关的 NAU 和几何结构
path = '/home/ecs-user/pid_3d_converter/data/library/VMB-8A-三维文件.stp'
entities = parse_step_file(path)

# 找 PLMV-K16 PRODUCT
plmv16_prod = None
for eid, (et, line) in entities.items():
    if et == 'PRODUCT':
        names = [_decode_step_name(n) for n in re.findall(r"'([^']*)'", line)]
        if any('PLMV-K16' == n.strip() for n in names):
            plmv16_prod = eid
            print('PLMV-K16 PRODUCT #%s: %s' % (eid, names))
            break

if not plmv16_prod:
    print('未找到 PLMV-K16')
    sys.exit()

# 找该PRODUCT的PD
pd_to_product = {}
reverse = {}
for eid, (et, line) in entities.items():
    refs = _extract_refs(line)
    reverse.setdefault(eid, set())
    for r in refs:
        if r != eid:
            reverse.setdefault(r, set()).add(eid)

# PRODUCT -> PDF -> PD (正向)
pdfs = []
for r in reverse.get(plmv16_prod, set()):
    if entities.get(r, ('',))[0] == 'PRODUCT_DEFINITION_FORMATION':
        pdfs.append(r)
print('PLMV-K16 的 PDF:', pdfs)

pds = []
for pdf in pdfs:
    for r in reverse.get(pdf, set()):
        if entities.get(r, ('',))[0] == 'PRODUCT_DEFINITION':
            pds.append(r)
print('PLMV-K16 的 PD:', pds)

# 找这些PD的NAU
print('\nPLMV-K16 的所有 NEXT_ASSEMBLY_USAGE_OCCURRENCE:')
nau_count = 0
for eid, (et, line) in entities.items():
    if et == 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
        refs = _extract_refs(line)
        # NAU 最后一个product相关的PD引用
        if any(pd in refs for pd in pds):
            nau_count += 1
            print('  #%s: %s' % (eid, line[:160]))
print('NAU总数:', nau_count)
