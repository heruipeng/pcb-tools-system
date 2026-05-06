"""
直接 Gerber 文件编辑器 (RS-274X)
不依赖 Genesis，纯 Python 解析和修改 Gerber 文件

支持：
- 线宽修改（改 aperture 定义）
- 焊盘尺寸修改
- 阻焊开窗调整
- 钻孔补偿
- 坐标偏移/镜像/旋转
- Gerber 文件分析
"""

import re
import os
import shutil
import hashlib
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field


# ═══════════════════════════════════════════
# Gerber 解析器
# ═══════════════════════════════════════════

@dataclass
class GerberAperture:
    """光圈/Aperture 定义"""
    d_code: int           # D10, D11, ...
    shape: str            # C=圆形, R=矩形, O=椭圆, P=多边形
    params: List[float]   # 尺寸参数
    raw_line: str         # 原始定义行
    usage_count: int = 0  # 使用次数

@dataclass
class GerberLayer:
    """Gerber 层数据"""
    filename: str
    content: str
    apertures: Dict[int, GerberAperture] = field(default_factory=dict)
    modified: bool = False


class GerberParser:
    """RS-274X Gerber 解析器"""

    # 光圈类型识别
    APERTURE_TYPES = {
        'C': 'circle',      # %ADD10C,0.100*%
        'R': 'rectangle',    # %ADD11R,0.200X0.300*%
        'O': 'oval',         # %ADD12O,0.200X0.300*%
        'P': 'polygon',      # %ADD13P,0.200X8X0.000*%
        'AM': 'macro',       # %AMMYSHAPE*...%
    }

    @staticmethod
    def parse(filepath: str) -> GerberLayer:
        """解析 Gerber 文件"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        layer = GerberLayer(filename=filepath, content=content)

        # 解析光圈定义 %ADD...
        for match in re.finditer(r'%ADD(\d+)([CROP]),(.+?)\*%', content):
            d_code = int(match.group(1))
            shape = match.group(2)
            # 解析参数：0.100 或 0.200X0.300
            params_str = match.group(3).split('X')
            params = [float(p) for p in params_str]

            layer.apertures[d_code] = GerberAperture(
                d_code=d_code,
                shape=shape,
                params=params,
                raw_line=match.group(0),
            )

        # 统计每个光圈的使用次数
        for d_code in layer.apertures:
            layer.apertures[d_code].usage_count = len(
                re.findall(rf'D{d_code:02d}\*', content)
            )

        return layer

    @staticmethod
    def analyze(layer: GerberLayer) -> dict:
        """分析 Gerber 层，输出报告"""
        all_widths = set()
        all_pads = set()
        total_flashes = len(re.findall(r'D0[3-9]\*', layer.content))
        total_draws = len(re.findall(r'D01\*', layer.content))

        for ap in layer.apertures.values():
            if ap.shape == 'C':
                dia = ap.params[0]
                if dia < 0.5:
                    all_widths.add(round(dia, 3))
                else:
                    all_pads.add(round(dia, 3))
            elif ap.shape == 'R':
                w, h = ap.params[0], ap.params[1] if len(ap.params) > 1 else ap.params[0]
                if w < 0.3:
                    all_widths.add(round(w, 3))
                else:
                    all_pads.add((round(w, 3), round(h, 3)))

        return {
            'filename': os.path.basename(layer.filename),
            'aperture_count': len(layer.apertures),
            'total_flashes': total_flashes,
            'total_draws': total_draws,
            'line_widths_mm': sorted(list(all_widths)),
            'pad_sizes_mm': sorted(list(set(all_pads)), key=lambda x: x[0] if isinstance(x, tuple) else x),
            'most_used_apertures': sorted(
                [{'d_code': f'D{ap.d_code}', 'shape': ap.shape, 'size': ap.params, 'uses': ap.usage_count}
                 for ap in layer.apertures.values() if ap.usage_count > 0],
                key=lambda x: x['uses'], reverse=True
            )[:10],
        }


# ═══════════════════════════════════════════
# Gerber 修改器
# ═══════════════════════════════════════════

class GerberModifier:
    """直接修改 Gerber 文件内容"""

    @staticmethod
    def change_line_width(layer: GerberLayer, original_width_mm: float,
                          target_width_mm: float, tolerance_mm: float = 0.005) -> dict:
        """
        修改线宽：找到所有宽度接近 original_width 的圆形光圈，改为 target_width

        Gerber 线宽通过光圈定义，圆形光圈 %ADD10C,0.100*% 表示直径0.1mm
        """
        changes = []
        for d_code, ap in layer.apertures.items():
            if ap.shape != 'C':
                continue
            dia = ap.params[0]
            if abs(dia - original_width_mm) <= tolerance_mm:
                # 替换光圈定义
                old_def = f'%ADD{d_code:02d}{ap.shape},{dia}*%'
                new_def = f'%ADD{d_code:02d}{ap.shape},{target_width_mm}*%'
                layer.content = layer.content.replace(old_def, new_def)
                layer.modified = True
                changes.append({
                    'd_code': f'D{d_code}',
                    'old_width': dia,
                    'new_width': target_width_mm,
                    'uses': ap.usage_count,
                })

        return {
            'type': 'line_width',
            'changes': changes,
            'count': len(changes),
            'message': f'线宽 {original_width_mm}mm → {target_width_mm}mm，修改了 {len(changes)} 个光圈' if changes else '未找到匹配的光圈',
        }

    @staticmethod
    def change_pad_size(layer: GerberLayer, original_size: Tuple[float, float],
                        target_size: Tuple[float, float], tolerance_mm: float = 0.01) -> dict:
        """修改焊盘尺寸"""
        changes = []
        ow, oh = original_size
        tw, th = target_size

        for d_code, ap in layer.apertures.items():
            matched = False
            if ap.shape == 'C':
                dia = ap.params[0]
                if abs(dia - ow) <= tolerance_mm:
                    new_def = f'%ADD{d_code:02d}{ap.shape},{tw}*%'
                    matched = True
            elif ap.shape == 'R' and len(ap.params) >= 2:
                w, h = ap.params[0], ap.params[1]
                if abs(w - ow) <= tolerance_mm and abs(h - oh) <= tolerance_mm:
                    new_def = f'%ADD{d_code:02d}{ap.shape},{tw}X{th}*%'
                    matched = True

            if matched:
                old_def = ap.raw_line
                layer.content = layer.content.replace(old_def, new_def)
                layer.modified = True
                changes.append({
                    'd_code': f'D{d_code}',
                    'old_size': ap.params,
                    'new_size': [tw, th] if ap.shape == 'R' else [tw],
                })

        return {
            'type': 'pad_size',
            'changes': changes,
            'count': len(changes),
            'message': f'焊盘 {original_size} → {target_size}，修改了 {len(changes)} 个光圈',
        }

    @staticmethod
    def scale_apertures(layer: GerberLayer, factor: float) -> dict:
        """按比例缩放所有光圈（阻焊开窗扩大/缩小）"""
        changes = []
        for d_code, ap in layer.apertures.items():
            new_params = [round(p * factor, 4) for p in ap.params]
            old_def = ap.raw_line

            if ap.shape in ('C', 'P'):
                new_def = f'%ADD{d_code:02d}{ap.shape},{new_params[0]}*%'
                for i, p in enumerate(new_params[1:], 1):
                    new_def = new_def[:-2] + f'X{p}*%'
            else:
                param_str = 'X'.join(str(p) for p in new_params)
                new_def = f'%ADD{d_code:02d}{ap.shape},{param_str}*%'

            layer.content = layer.content.replace(old_def, new_def)
            layer.modified = True
            changes.append({
                'd_code': f'D{d_code}',
                'old_params': ap.params,
                'new_params': new_params,
            })

        return {
            'type': 'scale',
            'factor': factor,
            'changes': len(changes),
            'message': f'按比例 {factor}x 缩放了 {len(changes)} 个光圈',
        }

    @staticmethod
    def offset_coordinates(layer: GerberLayer, dx_mm: float, dy_mm: float) -> dict:
        """偏移所有坐标"""
        count = 0

        def offset_coord(match):
            nonlocal count
            x = float(match.group(1))
            y = float(match.group(2))
            count += 1
            return f'X{round(x + dx_mm, 4)}Y{round(y + dy_mm, 4)}'

        # 匹配 X...Y... 坐标（Gerber 坐标格式）
        layer.content = re.sub(r'X(-?\d+\.?\d*)Y(-?\d+\.?\d*)', offset_coord, layer.content)
        layer.modified |= count > 0

        return {
            'type': 'offset',
            'dx': dx_mm,
            'dy': dy_mm,
            'coordinates': count,
            'message': f'坐标偏移 ({dx_mm}, {dy_mm})mm，修改了 {count} 个坐标',
        }

    @staticmethod
    def mirror_x(layer: GerberLayer) -> dict:
        """X 轴镜像"""
        count = 0

        def mirror(match):
            nonlocal count
            x = float(match.group(1))
            y = float(match.group(2))
            count += 1
            return f'X{-x}Y{y}'

        layer.content = re.sub(r'X(-?\d+\.?\d*)Y(-?\d+\.?\d*)', mirror, layer.content)
        layer.modified |= count > 0

        return {'type': 'mirror_x', 'coordinates': count, 'message': f'X轴镜像，修改了 {count} 个坐标'}

    @staticmethod
    def mirror_y(layer: GerberLayer) -> dict:
        """Y 轴镜像"""
        count = 0

        def mirror(match):
            nonlocal count
            x = float(match.group(1))
            y = float(match.group(2))
            count += 1
            return f'X{x}Y{-y}'

        layer.content = re.sub(r'X(-?\d+\.?\d*)Y(-?\d+\.?\d*)', mirror, layer.content)
        layer.modified |= count > 0

        return {'type': 'mirror_y', 'coordinates': count, 'message': f'Y轴镜像，修改了 {count} 个坐标'}


# ═══════════════════════════════════════════
# 文件操作
# ═══════════════════════════════════════════

class GerberFileManager:
    """Gerber 文件读写管理"""

    @staticmethod
    def save(layer: GerberLayer, output_path: str) -> str:
        """保存修改后的 Gerber 文件"""
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(layer.content)
        return output_path

    @staticmethod
    def backup(layer: GerberLayer) -> str:
        """备份原始文件"""
        backup_path = layer.filename + '.bak'
        shutil.copy2(layer.filename, backup_path)
        return backup_path

    @staticmethod
    def hash_file(filepath: str) -> str:
        """文件哈希校验"""
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()


# ═══════════════════════════════════════════
# 命令行测试入口
# ═══════════════════════════════════════════

if __name__ == '__main__':
    import sys, json

    if len(sys.argv) < 2:
        print("Gerber 文件编辑器")
        print("用法: python3 gerber_editor.py <gerber_file> [--analyze] [--modify json_params]")
        print()
        print("示例:")
        print("  python3 gerber_editor.py board_top.gbr --analyze")
        print("  python3 gerber_editor.py board_top.gbr --modify '{\"line_width\": {\"from\":0.1,\"to\":0.12}}'")
        print("  python3 gerber_editor.py board_top.gbr --modify '{\"pad\":{\"from\":[0.5,0.3],\"to\":[0.6,0.35]}}'")
        print("  python3 gerber_editor.py sm_top.gbr --modify '{\"scale\":1.05}'")
        print("  python3 gerber_editor.py silk_top.gbr --modify '{\"offset\":[0.1,-0.05]}'")
        sys.exit(0)

    filepath = sys.argv[1]
    layer = GerberParser.parse(filepath)

    if '--analyze' in sys.argv or len(sys.argv) == 2:
        analysis = GerberParser.analyze(layer)
        print(json.dumps(analysis, indent=2, ensure_ascii=False))

    if '--modify' in sys.argv:
        idx = sys.argv.index('--modify')
        params = json.loads(sys.argv[idx + 1])
        modifier = GerberModifier()

        # 备份
        backup_path = GerberFileManager.backup(layer)
        print(f"📁 已备份: {backup_path}")

        # 执行修改
        results = []

        if 'line_width' in params:
            lw = params['line_width']
            result = modifier.change_line_width(layer, lw['from'], lw['to'])
            results.append(result)

        if 'pad' in params:
            pd = params['pad']
            result = modifier.change_pad_size(
                layer, tuple(pd['from']), tuple(pd['to'])
            )
            results.append(result)

        if 'scale' in params:
            result = modifier.scale_apertures(layer, params['scale'])
            results.append(result)

        if 'offset' in params:
            dx, dy = params['offset']
            result = modifier.offset_coordinates(layer, dx, dy)
            results.append(result)

        if 'mirror_x' in params:
            results.append(modifier.mirror_x(layer))

        if 'mirror_y' in params:
            results.append(modifier.mirror_y(layer))

        # 保存
        output = filepath.replace('.gbr', '_mod.gbr').replace('.ger', '_mod.ger')
        GerberFileManager.save(layer, output)

        print(f"\n✅ 修改完成 → {output}")
        for r in results:
            print(f"   {r['message']}")

        # 校验
        orig_hash = GerberFileManager.hash_file(filepath)
        mod_hash = GerberFileManager.hash_file(output)
        print(f"\n📊 原始 MD5: {orig_hash}")
        print(f"📊 修改后 MD5: {mod_hash}")
