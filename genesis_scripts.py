"""
Genesis / InCAMPro 自动化脚本生成器

支持的修改类型及对应的 Genesis 脚本输出。
"""

from typing import Dict, Any, List
from dataclasses import dataclass, field


# ─── 修改类型定义 ───
MODIFY_TYPES = {
    'line_width': {
        'label': '线宽修改',
        'description': '批量修改指定层的线宽',
        'params': ['layer', 'original_width', 'target_width', 'tolerance'],
        'param_labels': {
            'layer': '目标层别',
            'original_width': '原始线宽(mm)',
            'target_width': '目标线宽(mm)',
            'tolerance': '容差(mm)',
        },
    },
    'line_spacing': {
        'label': '线距修改',
        'description': '调整导线之间的最小间距',
        'params': ['layer', 'min_spacing', 'apply_to'],
        'param_labels': {
            'layer': '目标层别',
            'min_spacing': '最小线距(mm)',
            'apply_to': '适用范围(all/outer/inner)',
        },
    },
    'pad_edit': {
        'label': '焊盘修改',
        'description': '批量替换/编辑焊盘尺寸和形状',
        'params': ['layer', 'original_pad', 'target_pad', 'shape'],
        'param_labels': {
            'layer': '目标层别',
            'original_pad': '原焊盘规格(mm)',
            'target_pad': '目标焊盘规格(mm)',
            'shape': '焊盘形状(round/rect/oval)',
        },
    },
    'drill_resize': {
        'label': '钻孔补偿',
        'description': '调整钻孔大小（电镀补偿/压接补偿）',
        'params': ['drill_layer', 'offset', 'drill_type'],
        'param_labels': {
            'drill_layer': '钻孔层别',
            'offset': '补偿值(mm，正=加大，负=减小)',
            'drill_type': '孔类型(plated/npth/all)',
        },
    },
    'soldermask': {
        'label': '阻焊开窗',
        'description': '调整阻焊开窗大小',
        'params': ['layer', 'pad_expansion', 'trace_expansion'],
        'param_labels': {
            'layer': '阻焊层别',
            'pad_expansion': '焊盘外扩(mm)',
            'trace_expansion': '线路外扩(mm)',
        },
    },
    'silk_adjust': {
        'label': '丝印调整',
        'description': '调整丝印位置和大小',
        'params': ['layer', 'offset_x', 'offset_y', 'scale'],
        'param_labels': {
            'layer': '丝印层别',
            'offset_x': 'X偏移(mm)',
            'offset_y': 'Y偏移(mm)',
            'scale': '缩放比例',
        },
    },
    'copper_pour': {
        'label': '铜皮修改',
        'description': '修改铺铜间距/连接方式',
        'params': ['layer', 'clearance', 'thermal_width', 'min_width'],
        'param_labels': {
            'layer': '目标层别',
            'clearance': '铜皮间距(mm)',
            'thermal_width': '散热连接宽度(mm)',
            'min_width': '最小铜皮宽度(mm)',
        },
    },
    'impedance_adj': {
        'label': '阻抗调整',
        'description': '调整差分线/单端线阻抗参数',
        'params': ['layer', 'target_impedance', 'diff_pair', 'tolerance_percent'],
        'param_labels': {
            'layer': '目标层别',
            'target_impedance': '目标阻抗(Ω)',
            'diff_pair': '是否差分对(true/false)',
            'tolerance_percent': '容差(%)',
        },
    },
    'panelize': {
        'label': '自动拼版',
        'description': '按指定参数自动拼版',
        'params': ['x_count', 'y_count', 'border', 'fiducial', 'tooling_holes'],
        'param_labels': {
            'x_count': 'X方向数量',
            'y_count': 'Y方向数量',
            'border': '工艺边(mm)',
            'fiducial': '光学点(true/false)',
            'tooling_holes': '定位孔(true/false)',
        },
    },
}


