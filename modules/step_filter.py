"""
STEP文件过滤器
============================
解析原始STEP装配体文件，根据实际匹配的组件型号代码和数量，
提取对应零件的所有实体引用，生成只包含所需零件的新STEP文件。

核心改进：用BOM spec中的型号代码（如PLPV-K08、G2FATW08）精确匹配
STEP文件中的PRODUCT名称，而不是用中文名模糊匹配。

按装配实例(NEXT_ASSEMBLY_USAGE_OCCURRENCE)数量过滤，
根据实际需要的组件数量只保留对应数量的实例。
"""
import re
import os
from typing import Set, Dict, List, Tuple


def _decode_step_name(s: str) -> str:
    """解码STEP文件中的X2和X编码"""
    def replace_x2(m):
        hex_str = m.group(1)
        try:
            return bytes.fromhex(hex_str).decode('utf-8')
        except:
            return m.group(0)
    s = re.sub(r'\\X2\\([0-9A-Fa-f]+)\\X0\\', replace_x2, s)
    def replace_x(m):
        hex_str = m.group(1)
        try:
            return bytes.fromhex(hex_str).decode('latin-1')
        except:
            return m.group(0)
    s = re.sub(r'\\X\\([0-9A-Fa-f]{2})\\', replace_x, s)
    return s


def _extract_refs(line: str) -> List[str]:
    """从一行STEP内容中提取所有 #id 引用"""
    return re.findall(r'#(\d+)', line)


def parse_step_file(stp_path: str) -> Dict[str, Tuple[str, str]]:
    """解析STEP文件，返回 {entity_id: (entity_type, raw_line)}"""
    entities = {}
    current_line = []
    
    with open(stp_path, 'r', encoding='utf-8', errors='ignore') as f:
        in_data = False
        for line in f:
            stripped = line.strip()
            if stripped == 'DATA;':
                in_data = True
                continue
            if stripped == 'ENDSEC;':
                in_data = False
                continue
            if not in_data:
                continue
            current_line.append(stripped)
            if stripped.endswith(';'):
                full_line = ' '.join(current_line)
                match = re.match(r'#(\d+)\s*=\s*(\w+)\s*\(', full_line)
                if match:
                    eid = match.group(1)
                    etype = match.group(2)
                    entities[eid] = (etype, full_line)
                else:
                    match2 = re.match(r'#(\d+)\s*=\s*\(\s*(\w+)\s*\(', full_line)
                    if match2:
                        eid = match2.group(1)
                        etype = match2.group(2)
                        entities[eid] = (etype, full_line)
                current_line = []
    return entities


def _build_reverse_graph(entities: Dict) -> Dict[str, Set[str]]:
    """构建反向引用图: {被引用的id: 引用它的id集合}"""
    reverse = {}
    for eid, (etype, line) in entities.items():
        refs = _extract_refs(line)
        own = eid
        for ref_id in refs:
            if ref_id != own:
                if ref_id not in reverse:
                    reverse[ref_id] = set()
                reverse[ref_id].add(eid)
    return reverse


def _build_pd_to_product_map(entities: Dict, reverse_graph: Dict) -> Dict[str, Tuple[str, str]]:
    """建立 PRODUCT_DEFINITION -> (product_id, product_name) 映射
    
    链路: PD正向引用PDF, PDF正向引用PRODUCT
    #759224=PRODUCT_DEFINITION(...,#759336)  -- PD -> PDF
    #759336=PRODUCT_DEFINITION_FORMATION(...,#759563)  -- PDF -> PRODUCT
    """
    pd_map = {}
    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT_DEFINITION':
            continue
        # PD正向引用PDF
        refs = _extract_refs(line)
        for ref in refs:
            if ref != eid and ref in entities and entities[ref][0] == 'PRODUCT_DEFINITION_FORMATION':
                pdf_id = ref
                # PDF正向引用PRODUCT
                pdf_refs = _extract_refs(entities[pdf_id][1])
                for prod in pdf_refs:
                    if prod != pdf_id and prod in entities and entities[prod][0] == 'PRODUCT':
                        # 获取名称
                        nm2 = re.search(r"'[^']*',\s*'([^']*)'", entities[prod][1])
                        decoded2 = _decode_step_name(nm2.group(1)) if nm2 else ''
                        nm1 = re.search(r"PRODUCT\(\s*'([^']*)'", entities[prod][1])
                        decoded1 = _decode_step_name(nm1.group(1)) if nm1 else ''
                        pd_map[eid] = (prod, decoded2 or decoded1)
                        break
                break
    return pd_map


