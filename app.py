"""
Flask后端API
==================
提供以下接口：
  - POST /api/learn        学习阶段：上传2D+点料表+3D参考目录
  - POST /api/generate     使用阶段：上传2D+点料表，生成3D
  - GET  /api/knowledge     查看已学知识
  - GET  /api/cases         查看历史案例
  - GET  /api/assembly/<id> 获取装配结果
  - GET  /api/download/<id> 下载STEP/GLB文件
"""
import os
import json
import uuid
import tempfile
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from pathlib import Path

from modules.dxf_parser import parse_dxf
from modules.bom_parser import parse_bom
from modules.matcher import match_components
from modules.learning_engine import learn_from_case, load_knowledge, apply_knowledge
from modules.model_generator import generate_assembly, export_assembly_scene

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# 数据存储目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CASES_DIR = os.path.join(DATA_DIR, "cases")
LIBRARY_DIR = os.path.join(DATA_DIR, "library")
KNOWLEDGE_DIR = os.path.join(DATA_DIR, "knowledge")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

for d in [CASES_DIR, LIBRARY_DIR, KNOWLEDGE_DIR, OUTPUT_DIR, UPLOAD_DIR]:
    os.makedirs(d, exist_ok=True)

# 内存中的装配结果缓存
assembly_cache = {}


def _safe_save(file_storage, dest_path):
    """安全保存上传文件：用唯一文件名避免冲突和锁问题"""
    # 不删除旧文件，直接保存到带时间戳的文件名
    base, ext = os.path.splitext(dest_path)
    import time
    unique_path = f"{base}_{int(time.time() * 1000)}{ext}"
    file_storage.save(unique_path)
    # 保存成功后，用新文件替换旧文件
    if os.path.exists(dest_path):
        try:
            os.replace(unique_path, dest_path)
        except PermissionError:
            # 如果旧文件被锁，直接用新文件路径
            return unique_path
    else:
        try:
            os.rename(unique_path, dest_path)
        except Exception:
            return unique_path
    return dest_path


@app.route("/")
def index():
    return render_template("index.html")


# ============= 学习阶段 =============