def generate_genesis_script(modify_type: str, material_no: str, params: Dict[str, Any],
                            job_name: str = None) -> str:
    """
    根据修改类型和参数生成 Genesis/InCAMPro 自动化脚本

    Args:
        modify_type: 修改类型（见 MODIFY_TYPES 的 key）
        material_no: 料号
        params: 修改参数
        job_name: Genesis 作业名（可选）

    Returns:
        完整的 Genesis C-Shell 脚本内容
    """
    meta = MODIFY_TYPES.get(modify_type)
    if not meta:
        raise ValueError(f"不支持修改类型: {modify_type}，可选: {list(MODIFY_TYPES.keys())}")

    job = job_name or f"{material_no}_{modify_type}"
    layer = params.get('layer', 'top')

    header = _genesis_header(job, material_no, meta['label'], params)

    # 根据类型生成不同的脚本内容
    script_body = {
        'line_width': _script_line_width,
        'line_spacing': _script_line_spacing,
        'pad_edit': _script_pad_edit,
        'drill_resize': _script_drill_resize,
        'soldermask': _script_soldermask,
        'silk_adjust': _script_silk_adjust,
        'copper_pour': _script_copper_pour,
        'impedance_adj': _script_impedance,
        'panelize': _script_panelize,
    }

    body_func = script_body.get(modify_type, _script_line_width)
    body = body_func(job, material_no, params)

    footer = _genesis_footer(job)

    return header + body + footer


# ─── Genesis 脚本头部 ───
def _genesis_header(job_name: str, material_no: str, modify_label: str,
                    params: Dict[str, Any]) -> str:
    params_str = str(params)
    now_str = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"""#!/bin/csh -f
# ============================================================
# Genesis Automation Script
# 修改类型: {modify_label}
# 料号:     {material_no}
# 作业名:   {job_name}
# 生成时间: {now_str}
# 参数:     {params_str}
# ============================================================

set JOB = "{job_name}"
set MAT = "{material_no}"

# ── 环境变量设置 ──
setenv GENESIS_EDIR /genesis/e${{GENESIS_VERSION}}
setenv GENESIS_ROOT /genesis
source $GENESIS_ROOT/genesis.set

# ── 连接 Genesis 数据库 ──
genesis_open -job $JOB -matrix $MAT
if ($status != 0) then
    echo "ERROR: 无法打开作业 $JOB"
    exit 1
endif

"""


# ─── 各类型脚本实现 ───

def _script_line_width(job: str, mat: str, params: dict) -> str:
    """线宽修改脚本"""
    orig = params.get('original_width', 0.1)
    target = params.get('target_width', 0.12)
    tolerance = params.get('tolerance', 0.01)
    layer = params.get('layer', 'top')

    return f"""
# ── 线宽修改: {orig}mm → {target}mm, 容差 ±{tolerance}mm ──

# 1) 选择目标层的线路
genesis_select -layer {layer} -type signal

# 2) 按线宽筛选
DFM resize \\
    -in_layer {layer} \\
    -select_width {orig}mm \\
    -tolerance {tolerance}mm \\
    -new_width {target}mm \\
    -mode round

# 3) 验证修改结果
genesis_measure -layer {layer} -min_width
genesis_measure -layer {layer} -max_width

echo "✅ 线宽修改完成: {orig}mm → {target}mm"

"""


def _script_line_spacing(job: str, mat: str, params: dict) -> str:
    """线距修改脚本"""
    min_spacing = params.get('min_spacing', 0.1)
    layer = params.get('layer', 'top')
    apply_to = params.get('apply_to', 'all')

    return f"""
# ── 线距修改: 最小间距 {min_spacing}mm ──

# 1) 设置信号规则
SR setrule \\
    -layer {layer} \\
    -spacing {min_spacing}mm \\
    -apply {apply_to}

# 2) 执行 DRC 检查线距
DRC check \\
    -layers {layer} \\
    -rule spacing \\
    -min {min_spacing}mm \\
    -output reports/{mat}_spacing_report.txt

# 3) 自动修复线距违规
DFM spacing \\
    -layer {layer} \\
    -min {min_spacing}mm \\
    -mode trace_push \\
    -max_iter 10

echo "✅ 线距修改完成: min={min_spacing}mm"

"""


