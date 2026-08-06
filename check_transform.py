import re, sys
sys.path.insert(0, '/home/ecs-user/pid_3d_converter')
from modules.step_filter import parse_step_file, _decode_step_name, _extract_refs

path = '/home/ecs-user/pid_3d_converter/data/library/VMB-8A-三维文件.stp'
entities = parse_step_file(path)

reverse={}
for eid,(et,line) in entities.items():
    for r in _extract_refs(line):
        if r!=eid: reverse.setdefault(r,set()).add(eid)

# 看NAU #2198 和 #2212 的完整子装配链：NAU -> child PD(759218) -> PDS -> SDR -> SRR -> placement
for nau in ['2198','2212']:
    child_pd = None
    refs = _extract_refs(entities[nau][1])
    for r in refs:
        if r!=nau and entities.get(r,('',))[0]=='PRODUCT_DEFINITION':
            child_pd=r; break
    print('='*55)
    print('NAU #%s -> child PD #%s'%(nau, child_pd))
    if not child_pd: continue
    # PD反向找PDS
    for pds in reverse.get(child_pd,set()):
        if entities.get(pds,('',))[0]=='PRODUCT_DEFINITION_SHAPE':
            print('  PDS #%s: %s'%(pds, entities[pds][1][:120]))
            # PDS反向找SDR
            for sdr in reverse.get(pds,set()):
                if entities.get(sdr,('',))[0]=='SHAPE_DEFINITION_REPRESENTATION':
                    print('  SDR #%s: %s'%(sdr, entities[sdr][1][:150]))
                    # SDR反向找SRR
                    for sr in reverse.get(sdr,set()):
                        if entities.get(sr,('',))[0]:
                            pass
                    # SDR正向引用SR
                    for sr in _extract_refs(entities[sdr][1]):
                        if sr!=sdr and entities.get(sr,('',))[0]=='SHAPE_REPRESENTATION':
                            print('    SR #%s: %s'%(sr, entities[sr][1][:150]))
                            # SR反向找 SRR_WITH_TRANSFORMATION 或 REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION
                            for srr in reverse.get(sr,set()):
                                if entities.get(srr,('',))[0]:
                                    t=entities[srr][0]
                                    if 'TRANSFORMATION' in t or 'RELATIONSHIP' in t:
                                        print('    %s #%s: %s'%(t,srr, entities[srr][1][:200]))
                                        # 找 AXIS2_PLACEMENT / ITEM_DEFINED_TRANSFORMATION
                                        for r2 in _extract_refs(entities[srr][1]):
                                            if r2!=srr and entities.get(r2,('',))[0]:
                                                t2=entities[r2][0]
                                                if 'TRANSFORMATION' in t2 or 'PLACEMENT' in t2:
                                                    print('      -> #%s %s: %s'%(r2,t2, entities[r2][1][:180]))
                                                    for r3 in _extract_refs(entities[r2][1]):
                                                        if r3!=r2 and entities.get(r3,('',))[0]:
                                                            t3=entities[r3][0]
                                                            if 'PLACEMENT' in t3 or 'CARTESIAN' in t3:
                                                                print('         -> #%s %s: %s'%(r3,t3, entities[r3][1][:120]))