def extract_model_codes(spec: str, name: str = "") -> List[str]:
    """从BOM物料的spec字段和name中提取型号代码

    型号代码格式：字母+数字组合，如 PLPV-K08, G2FATW08, MMD303RN, CW-UE-W4-S。
    统一用 _extract_codes_from_text 提取，与学习引擎的库型号提取保持一致。
    """
    text = f"{spec} {name}"
    return _extract_codes_from_text(text)


def _extract_codes_from_text(text: str) -> List[str]:
    """从文本中提取型号代码（统一规则，供spec/库型号两端使用）

    要求：型号必须含数字（真型号都有数字，如 G2FATW08, PLPV-K08, MMD303RN），
    纯字母前缀（PLMV)、品牌名（SUPER、Entegris）不被误当作型号。
    """
    codes = []
    up = text.upper()
    token_pats = [
        r'\b[A-Z][A-Z0-9]*[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b',   # 带分隔符：PLPV-K08, CW-UE-W4-S, MRV-15F
        r'\b[A-Z][A-Z0-9]{3,}\b',                                 # 无分隔符连续：MMD303RN, G2FATW08
    ]
    for tpat in token_pats:
        for m in re.findall(tpat, up):
            c = m
            # 必要条件：长度>=4、含数字、非纯hex
            if len(c) < 4:
                continue
            if not re.search(r'\d', c):
                continue
            if re.fullmatch(r'[0-9A-F]{6,}', c):
                continue
            if c not in codes:
                codes.append(c)
    return codes


def find_matching_products(entities: Dict, model_codes: List[str]) -> Set[str]:
    """用型号代码精确匹配STEP文件中的PRODUCT实体
    
    Args:
        entities: STEP实体字典
        model_codes: 型号代码列表（如['PLPV-K08', 'G2FATW08']），已大写
    """
    matched_ids = set()
    if not model_codes:
        return matched_ids
    
    # 先收集所有PRODUCT的名称
    product_names = {}  # {product_id: [name1, name2, ...]}
    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT':
            continue
        names = []
        name_matches = re.findall(r"'([^']*)'", line)
        for nm in name_matches:
            decoded = _decode_step_name(nm)
            names.append(decoded)
        product_names[eid] = names
    
    # 收集PRODUCT_RELATED_PRODUCT_CATEGORY中的名称
    prpc_to_product = {}
    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT_RELATED_PRODUCT_CATEGORY':
            continue
        name_match = re.search(r"PRODUCT_RELATED_PRODUCT_CATEGORY\(\s*'([^']*)'", line)
        if not name_match:
            continue
        decoded = _decode_step_name(name_match.group(1))
        refs = _extract_refs(line)
        for ref in refs:
            if ref != eid and ref in entities and entities[ref][0] == 'PRODUCT':
                prpc_to_product[ref] = decoded
    
    # 精确匹配：型号代码 == PRODUCT名称
    for prod_id, names in product_names.items():
        all_text = ' '.join(names).upper()
        for code in model_codes:
            code_upper = code.upper()
            if code_upper in all_text:
                matched_ids.add(prod_id)
                break
        else:
            # 检查PRPC名称
            prpc_name = prpc_to_product.get(prod_id, '')
            if prpc_name:
                prpc_upper = prpc_name.upper()
                for code in model_codes:
                    if code.upper() in prpc_upper:
                        matched_ids.add(prod_id)
                        break
    
    print(f"  型号代码匹配: {model_codes} -> {len(matched_ids)} PRODUCT(s)")
    for mid in matched_ids:
        names = product_names.get(mid, [])
        print(f"    #{mid}: {names}")
    
    return matched_ids