def _script_pad_edit(job: str, mat: str, params: dict) -> str:
    """焊盘修改脚本"""
    orig_pad = params.get('original_pad', '0.5x0.3')
    target_pad = params.get('target_pad', '0.6x0.35')
    shape = params.get('shape', 'rect')
    layer = params.get('layer', 'top')

    return f"""
# ── 焊盘修改: {orig_pad} → {target_pad} ({shape}) ──

# 1) 选择目标焊盘
pad_select \\
    -layer {layer} \\
    -size {orig_pad} \\
    -shape all

# 2) 批量编辑
pad_edit \\
    -layer {layer} \\
    -new_size {target_pad} \\
    -new_shape {shape} \\
    -mode replace_all

# 3) 更新阻焊开窗（如果存在）
soldermask_update \\
    -from_layer {layer} \\
    -pad_expansion 0.05mm \\
    -mode auto

echo "✅ 焊盘修改完成: {orig_pad} → {target_pad}"

"""


def _script_drill_resize(job: str, mat: str, params: dict) -> str:
    """钻孔补偿脚本"""
    drill_layer = params.get('drill_layer', 'drill')
    offset = params.get('offset', 0.1)
    drill_type = params.get('drill_type', 'plated')

    return f"""
# ── 钻孔补偿: {drill_type} 孔偏移 {offset}mm ──

# 1) 选择钻孔层
drill_select \\
    -layer {drill_layer} \\
    -type {drill_type}

# 2) 批量调整钻孔
drill_resize \\
    -layer {drill_layer} \\
    -offset {offset}mm \\
    -type {drill_type} \\
    -mode oversize

# 3) 验证
drill_report \\
    -layer {drill_layer} \\
    -output reports/{mat}_drill_report.txt

echo "✅ 钻孔补偿完成: offset={offset}mm"

"""


def _script_soldermask(job: str, mat: str, params: dict) -> str:
    """阻焊开窗脚本"""
    layer = params.get('layer', 'sm_top')
    pad_exp = params.get('pad_expansion', 0.05)
    trace_exp = params.get('trace_expansion', 0.03)

    return f"""
# ── 阻焊开窗: 焊盘+{pad_exp}mm, 线路+{trace_exp}mm ──

# 1) 阻焊层处理
soldermask_create \\
    -from_layer top \\
    -to_layer {layer} \\
    -pad_expansion {pad_exp}mm \\
    -trace_expansion {trace_exp}mm \\
    -mode auto

# 2) DRC 检查阻焊桥
DRC check \\
    -layers {layer} \\
    -rule soldermask_bridge \\
    -min 0.08mm \\
    -output reports/{mat}_sm_report.txt

echo "✅ 阻焊开窗完成"

"""


def _script_silk_adjust(job: str, mat: str, params: dict) -> str:
    """丝印调整"""
    layer = params.get('layer', 'silk_top')
    offset_x = params.get('offset_x', 0)
    offset_y = params.get('offset_y', 0)
    scale = params.get('scale', 1.0)

    return f"""
# ── 丝印调整: 偏移({offset_x},{offset_y})mm, 缩放{scale}x ──

genesis_select -layer {layer} -type silk

# 平移
transform move \\
    -layer {layer} \\
    -x {offset_x}mm \\
    -y {offset_y}mm

# 缩放（如果需要）
if ({scale} != 1.0) then
    transform scale \\
        -layer {layer} \\
        -factor {scale} \\
        -origin center
endif

echo "✅ 丝印调整完成"

"""