@app.route("/api/learn", methods=["POST"])
def api_learn():
    """学习阶段：输入2D+点料表+3D参考，提取知识"""
    try:
        # 获取参数
        case_name = request.form.get("case_name", f"case_{uuid.uuid4().hex[:8]}")
        dxf_file = request.files.get("dxf_file")
        bom_file = request.files.get("bom_file")
        ref_3d_dir = request.form.get("ref_3d_dir", "")

        if not dxf_file or not bom_file:
            return jsonify({"error": "请上传DXF和点料表文件"}), 400

        # 保存上传文件（先删除旧文件避免Permission denied）
        case_upload_dir = os.path.join(UPLOAD_DIR, case_name)
        os.makedirs(case_upload_dir, exist_ok=True)

        dxf_path = os.path.join(case_upload_dir, dxf_file.filename)
        _safe_save(dxf_file, dxf_path)

        bom_path = os.path.join(case_upload_dir, bom_file.filename)
        _safe_save(bom_file, bom_path)

        # 如果上传了3D参考文件（案例的三维装配体）
        ref_files = request.files.getlist("ref_3d_files")
        if ref_files and ref_files[0].filename:
            ref_dir = os.path.join(case_upload_dir, "ref_3d")
            os.makedirs(ref_dir, exist_ok=True)
            for f in ref_files:
                fname = f.filename
                fpath = os.path.join(ref_dir, fname)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                _safe_save(f, fpath)
            ref_3d_dir = ref_dir

        if not ref_3d_dir or not os.path.exists(ref_3d_dir):
            ref_3d_dir = os.path.join(case_upload_dir, "ref_3d")

        # 如果上传了通用器件库文件，保存到 LIBRARY_DIR
        lib_files = request.files.getlist("library_files")
        if lib_files and lib_files[0].filename:
            os.makedirs(LIBRARY_DIR, exist_ok=True)
            lib_saved = []
            for f in lib_files:
                fname = f.filename
                fpath = os.path.join(LIBRARY_DIR, fname)
                _safe_save(f, fpath)
                lib_saved.append(fname)
            # 同时把器件库路径告诉学习引擎
            ref_3d_dir = ref_3d_dir if os.path.exists(ref_3d_dir) else LIBRARY_DIR

        # 执行学习
        knowledge = learn_from_case(
            case_name=case_name,
            dxf_path=dxf_path,
            bom_path=bom_path,
            ref_3d_dir=ref_3d_dir,
            output_dir=KNOWLEDGE_DIR,
        )

        # 如果有器件库文件，把库索引也保存到知识文件中
        if lib_files and lib_files[0].filename:
            # 扫描器件库
            from modules.learning_engine import _scan_3d_library
            lib_index = _scan_3d_library(LIBRARY_DIR)
            # 更新知识文件，加入器件库信息
            knowledge_file = os.path.join(KNOWLEDGE_DIR, f"{case_name}_knowledge.json")
            if os.path.exists(knowledge_file):
                with open(knowledge_file, 'r', encoding='utf-8') as f:
                    kdata = json.load(f)
                kdata["library_index"] = lib_index
                with open(knowledge_file, 'w', encoding='utf-8') as f:
                    json.dump(kdata, f, ensure_ascii=False, indent=2)

        return jsonify({
            "status": "success",
            "case_name": case_name,
            "knowledge": knowledge.to_dict(),
            "message": f"学习完成！提取了 {len(knowledge.part_mappings)} 条映射规则、"
                      f"{len(knowledge.topology_rules)} 条拓扑规则、"
                      f"{len(knowledge.assembly_rules)} 条装配规则、"
                      f"{len(knowledge.hidden_param_rules)} 条隐形参数规则",
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============= 生成阶段 =============

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """使用阶段：输入2D+点料表，利用已学知识生成3D"""
    try:
        dxf_file = request.files.get("dxf_file")
        bom_file = request.files.get("bom_file")
        project_name = request.form.get("project_name", f"project_{uuid.uuid4().hex[:8]}")

        if not dxf_file or not bom_file:
            return jsonify({"error": "请上传DXF和点料表文件"}), 400

        # 保存上传文件
        proj_dir = os.path.join(UPLOAD_DIR, project_name)
        os.makedirs(proj_dir, exist_ok=True)

        dxf_path = os.path.join(proj_dir, dxf_file.filename)
        _safe_save(dxf_file, dxf_path)

        bom_path = os.path.join(proj_dir, bom_file.filename)
        _safe_save(bom_file, bom_path)

        # 加载已学知识
        knowledge_list = load_knowledge(KNOWLEDGE_DIR)

        # 应用知识进行匹配
        match_result = apply_knowledge(dxf_path, bom_path, knowledge_list)

        output_dir = os.path.join(OUTPUT_DIR, project_name)

        # 查找STEP零件库路径
        step_lib_path = ""
        # 1. 先查通用器件库目录 data/library
        if os.path.exists(LIBRARY_DIR):
            for f in os.listdir(LIBRARY_DIR):
                if f.lower().endswith(('.stp', '.step')):
                    step_lib_path = os.path.join(LIBRARY_DIR, f)
                    break
        # 2. 没有通用器件库就从知识文件中查找案例的STEP路径
        if not step_lib_path:
            for k in knowledge_list:
                # 知识文件在 KNOWLEDGE_DIR 目录
                case_file = os.path.join(KNOWLEDGE_DIR, f"{k.get('case_name', '')}_case.json")
                if os.path.exists(case_file):
                    try:
                        with open(case_file, 'r', encoding='utf-8') as f:
                            case_data = json.load(f)
                        lib = case_data.get("library_index", {})
                        for sf in lib.get("step_files", []):
                            fp = sf.get("full_path", "")
                            if fp and os.path.exists(fp):
                                step_lib_path = fp
                                break
                        if step_lib_path:
                            break
                    except Exception:
                        pass

        assembly_result = generate_assembly(match_result, output_dir, step_library_path=step_lib_path)

        # 缓存结果
        assembly_id = uuid.uuid4().hex
        assembly_cache[assembly_id] = {
            "project_name": project_name,
            "match_result": match_result.to_dict(),
            "assembly_result": assembly_result.to_dict(),
            "output_dir": output_dir,
        }

        # 保存匹配结果JSON
        match_json_path = os.path.join(output_dir, "match_result.json")
        with open(match_json_path, 'w', encoding='utf-8') as f:
            json.dump(match_result.to_dict(), f, ensure_ascii=False, indent=2)

        return jsonify({
            "status": "success",
            "assembly_id": assembly_id,
            "project_name": project_name,
            "match_summary": match_result.summary,
            "assembly_stats": assembly_result.stats,
            "knowledge_used": len(knowledge_list),
            "download_urls": {
                "glb": f"/api/download/{assembly_id}/assembly.glb",
                "match_json": f"/api/download/{assembly_id}/match_result.json",
            },
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============= 查看知识/案例 =============

@app.route("/api/knowledge", methods=["GET"])
def api_knowledge():
    """查看已学知识"""
    knowledge_list = load_knowledge(KNOWLEDGE_DIR)
    return jsonify({
        "count": len(knowledge_list),
        "knowledge": knowledge_list,
    })


@app.route("/api/cases", methods=["GET"])
def api_cases():
    """查看历史案例"""
    cases = []
    if os.path.exists(CASES_DIR):
        for fname in os.listdir(CASES_DIR):
            if fname.endswith("_case.json"):
                fpath = os.path.join(CASES_DIR, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        case = json.load(f)
                        cases.append({
                            "name": case.get("name", fname),
                            "pid_summary": case.get("pid", {}).get("component_count", 0),
                            "bom_summary": case.get("bom", {}).get("total_materials", 0),
                        })
                except Exception:
                    pass
    return jsonify({"count": len(cases), "cases": cases})


@app.route("/api/assembly/<assembly_id>", methods=["GET"])
def api_get_assembly(assembly_id):
    """获取装配结果"""
    if assembly_id not in assembly_cache:
        return jsonify({"error": "未找到装配结果"}), 404

    data = assembly_cache[assembly_id]
    return jsonify({
        "project_name": data["project_name"],
        "match_result": data["match_result"],
        "assembly_result": data["assembly_result"],
    })


# ============= 下载 =============

@app.route("/api/download/<assembly_id>/<filename>", methods=["GET"])
def api_download(assembly_id, filename):
    """下载生成的文件"""
    if assembly_id not in assembly_cache:
        return jsonify({"error": "未找到装配结果"}), 404

    output_dir = assembly_cache[assembly_id]["output_dir"]
    file_path = os.path.join(output_dir, filename)

    if not os.path.exists(file_path):
        return jsonify({"error": f"文件不存在: {filename}"}), 404

    return send_file(file_path, as_attachment=True, download_name=filename)


# ============= 直接解析预览 =============

@app.route("/api/parse/preview", methods=["POST"])
def api_parse_preview():
    """仅解析不生成3D，用于预览匹配结果"""
    try:
        dxf_file = request.files.get("dxf_file")
        bom_file = request.files.get("bom_file")

        if not dxf_file or not bom_file:
            return jsonify({"error": "请上传DXF和点料表文件"}), 400

        tmpdir = tempfile.mkdtemp()
        dxf_path = os.path.join(tmpdir, dxf_file.filename)
        _safe_save(dxf_file, dxf_path)
        bom_path = os.path.join(tmpdir, bom_file.filename)
        _safe_save(bom_file, bom_path)

        # 解析
        pid = parse_dxf(dxf_path)
        bom = parse_bom(bom_path)
        match_result = match_components(pid, bom)

        return jsonify({
            "pid": pid.to_dict(),
            "bom": bom.to_dict(),
            "match_result": match_result.to_dict(),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("  2D->3D 转换程序")
    print("  学习阶段：上传 2D(DXF) + 点料表(xls) + 3D参考文件")
    print("  使用阶段：上传 2D(DXF) + 点料表(xls) -> 生成 3D")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5173, debug=True, use_reloader=False)