def _find_nau_instances(entities: Dict, pd_to_product: Dict) -> Dict[str, List[str]]:
    """找到每个PRODUCT_DEFINITION对应的所有NAU实例ID
    
    Returns: {child_pd_id: [nau_id, ...]}
    """
    nau_map = {}
    for eid, (etype, line) in entities.items():
        if etype != 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
            continue
        refs = _extract_refs(line)
        if len(refs) >= 3:
            child_pd = refs[2]
            if child_pd not in nau_map:
                nau_map[child_pd] = []
            nau_map[child_pd].append(eid)
    return nau_map


def collect_geometry_for_instances(
    entities: Dict, 
    reverse_graph: Dict,
    product_ids: Set[str],
    component_counts: Dict[str, int]
) -> Set[str]:
    """根据实际需要的组件数量，收集几何实体
    
    Args:
        entities: STEP实体字典
        reverse_graph: 反向引用图
        product_ids: 匹配到的PRODUCT ID集合
        component_counts: {product_name: needed_count} 每种组件需要的数量
    """
    collected = set()
    
    # 1. 建立PD到PRODUCT的映射
    pd_to_product = _build_pd_to_product_map(entities, reverse_graph)
    
    # 2. 找到匹配PRODUCT对应的所有PD
    matched_pds = {}  # {product_id: [pd_id, ...]}
    for pd_id, (prod_id, prod_name) in pd_to_product.items():
        if prod_id in product_ids:
            if prod_id not in matched_pds:
                matched_pds[prod_id] = []
            matched_pds[prod_id].append(pd_id)
    
    # 3. 找到每个PD对应的NAU实例
    nau_map = _find_nau_instances(entities, pd_to_product)
    
    # 4. 对每个匹配的PRODUCT，只保留需要数量的NAU实例
    selected_naus = set()
    for prod_id, pd_ids in matched_pds.items():
        prod_name = ''
        for pd_id in pd_ids:
            if pd_id in pd_to_product:
                prod_name = pd_to_product[pd_id][1]
                break
        
        # 确定需要的实例数 - 用型号代码精确匹配
        needed = 0
        prod_name_upper = prod_name.upper()
        for code, count in component_counts.items():
            code_upper = code.upper()
            if code_upper and code_upper in prod_name_upper:
                needed = max(needed, count)
                break
        if needed == 0:
            needed = 1  # 至少保留1个
        
        # 按NAU ID排序，只取前needed个
        all_naus = []
        for pd_id in pd_ids:
            all_naus.extend(nau_map.get(pd_id, []))
        all_naus.sort(key=lambda x: int(x))
        
        selected = all_naus[:needed]
        selected_naus.update(selected)
        print(f"  PRODUCT #{prod_id} ({prod_name}): {len(all_naus)} NAUs, keeping {len(selected)}")
    
    # 5. 从每个匹配的PRODUCT出发，追踪完整几何链路
    # 链路方向（关键！）:
    #   PRODUCT <- PDF (反向: PDF正向引用PRODUCT)
    #   PDF <- PD (反向: PD正向引用PDF)  
    #   PD <- PDS (反向: PDS正向引用PD)
    #   PDS <- SDR (反向: SDR正向引用PDS)
    #   SDR -> SR (正向: SDR引用SR)
    #   SR <- SRR/RR (反向: SRR/RR正向引用SR)
    #   SRR/RR -> ABSR (正向: SRR/RR引用ABSR)
    start_ids = set()
    start_ids.update(product_ids)
    start_ids.update(selected_naus)
    
    for pid in product_ids:
        if pid not in entities:
            continue
        # PRODUCT反向找PDF (PDF正向引用PRODUCT)
        for pdf in reverse_graph.get(pid, set()):
            if pdf in entities and entities[pdf][0] == 'PRODUCT_DEFINITION_FORMATION':
                start_ids.add(pdf)
                # PDF反向找PD (PD正向引用PDF)
                for pd_id in reverse_graph.get(pdf, set()):
                    if pd_id in entities and entities[pd_id][0] == 'PRODUCT_DEFINITION':
                        start_ids.add(pd_id)
                        # PD反向找PDS (PDS正向引用PD)
                        for pds in reverse_graph.get(pd_id, set()):
                            if pds in entities and entities[pds][0] == 'PRODUCT_DEFINITION_SHAPE':
                                start_ids.add(pds)
                                # PDS反向找SDR (SDR正向引用PDS)
                                for sdr in reverse_graph.get(pds, set()):
                                    if sdr in entities and entities[sdr][0] == 'SHAPE_DEFINITION_REPRESENTATION':
                                        start_ids.add(sdr)
                                        # SDR正向引用SR
                                        for sr in _extract_refs(entities[sdr][1]):
                                            if sr != sdr and sr in entities and entities[sr][0] == 'SHAPE_REPRESENTATION':
                                                start_ids.add(sr)
                                                # SR反向找SRR/RR
                                                for srr in reverse_graph.get(sr, set()):
                                                    if srr in entities:
                                                        srr_type = entities[srr][0]
                                                        if srr_type in ('SHAPE_REPRESENTATION_RELATIONSHIP', 'REPRESENTATION_RELATIONSHIP'):
                                                            start_ids.add(srr)
                                                            # SRR/RR正向引用可能有ABSR
                                                            start_ids.update(_extract_refs(entities[srr][1]))
    
    # 6. 正向收集所有依赖
    to_process = list(start_ids)
    while to_process:
        eid = to_process.pop(0)
        if eid in collected or eid not in entities:
            continue
        collected.add(eid)
        _, line = entities[eid]
        refs = _extract_refs(line)
        for ref_id in refs:
            if ref_id != eid and ref_id not in collected:
                if ref_id in entities:
                    to_process.append(ref_id)
    
    # 7. 补充缺失引用
    for _ in range(10):
        to_add = set()
        for eid in collected:
            if eid not in entities:
                continue
            line = entities[eid][1]
            refs = _extract_refs(line)
            for ref in refs:
                if ref != eid and ref in entities and ref not in collected:
                    to_add.add(ref)
        if not to_add:
            break
        collected.update(to_add)
    
    return collected


