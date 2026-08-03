"""
STEP文件过滤器
============================
解析原始STEP装配体文件，根据实际匹配的组件名称，
提取对应零件的所有实体引用，生成只包含所需零件的新STEP文件。

STEP引用链路:
  PRODUCT -> PRODUCT_DEFINITION_FORMATION -> PRODUCT_DEFINITION
  PRODUCT_DEFINITION -> PRODUCT_DEFINITION_SHAPE -> SHAPE_DEFINITION_REPRESENTATION
  SHAPE_DEFINITION_REPRESENTATION -> SHAPE_REPRESENTATION
  SHAPE_REPRESENTATION -> SHAPE_REPRESENTATION_RELATIONSHIP -> ADVANCED_BREP_SHAPE_REPRESENTATION
  ADVANCED_BREP_SHAPE_REPRESENTATION -> MANIFOLD_SOLID_BREP -> CLOSED_SHELL -> ADVANCED_FACE -> ...
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
                    # 复合格式: #id = ( TYPE(...) ... )
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


def find_matching_products(entities: Dict, component_names: List[str]) -> Set[str]:
    """找到名称匹配的PRODUCT实体ID集合
    
    检查PRODUCT的两个名称字段和PRODUCT_RELATED_PRODUCT_CATEGORY中的名称
    """
    matched_ids = set()
    
    # 收集所有匹配关键词
    keywords = []
    for name in component_names:
        if not name:
            continue
        keywords.append(name.lower())
        # 也拆分关键词
        for part in re.split(r'[\s\-_/()]', name):
            if len(part) >= 2:
                keywords.append(part.lower())
    
    # 1. 检查PRODUCT实体（两个名称字段）
    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT':
            continue
        
        # 提取第一个名称
        names = []
        name_matches = re.findall(r"'([^']*)'", line)
        for nm in name_matches:
            decoded = _decode_step_name(nm)
            names.append(decoded)
        
        # 检查是否匹配
        all_names = ' '.join(names).lower()
        for kw in keywords:
            if kw in all_names:
                matched_ids.add(eid)
                break
    
    # 2. 检查PRODUCT_RELATED_PRODUCT_CATEGORY (可能包含英文名)
    prpc_to_product = {}
    for eid, (etype, line) in entities.items():
        if etype != 'PRODUCT_RELATED_PRODUCT_CATEGORY':
            continue
        name_match = re.search(r"PRODUCT_RELATED_PRODUCT_CATEGORY\(\s*'([^']*)'", line)
        if not name_match:
            continue
        decoded = _decode_step_name(name_match.group(1))
        
        # 找到这个PRPC引用的PRODUCT
        refs = _extract_refs(line)
        for ref in refs:
            if ref != eid and ref in entities and entities[ref][0] == 'PRODUCT':
                prpc_to_product[ref] = decoded
    
    # 用PRPC的名称匹配
    for product_id, prpc_name in prpc_to_product.items():
        if product_id in matched_ids:
            continue
        for kw in keywords:
            if kw in prpc_name.lower():
                matched_ids.add(product_id)
                break
    
    return matched_ids


def collect_geometry_entities(entities: Dict, reverse_graph: Dict, 
                               product_ids: Set[str]) -> Set[str]:
    """从PRODUCT实体出发，沿着完整引用链收集所有几何相关实体
    
    链路:
      PRODUCT -> PRODUCT_DEFINITION_FORMATION (正向)
      PRODUCT_DEFINITION_FORMATION -> PRODUCT_DEFINITION (反向)
      PRODUCT_DEFINITION -> PRODUCT_DEFINITION_SHAPE (反向)
      PRODUCT_DEFINITION_SHAPE -> SHAPE_DEFINITION_REPRESENTATION (反向)
      SHAPE_DEFINITION_REPRESENTATION -> SHAPE_REPRESENTATION (正向)
      SHAPE_REPRESENTATION -> SHAPE_REPRESENTATION_RELATIONSHIP (反向)
      SHAPE_REPRESENTATION_RELATIONSHIP -> ADVANCED_BREP_SHAPE_REPRESENTATION (正向)
      ADVANCED_BREP_SHAPE_REPRESENTATION -> MANIFOLD_SOLID_BREP (正向)
      MANIFOLD_SOLID_BREP -> CLOSED_SHELL (正向)
      -> 从所有实体正向收集所有依赖
    """
    collected = set()
    
    # Step 1: 从PRODUCT找到PRODUCT_DEFINITION_FORMATION (反向)
    pdf_ids = set()
    for pid in product_ids:
        referrers = reverse_graph.get(pid, set())
        for ref in referrers:
            if ref in entities and entities[ref][0] == 'PRODUCT_DEFINITION_FORMATION':
                pdf_ids.add(ref)
    
    # Step 2: PRODUCT_DEFINITION_FORMATION -> PRODUCT_DEFINITION (反向)
    pd_ids = set()
    for pdf in pdf_ids:
        referrers = reverse_graph.get(pdf, set())
        for ref in referrers:
            if ref in entities and entities[ref][0] == 'PRODUCT_DEFINITION':
                pd_ids.add(ref)
    
    # Step 3: PRODUCT_DEFINITION -> PRODUCT_DEFINITION_SHAPE (反向)
    pds_ids = set()
    for pd in pd_ids:
        referrers = reverse_graph.get(pd, set())
        for ref in referrers:
            if ref in entities and entities[ref][0] == 'PRODUCT_DEFINITION_SHAPE':
                pds_ids.add(ref)
    
    # Step 4: PRODUCT_DEFINITION_SHAPE -> SHAPE_DEFINITION_REPRESENTATION (反向)
    sdr_ids = set()
    for pds in pds_ids:
        referrers = reverse_graph.get(pds, set())
        for ref in referrers:
            if ref in entities and entities[ref][0] == 'SHAPE_DEFINITION_REPRESENTATION':
                sdr_ids.add(ref)
    
    # Step 5: SHAPE_DEFINITION_REPRESENTATION -> SHAPE_REPRESENTATION (正向)
    sr_ids = set()
    for sdr in sdr_ids:
        refs = _extract_refs(entities[sdr][1])
        for ref in refs:
            if ref != sdr and ref in entities:
                sr_ids.add(ref)
    
    # Step 6: SHAPE_REPRESENTATION -> SHAPE_REPRESENTATION_RELATIONSHIP (反向)
    srr_ids = set()
    for sr in sr_ids:
        referrers = reverse_graph.get(sr, set())
        for ref in referrers:
            if ref in entities and entities[ref][0] == 'SHAPE_REPRESENTATION_RELATIONSHIP':
                srr_ids.add(ref)
    
    # Step 7: SRR -> ADVANCED_BREP_SHAPE_REPRESENTATION (正向)
    absr_ids = set()
    for srr in srr_ids:
        refs = _extract_refs(entities[srr][1])
        for ref in refs:
            if ref != srr and ref in entities:
                absr_ids.add(ref)
    
    # Step 8: 收集所有起始点
    start_ids = set()
    start_ids.update(product_ids)  # PRODUCT
    start_ids.update(pdf_ids)      # PRODUCT_DEFINITION_FORMATION
    start_ids.update(pd_ids)       # PRODUCT_DEFINITION
    start_ids.update(pds_ids)      # PRODUCT_DEFINITION_SHAPE
    start_ids.update(sdr_ids)      # SHAPE_DEFINITION_REPRESENTATION
    start_ids.update(sr_ids)       # SHAPE_REPRESENTATION
    start_ids.update(srr_ids)      # SHAPE_REPRESENTATION_RELATIONSHIP
    start_ids.update(absr_ids)     # ADVANCED_BREP_SHAPE_REPRESENTATION
    
    # Step 9: 从所有起始点正向收集依赖
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
    
    # Step 10: 收集装配关系 (NEXT_ASSEMBLY_USAGE_OCCURRENCE)
    for eid, (etype, line) in entities.items():
        if etype == 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
            refs = _extract_refs(line)
            for ref in refs:
                if ref in collected:
                    collected.add(eid)
                    break
    
    # Step 11: 收集MDGPR中引用的STYLED_ITEM (如果其item在collected中)
    for eid, (etype, line) in entities.items():
        if etype == 'STYLED_ITEM':
            refs = _extract_refs(line)
            for ref in refs:
                if ref != eid and ref in collected:
                    collected.add(eid)
                    break
    
    # Step 12: 补充缺失的引用 (被collected中实体引用但未被收集的实体)
    # 多轮补充直到稳定
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


def filter_step_file(stp_path: str, component_names: List[str], 
                     output_path: str) -> str:
    """过滤STEP文件，只保留匹配的组件"""
    # 1. 解析STEP文件
    entities = parse_step_file(stp_path)
    
    # 2. 构建反向引用图
    reverse_graph = _build_reverse_graph(entities)
    
    # 3. 找到匹配的PRODUCT实体
    matched_product_ids = find_matching_products(entities, component_names)
    
    # 4. 收集所有几何相关实体
    if matched_product_ids:
        needed_ids = collect_geometry_entities(entities, reverse_graph, matched_product_ids)
    else:
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
        
        # 对MDGPR做特殊处理：过滤掉不在needed_ids中的引用
        if etype == 'MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION':
            def filter_refs(m):
                ref_id = m.group(1)
                if ref_id in needed_ids:
                    return m.group(0)
                return ''
            filtered_line = re.sub(r'#(\d+)', filter_refs, line)
            # 清理空引用
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
    
    return output_path
