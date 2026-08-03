# 2D->3D 转换程序

## 概述
将二维PID图 + 点料表自动转换为三维模型。

### 工作流程
- **学习阶段**：输入 2D(DXF) + 点料表(xls) + 三维参考文件(ipt/iam/stp)，系统自动提取映射规则
- **使用阶段**：输入 2D(DXF) + 点料表(xls)，利用已学知识自动生成三维模型(GLB)

## 文件结构
```
23d_converter/
├── app.py                      # Flask后端入口
├── templates/index.html        # Web前端界面
├── modules/
│   ├── __init__.py
│   ├── dxf_parser.py           # DXF二维PID图解析器
│   ├── bom_parser.py           # 点料表(xls)解析器
│   ├── matcher.py              # 组件匹配引擎
│   ├── learning_engine.py      # 学习引擎(提取/应用知识)
│   └── model_generator.py      # 三维模型生成器
├── data/
│   ├── knowledge/              # 已学知识JSON
│   ├── cases/                  # 历史案例
│   ├── library/                # 零件库
│   └── output/                 # 生成结果
```

## 启动方式
```bash
cd 23d_converter
python app.py
# 访问 http://localhost:5173
```

## 使用方式

### 1. 学习案例（首次使用）
1. 打开Web界面 -> 点击「学习案例」
2. 填写案例名称（如：海神VMB-700）
3. 上传：DXF文件 + 点料表xls + 三维参考文件(ipt/iam/stp)
4. 点击「开始学习」

### 2. 生成3D（日常使用）
1. 点击「生成3D」
2. 上传：DXF文件 + 点料表xls
3. 点击「生成三维模型」
4. 查看三维预览，下载GLB模型

## 技术栈
- Python 3.13 + Flask
- ezdxf (DXF解析)
- xlrd (xls解析)
- trimesh + numpy (三维几何体生成)
- Three.js (前端三维预览)