def filter_step_file(stp_path: str, model_codes: List[str], 
                     output_path: str,
                     component_counts: Dict[str, int] = None) -> str:
    """过滤STEP文件，只保留匹配的组件
    
    Args:
        stp_path: 原始STEP文件路径
        model_codes: 型号代码列表（如['PLPV-K08', 'G2FATW08']）
        output_path: 输出STEP文件路径
        component_counts: {型号代码: 数量} 每种组件需要的实例数
    """
    if component_counts is None:
        from collections import Counter
        component_counts = dict(Counter(model_codes))
    
    # 1. 解析STEP文件
    entities = parse_step_file(stp_path)
    
    # 2. 构建反向引用图
    reverse_graph = _build_reverse_graph(entities)
    
    # 3. 用型号代码精确匹配PRODUCT
    matched_product_ids = find_matching_products(entities, model_codes)
    
    # 4. 收集所有几何相关实体（按实例数量）
    if matched_product_ids:
        needed_ids = collect_geometry_for_instances(
            entities, reverse_graph, matched_product_ids, component_counts
        )
    else:
        print(f"  WARNING: 没有找到匹配的PRODUCT，型号代码: {model_codes}")
        needed_ids = set(entities.keys())
    
    # 5. 读取原始文件获取HEADER
    with open(stp_path, 'r', encoding='utf-8', errors='ignore') as f:
        original = f.read()
    
    header_match = re.search(r'(ISO-10303-21;.*?ENDSEC;)', original, re.DOTALL)
    header = header_match.group(1) if header_match else 'ISO-10303-21;\nHEADER;\nENDSEC;'
    
    # 6. 构建DATA段
    data_lines = []
    sorted_ids = sorted(needed_ids, key=lambda x: int(x))
    
    for eid in sorted_ids:
        etype, line = entities[eid]
        
        if etype == 'MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION':
            def filter_refs(m):
                ref_id = m.group(1)
                if ref_id in needed_ids:
                    return m.group(0)
                return ''
            filtered_line = re.sub(r'#(\d+)', filter_refs, line)
            filtered_line = re.sub(r',\s*,', ',', filtered_line)
            filtered_line = re.sub(r'\(\s*,', '(', filtered_line)
            filtered_line = re.sub(r',\s*\)', ')', filtered_line)
            data_lines.append(filtered_line)
        else:
            data_lines.append(line)
    
    # 7. 写入新STEP文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write('\nDATA;\n')
        for line in data_lines:
            f.write(line + '\n')
        f.write('ENDSEC;\nEND-ISO-10303-21;\n')
    
    print(f"  STEP过滤完成: {len(needed_ids)} 实体 -> {os.path.getsize(output_path)} 字节")
    return output_path


