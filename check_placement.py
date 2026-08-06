import re, sys, os
sys.path.insert(0, '/home/ecs-user/pid_3d_converter')
from modules.step_filter import parse_step_file, _decode_step_name, _extract_refs

path = '/home/ecs-user/pid_3d_converter/data/library/VMB-8A-三维文件.stp'
entities = parse_step_file(path)

# 1. 找 PLMV-K08 的PD
plmv08_prod = None
for eid,(et,line) in entities.items():
    if et=='PRODUCT':
        names=[_decode_step_name(n) for n in re.findall(r"'([^']*)'", line)]
        if any(n.strip()=='PLMV-K08' for n in names):
            plmv08_prod=eid; break

reverse={}
for eid,(et,line) in entities.items():
    for r in _extract_refs(line):
        if r!=eid: reverse.setdefault(r,set()).add(eid)

# PRODUCT -> PDF -> PD
pdfs=[r for r in reverse.get(plmv08_prod,set()) if entities.get(r,('',))[0]=='PRODUCT_DEFINITION_FORMATION']
pds=[]
for pdf in pdfs:
    for r in reverse.get(pdf,set()):
        if entities.get(r,('',))[0]=='PRODUCT_DEFINITION': pds.append(r)
print('PLMV-K08 PD:', pds)

# 找所有NAU
naus=[]
for eid,(et,line) in entities.items():
    if et=='NEXT_ASSEMBLY_USAGE_OCCURRENCE':
        refs=_extract_refs(line)
        if any(pd in refs for pd in pds):
            naus.append((eid,refs))
print('PLMV-K08 NAU数:', len(naus))
for eid,refs in naus[:8]:
    print('  NAU #%s refs=%s'%(eid,refs))
    print('    line:', entities[eid][1][:150])
    # NAU关联的变换: 从NAU的item (第6个字段通常)? 实际STEP NAU关联变换是通过其引用的PDS/SDR/SRR_WITH_TRANSFORMATION