def _script_copper_pour(job: str, mat: str, params: dict) -> str:
    """铜皮修改"""
    layer = params.get('layer', 'top')
    clearance = params.get('clearance', 0.3)
    thermal = params.get('thermal_width', 0.25)
    min_width = params.get('min_width', 0.2)

    return f"""
# ── 铜皮修改: 间距{clearance}mm, 散热{thermal}mm ──

# 1) 铜皮参数设置
copper_pour param \\
    -clearance {clearance}mm \\
    -thermal_width {thermal}mm \\
    -min_width {min_width}mm

# 2) 重新铺铜
copper_pour fill \\
    -layer {layer} \\
    -mode repour_all

# 3) DRC
DRC check \\
    -layers {layer} \\
    -rule copper_island \\
    -min {min_width}mm

echo "✅ 铜皮修改完成"

"""


def _script_impedance(job: str, mat: str, params: dict) -> str:
    """阻抗调整"""
    target_z = params.get('target_impedance', 50)
    is_diff = params.get('diff_pair', False)
    tolerance = params.get('tolerance_percent', 10)
    layer = params.get('layer', 'top')
    pair_label = "diff_pair" if is_diff else "single"

    return f"""
# ── 阻抗调整: {pair_label} {target_z}Ω ±{tolerance}% ──

# 1) 计算当前阻抗
calc_impedance \\
    -layer {layer} \\
    -type {pair_label} \\
    -output reports/{mat}_impedance_before.txt

# 2) 调整参数以达到目标
set i 0
while ($i < 10)
    set actual = `grep "Z0 =" reports/{mat}_impedance_before.txt | awk '{{print $3}}'`
    set diff = `echo "$actual - {target_z}" | bc -l`
    if (abs($diff) < {target_z}*{tolerance}/100.0) break

    # 微调线宽
    DFM resize \\
        -layer {layer} \\
        -select_impedance {target_z} \\
        -type {pair_label} \\
        -tolerance {tolerance}% \\
        -mode auto_tune

    calc_impedance \\
        -layer {layer} \\
        -type {pair_label} \\
        -output reports/{mat}_impedance_result.txt

    @ i++
end

echo "✅ 阻抗调整完成: target={target_z}Ω"

"""


def _script_panelize(job: str, mat: str, params: dict) -> str:
    """拼版"""
    x_count = params.get('x_count', 2)
    y_count = params.get('y_count', 3)
    border = params.get('border', 10)
    fiducial = params.get('fiducial', True)
    tooling = params.get('tooling_holes', True)

    fid_str = "-fiducial yes" if fiducial else ""
    tl_str = "-tooling yes" if tooling else ""

    return f"""
# ── 自动拼版: {x_count}x{y_count}, 工艺边{border}mm ──

# 1) 面板定义
panelize define \\
    -x_count {x_count} \\
    -y_count {y_count} \\
    -border {border}mm \\
    {fid_str} \\
    {tl_str}

# 2) 生成拼版
panelize generate \\
    -job $JOB \\
    -mode auto

# 3) 添加 V-Cut / 邮票孔
panelize tooling \\
    -type vcut \\
    -depth 0.3mm

# 4) 输出报告
panelize report \\
    -output reports/{mat}_panel_report.txt

echo "✅ 拼版完成: {x_count}x{y_count}, 工艺边{border}mm"

"""


# ─── 底部清理 ───
def _genesis_footer(job: str) -> str:
    return f"""
# ── 清理并保存 ──
genesis_save -job $JOB
genesis_close -job $JOB

echo "============================================"
echo "  Genesis 自动化脚本执行完毕"
echo "  作业: $JOB"
echo "============================================"

exit 0
"""


# ─── 命令行入口 ───
if __name__ == '__main__':
    import sys, json

    if len(sys.argv) < 3:
        print("用法: python3 genesis_scripts.py <modify_type> <material_no> [params_json]")
        print(f"支持类型: {list(MODIFY_TYPES.keys())}")
        sys.exit(1)

    modify_type = sys.argv[1]
    material_no = sys.argv[2]
    params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    script = generate_genesis_script(modify_type, material_no, params)
    print(script)