def filter_step_files(stp_paths: List[str], model_codes: List[str],
                      output_path: str,
                      component_counts: Dict[str, int] = None) -> str:
    """从多个STEP库文件合并过滤，输出一个通用装配STEP

    每个库独立解析、匹配同一批型号代码，收集匹配零件的几何实体。
    各库实体ID通过全局偏移避免冲突，最终拼接成一个STEP文件。
    这样任何项目都能从所有库里找到可用的零件，实现"通用库"。

    Args:
        stp_paths: 多个STEP库文件路径
        model_codes: 型号代码列表
        output_path: 输出STEP文件路径
        component_counts: {型号代码: 数量}
    """
    if component_counts is None:
        from collections import Counter
        component_counts = dict(Counter(model_codes))

    if not stp_paths:
        raise ValueError("stp_paths为空")

    header = None
    global_offset = 0
    all_data_lines = []
    extra_refs = set()  # 跨库补充引用（在本库匹配集内需优先保留）
    per_lib_entity_sets = []
    # 已展开的型号（避免跨库重复）
    expanded_codes = set()

    for si, stp_path in enumerate(stp_paths):
        if not os.path.exists(stp_path):
            print(f"  WARNING: 库文件不存在，跳过: {stp_path}")
            continue

        entities = parse_step_file(stp_path)
        reverse_graph = _build_reverse_graph(entities)

        # 匹配型号
        matched_product_ids = find_matching_products(entities, model_codes)

        if matched_product_ids:
            needed_ids = collect_geometry_for_instances(
                entities, reverse_graph, matched_product_ids, component_counts
            )
        else:
            print(f"  WARNING: 库 {os.path.basename(stp_path)} 没有匹配的PRODUCT")
            needed_ids = set()

        if not needed_ids:
            continue

        # 记录本库实体集（用于MDGPR引用过滤判断）
        per_lib_entity_sets.append(set(needed_ids))
        # 读取HEADER（取第一个有匹配的库）
        if header is None:
            with open(stp_path, 'r', encoding='utf-8', errors='ignore') as f:
                original = f.read()
            hm = re.search(r'(ISO-10303-21;.*?ENDSEC;)', original, re.DOTALL)
            header = hm.group(1) if hm else 'ISO-10303-21;\nHEADER;\nENDSEC;'

        # 本库的ID偏移：以本库最大实体ID + 1 作为本库内部offset基准
        local_max = max(int(eid) for eid in entities.keys()) if entities else 0
        offset = global_offset
        global_offset += (local_max + 1)

        sorted_ids = sorted(needed_ids, key=lambda x: int(x))
        for eid in sorted_ids:
            etype, line = entities[eid]

            if etype == 'MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION':
                # 只保留需要的引用
                def filter_refs(m):
                    ref_id = m.group(1)
                    if int(ref_id) in needed_ids or (offset and False):
                        return f'#{int(ref_id) + offset}'
                    return ''
                fl = re.sub(r'#(\d+)', filter_refs, line)
                fl = re.sub(r',\s*,', ',', fl)
                fl = re.sub(r'\(\s*,', '(', fl)
                fl = re.sub(r',\s*\)', ')', fl)
                if fl.strip():
                    all_data_lines.append(fl)
            else:
                # 把实体定义行开头 #id= 重写，并重写行内引用
                rewritten = _rewrite_offset(line, offset)
                all_data_lines.append(rewritten)

        # ---- 实例展开：让STP中每个型号的NAU实例数与物料数量一一对应 ----
        # 用本库匹配到的型号生成 N 个NAU（命名 型号:1..N，引用本库几何，引用ID加offset），
        # 让 8A(81个PLMV-K16) 与 8A测试(80个) 的STP体现真实数量差异。
        for code, need in sorted(component_counts.items(), key=lambda x: x[0]):
            if code in expanded_codes:
                continue
            nau_refs = _find_nau_for_code(entities, reverse_graph, code)
            if not nau_refs:
                continue
            root_pd, child_pd = nau_refs
            need = max(1, int(need))
            ref_root = f'#{int(root_pd) + offset}'
            ref_child = f'#{int(child_pd) + offset}'
            for i in range(1, need + 1):
                inst_name = f"{code}:{i}"
                line = (f"#{global_offset + i}=NEXT_ASSEMBLY_USAGE_OCCURRENCE("
                        f"'{_esc(inst_name)}','{_esc(inst_name)}','{_esc(inst_name)}',"
                        f"{ref_root},{ref_child},'{_esc(inst_name)}');")
                all_data_lines.append(line)
                extra_refs.add(inst_name)
            expanded_codes.add(code)
            print(f"  [库{si}] 实例展开 {code}: {need} 个NAU")
            global_offset += (need + 1)

        print(f"  [库{si}] {os.path.basename(stp_path)}: 偏移+{offset}, 收集 {len(needed_ids)} 实体")

    if header is None:
        header = 'ISO-10303-21;\nHEADER;\nENDSEC;'

    # 写合并STP
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write('\nDATA;\n')
        for line in all_data_lines:
            f.write(line + '\n')
        f.write('ENDSEC;\nEND-ISO-10303-21;\n')

    print(f"  多库合并过滤完成: {len(all_data_lines)} 实体 -> {os.path.getsize(output_path)} 字节")
    return output_path


