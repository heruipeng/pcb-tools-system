# Gerber 文件编辑器 — 独立工具

纯 Python 操作 Gerber 文件 (RS-274X)，不依赖任何 CAM 软件。

## 快速开始

```bash
# 分析 Gerber 文件结构
python3 gerber_editor.py your_board.gbr

# 或
python3 gerber_editor.py your_board.gbr --analyze
```

## 支持的操作

### 线宽修改
```bash
python3 gerber_editor.py board.gbr --modify '{"line_width":{"from":0.1,"to":0.12}}'
```

### 焊盘尺寸修改
```bash
python3 gerber_editor.py board.gbr --modify '{"pad":{"from":[0.5,0.3],"to":[0.6,0.35]}}'
```

### 阻焊/孔径缩放
```bash
python3 gerber_editor.py sm_top.gbr --modify '{"scale":1.05}'
```

### 坐标偏移
```bash
python3 gerber_editor.py board.gbr --modify '{"offset":[100,0]}'
```

### X/Y 镜像
```bash
python3 gerber_editor.py board.gbr --modify '{"mirror_x":true}'
```

### 组合修改
```bash
python3 gerber_editor.py board.gbr --modify '{"line_width":{"from":0.1,"to":0.12},"offset":[10,5]}'
```

## 生成 Genesis 脚本（可选）

如果你有 Genesis/InCAMPro 环境，可以用脚本生成器：
```bash
python3 genesis_scripts.py line_width "PCB-A8X-001" '{"layer":"top","original_width":0.1,"target_width":0.12}'
```
