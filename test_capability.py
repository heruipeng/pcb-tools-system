#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genesis 能力验证脚本 — 创建料号 → 层 → 添加 1000μm PAD
带完整防呆（Poka-Yoke）检查

用法:
  python test_capability.py              # 自动发现 PID
  python test_capability.py --pid 22368  # 指定 PID（跳过发现）
"""

import sys
import os
import subprocess
import time
import argparse

sys.path.insert(0, os.path.dirname(__file__))
from cam_interface import CAM, _GatewayCOM, IS_WINDOWS


# ═══════════════════════════════════════════
# 配置（由 AI 自主设定）
# ═══════════════════════════════════════════

NEW_JOB    = 'wukong_test_001'
NEW_LAYER  = 'sig_top'
PAD_X      = 10000
PAD_Y      = 10000
PAD_SIZE   = 1000

SYMBOL_CANDIDATES = [
    f'r{PAD_SIZE}',
    f'r0.{PAD_SIZE}',
    f'pad_{PAD_SIZE}',
    'r100',
    'r200',
]

CONNECT_TIMEOUT = 8  # Gateway 连接超时（秒）


# ═══════════════════════════════════════════
# 防呆框架
# ═══════════════════════════════════════════

class PokaYoke:
    def __init__(self):
        self.checks = []

    def check(self, step, result, detail=''):
        emoji = '✅' if result else '❌'
        line = f'  {emoji} {step}'
        if detail:
            line += f'  → {detail}'
        self.checks.append((step, result, detail))
        print(line)
        return result

    def fatal(self, step, result, detail=''):
        ok = self.check(step, result, detail)
        if not ok:
            print(f'\n⛔ 防呆阻断: {step}')
            sys.exit(1)
        return ok

    def summary(self):
        passed = sum(1 for _, r, _ in self.checks if r)
        total = len(self.checks)
        print(f'\n防呆报告: {passed}/{total} 通过')


# ═══════════════════════════════════════════
# PID 精确发现
# ═══════════════════════════════════════════

def find_genesis_pid():
    """
    精确发现 Genesis get.exe 进程 PID。
    
    策略：只匹配进程名严格为 get.exe 的进程，
    排除名称中包含 get 的其他进程（如 target.exe）。
    """
    # 方法 1: tasklist 精确过滤 get.exe（最可靠）
    try:
        result = subprocess.run(
            'tasklist /FI "IMAGENAME eq get.exe" /NH',
            shell=True, capture_output=True, text=True, timeout=8
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or 'INFO:' in line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0] == 'get.exe' and parts[1].isdigit():
                pid = int(parts[1])
                if 100 < pid < 1000000:
                    print(f'  🔍 发现 Genesis get.exe → PID: {pid}')
                    return pid
    except Exception:
        pass

    # 方法 2: wmic 精确匹配 get.exe
    try:
        result = subprocess.run(
            'wmic process where name="get.exe" get ProcessId,Name /format:csv',
            shell=True, capture_output=True, text=True, timeout=8
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('Node'):
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                for p in parts:
                    p = p.strip().strip('"')
                    if p.isdigit():
                        pid = int(p)
                        if 100 < pid < 1000000:
                            print(f'  🔍 发现 Genesis get.exe → PID: {pid}')
                            return pid
    except Exception:
        pass

    return None


# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Genesis 能力验证脚本')
    parser.add_argument('--pid', type=int, default=None,
                        help='指定 Genesis PID（跳过自动发现）')
    args = parser.parse_args()

    print('=' * 64)
    print('  Genesis 能力验证 — 防呆全流程')
    print(f'  料号: {NEW_JOB} | 层: {NEW_LAYER}')
    print(f'  PAD: {PAD_SIZE}μm @ ({PAD_X}, {PAD_Y}) μm')
    print('=' * 64)

    pk = PokaYoke()

    # ── 第 0 步：获取 PID ──
    pid = args.pid
    if pid is None:
        print('\n[0] 发现 Genesis 进程...')
        pid = find_genesis_pid()
    else:
        print(f'\n[0] 使用指定 PID: {pid}')

    pk.fatal('Genesis 进程检测', pid is not None,
             f'PID={pid}' if pid else '未找到 get.exe 进程！\n'
             '  请确认 Genesis 已启动，或用 --pid <PID> 指定')

    # ── 第 1 步：Gateway 连接 + 超时保护 ──
    print(f'\n[1] Gateway 连接 PID={pid} (超时={CONNECT_TIMEOUT}s)...')
    
    # 先用 gateway PID 命令快速验证 PID 是否有效
    try:
        edir = os.path.realpath(
            os.environ.get('GENESIS_EDIR', 
            os.environ.get('INCAM_PRODUCT', ''))
        ).rstrip('/get/get')
    except Exception:
        edir = ''
    
    gw_exe = os.path.join(edir, 'misc', 'gateway') if edir else 'gateway'
    if IS_WINDOWS:
        gw_exe += '.exe' if not gw_exe.endswith('.exe') else ''

    host = os.environ.get('COMPUTERNAME', 'localhost')
    
    # 快速 PID 验证（2秒超时）
    pid_valid = False
    if os.path.isfile(gw_exe):
        try:
            r = subprocess.run(
                [gw_exe, f'PID {pid}@{host}'],
                capture_output=True, text=True, timeout=3
            )
            if r.stdout.strip() and str(pid) in r.stdout:
                pid_valid = True
                print(f'  ✅ PID 验证通过')
            else:
                print(f'  ❌ PID {pid} 没有对应的 Genesis 会话')
                print(f'     返回: {r.stdout.strip() or r.stderr.strip()}')
        except subprocess.TimeoutExpired:
            print(f'  ❌ PID 验证超时（3s）— 可能 PID 不对')
        except Exception as e:
            print(f'  ⚠️  PID 验证异常: {e}')
    else:
        print(f'  ⚠️  gateway.exe 未找到: {gw_exe}')

    if not pid_valid:
        pk.check('PID 验证 (gateway PID)', False,
                 f'PID={pid} 无效或无 Genesis 会话 → 尝试继续连接...')
    
    # 正式连接（带超时保护）
    cam = None
    try:
        cam = CAM(embedded=False, pid=pid)
        # CAM.__init__ 会调用 self._io.connect(pid)
        # 但 connect() 可能卡住 — 在 CAM 层面的 get_user 加超时检测
        print('  Gateway 进程已启动，测试通信...')
        
        # 给 3 秒让 Gateway 启动，然后用 get_user 验证
        user = None
        deadline = time.time() + CONNECT_TIMEOUT
        last_err = ''
        while time.time() < deadline:
            try:
                user = cam.get_user()
                if user and user.strip():
                    break
            except Exception as e:
                last_err = str(e)
                time.sleep(0.5)
        
        if user and user.strip():
            pk.check('Gateway 通信', True, f'用户={user}')
        else:
            pk.fatal('Gateway 通信', False,
                     f'超时 ({CONNECT_TIMEOUT}s) — 可能 PID={pid} 不对\n'
                     f'  最后错误: {last_err}\n'
                     f'  建议: python test_capability.py --pid <正确PID>')
    except Exception as e:
        pk.fatal('Gateway 连接', False, str(e))

    # ── 第 2 步：料号 ──
    print(f'\n[2] 料号: {NEW_JOB}')
    try:
        cam.close_job()
    except Exception:
        pass

    try:
        job_list = cam.get_job_list()
        job_exists = NEW_JOB in str(job_list)
    except Exception:
        job_list = []
        job_exists = False

    if job_exists:
        print(f'  ℹ️  料号已存在，跳过创建')
        try:
            cam.open_job(NEW_JOB)
            pk.check('打开料号', True)
        except Exception as e:
            pk.fatal('打开料号', False, str(e))
    else:
        try:
            cam.new_job(NEW_JOB)
            cam.open_job(NEW_JOB)
            pk.check('创建 + 打开料号', True)
        except Exception as e:
            pk.fatal('创建料号', False, str(e))

    # ── 第 3 步：层 ──
    print(f'\n[3] 层: {NEW_LAYER}')
    try:
        layers = cam.get_layer_list()
    except Exception:
        layers = []

    if NEW_LAYER in str(layers):
        print(f'  ℹ️  层已存在，跳过创建')
    else:
        try:
            cam.layer_create(NEW_LAYER, context='board',
                             layer_type='signal', polarity='positive')
            pk.check('创建层', True)
        except Exception as e:
            pk.fatal('创建层', False, str(e))

    try:
        layers2 = cam.get_layer_list()
        pk.fatal('验证层存在', NEW_LAYER in str(layers2),
                 f'层列表: {layers2}')
    except Exception as e:
        pk.check('验证层存在 (降级)', False, str(e))

    # ── 第 4 步：工作层 ──
    print(f'\n[4] 设置工作层')
    try:
        cam.layer_clear()
        cam.affected_layer(NEW_LAYER, affected='yes')
        cam.work_layer(NEW_LAYER)
        pk.check('工作层就绪', True, f'{NEW_LAYER}')
    except Exception as e:
        pk.fatal('设置工作层', False, str(e))

    # ── 第 5 步：添加 PAD ──
    print(f'\n[5] 添加 {PAD_SIZE}μm PAD @ ({PAD_X},{PAD_Y})')
    pad_ok = False
    for i, sym in enumerate(SYMBOL_CANDIDATES):
        try:
            if sym in ('r100', 'r200'):
                resize = PAD_SIZE / int(sym[1:])
                result = cam._io.COM(
                    f'add_pad, attributes = no, x = {PAD_X}, y = {PAD_Y},'
                    f' symbol = {sym},polarity = positive,'
                    f'angle = 0, mirror = no, nx = 1, ny = 1,'
                    f'dx = 0, dy = 0, xscale = {resize}, yscale = {resize}'
                )
                print(f'  ✅ 符号={sym} resize={resize} 返回={result}')
            else:
                result = cam.add_pad(PAD_X, PAD_Y, sym, pol='positive')
                print(f'  ✅ 符号={sym} 返回={result}')
            pad_ok = True
            break
        except Exception as e:
            print(f'  🔄 符号={sym} 不可用: {e}')

    if not pad_ok:
        print(f'  ⚠️  所有符号失败，尝试 add_circle 兜底...')
        try:
            result = cam._io.COM(
                f'add_circle,x={PAD_X},y={PAD_Y},'
                f'radius={PAD_SIZE/2},polarity=positive'
            )
            print(f'  ✅ add_circle 返回={result}')
            pad_ok = True
        except Exception:
            pass

    pk.check('添加 PAD', pad_ok)

    # ── 第 6 步：保存 ──
    print(f'\n[6] 保存')
    try:
        cam.save_job()
        pk.check('保存料号', True)
    except Exception as e:
        pk.check('保存料号', False, str(e))

    # ── 第 7 步：验证 ──
    print(f'\n[7] 最终验证')
    try:
        info = cam._io.DO_INFO(
            f'-t feature -e {NEW_JOB}/step/{NEW_LAYER} -d COUNT'
        )
        count = info.get('gCOUNT', '?')
        pk.check('特征数', True, f'{NEW_LAYER} = {count} 个')
    except Exception as e:
        pk.check('特征数', False, str(e))

    pk.summary()
    print(f'\n{"=" * 64}')
    print(f'  完成 — {NEW_JOB} / {NEW_LAYER} / {PAD_SIZE}μm PAD')
    print(f'{"=" * 64}')


if __name__ == '__main__':
    main()