def _rewrite_offset(line: str, offset: int) -> str:
    """重写STEP实体行：所有 #数字（含定义ID和引用）统一加偏移。
    一次 re.sub 同时处理定义行首和行内引用，不会重复加——
    re.sub 对每个匹配只替换一次。
    """
    if offset == 0:
        return line
    return re.sub(r'#(\d+)', lambda m: f'#{int(m.group(1)) + offset}', line)


def _esc(s: str) -> str:
    """STEP字符串转义"""
    return s.replace("'", "''")


def _find_nau_for_code(entities: Dict, reverse_graph: Dict, code: str):
    """找到某型号代码在库中NAU的 (root_pd, child_pd) 引用ID

    通过匹配 PRODUCT 名称里的型号代码 -> 反查 PD -> 找引用该PD的NAU，
    取其 parent为root_pd、child为child_pd。返回 (root_pd, child_pd)；找不到返回None。
    """
    pd_to_product = _build_pd_to_product_map(entities, reverse_graph)
    code_up = code.upper()

    # 该型号对应的 PD
    target_pd = None
    for pd_id, (prod_id, prod_name) in pd_to_product.items():
        if code_up in (prod_name or '').upper():
            target_pd = pd_id
            break
    if not target_pd:
        return None

    # 找引用该PD作为child的NAU，取其 (root_pd, child_pd)
    for eid, (et, line) in entities.items():
        if et != 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
            continue
        refs = _extract_refs(line)
        if target_pd in refs:
            root_pd = refs[1] if len(refs) > 1 else target_pd
            child_pd = refs[2] if len(refs) > 2 else target_pd
            return (root_pd, child_pd)
    return None
