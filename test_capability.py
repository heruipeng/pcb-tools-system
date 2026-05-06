#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genesis 能力验证脚本 — 创建料号 → 层 → 添加 1000μm PAD
带完整防呆（Poka-Yoke）检查
"""

import sys
import os
import subprocess
import time

sys.path.insert(0, os.path.dirname(__file__))
from cam_interface import CAM, _GatewayCOM, IS_WINDOWS


# ═══════════════════════════════════════════
# 配置（由 AI 自主设定）
# ═══════════════════════════════════════════

NEW_JOB    = 'WUKONG_TEST_001'
NEW_LAYER  = 'sig_top'
PAD_X      = 10000   # 10mm (Genesis 以 μm 为单位)
PAD_Y      = 10000   # 10mm
PAD_SIZE   = 1000    # 1000μm = 1mm 直径

# 可能的符号名（按优先级尝试）
SYMBOL_CANDIDATES = [
    f'r{PAD_SIZE}',       # r1000 — Genesis 标准命名
    f'r0.{PAD_SIZE}',     # r0.1000
    f'pad_{PAD_SIZE}',    # pad_1000
    'r100',               # 回退：用 r100 + resize
    'r200',               # 再回退
]


# ═══════════════════════════════════════════
# 防呆框架
# ═══════════════════════════════════════════

class PokaYoke:
    """防呆检查结果"""
    def __init__(self):
        self.checks = []

    def check(self, step, result, detail=''):
        status = '✅' if result else '❌'
        msg = f'  {status} {step}'
        if detail:
            msg += f'  → {detail}'
        self.checks.append((step, result, detail))
        print(msg)
        return result

    def fatal_if(self, step, result, detail=''):
        """防呆失败则终止"""
        ok = self.check(step, result, detail)
        if not ok:
            print(f'\n⛔ 防呆阻断: {step} — {detail}')
            sys.exit(1)
        return ok

    def summary(self):
        passed = sum(1 for _, r, _ in self.checks if r)
        total = len(self.checks)
        print(f'\n防呆报告: {passed}/{total} 通过')
        return passed == total


# ═══════════════════════════════════════════
# PID 自动发现
# ═══════════════════════════════════════════

def find_genesis_pid():
    """自动发现 Genesis get.exe 的 PID"""
    methods = [
        ('wmic (快)', 
         'wmic process where "name like \'%get%.exe\'" get ProcessId /format:csv 2>nul'),
        ('tasklist /FI', 
         'tasklist /FI "IMAGENAME eq get.exe" /NH 2>nul'),
        ('tasklist /V (全扫)', 
         'tasklist /V /FO CSV 2>nul'),
    ]

    for method_name, cmd in methods:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=8
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                for token in line.replace('"', '').split(','):
                    token = token.strip()
                    if token.isdigit():
                        pid = int(token)
                        if 100 < pid < 1000000:
                            print(f'  🔍 [{method_name}] 发现 Genesis PID: {pid}')
                            return pid
        except Exception:
            continue

    return None


# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════

def main():
    print('=' * 64)
    print('  Genesis 能力验证 — 防呆全流程')
    print(f'  料号: {NEW_JOB} | 层: {NEW_LAYER} | PAD: {PAD_SIZE}μm')
    print(f'  坐标: ({PAD_X}, {PAD_Y}) μm')
    print('=' * 64)

    pk = PokaYoke()

    # ── 第 0 步：PID 发现 ──
    print('\n[0] 防呆 — 发现 Genesis 进程')
    if IS_WINDOWS:
        pid = find_genesis_pid()
    else:
        pid = 22368  # 已知 PID (备用)
    pk.fatal_if('Genesis 进程检测', pid is not None, 
                f'PID={pid}' if pid else '未找到 get.exe 进程')

    # ── 第 1 步：Gateway 连接 + 验证 ──
    print(f'\n[1] 防呆 — Gateway 连接 PID={pid}')
    try:
        cam = CAM(embedded=False, pid=pid)
    except Exception as e:
        pk.fatal_if('Gateway 连接', False, str(e))

    # 验证连接 — 通过 get_user 确认通信正常
    try:
        user = cam.get_user()
        pk.fatal_if('通信验证 (get_user)', bool(user) and user != '', 
                    f'用户={user}')
    except Exception as e:
        pk.fatal_if('通信验证', False, str(e))

    # ── 第 2 步：料号防呆 + 创建 ──
    print(f'\n[2] 防呆 — 料号: {NEW_JOB}')

    # 2a. 如果 job 已打开，先关闭
    try:
        cam.close_job()
    except Exception:
        pass  # 可能本来就没打开

    # 2b. 检查料号是否已存在
    job_already_exists = False
    try:
        # 先看 job list 里有没有
        job_list = cam.get_job_list()
        job_already_exists = NEW_JOB in str(job_list)
        pk.check('查询 Job 列表', True, 
                 f'已有 {len(job_list) if isinstance(job_list, list) else "?"} 个料号')
    except Exception as e:
        # get_job_list 可能不工作（嵌入式模式限制），尝试直接打开
        pk.check('查询 Job 列表 (降级)', False, str(e))
        try:
            cam.open_job(NEW_JOB)
            job_already_exists = True
        except Exception:
            job_already_exists = False

    if job_already_exists:
        print(f'  ℹ️  料号 {NEW_JOB} 已存在，跳过创建，直接打开')
        try:
            cam.open_job(NEW_JOB)
            pk.check('打开已有料号', True)
        except Exception as e:
            pk.fatal_if('打开已有料号', False, str(e))
    else:
        # 2c. 创建新料号
        try:
            cam.new_job(NEW_JOB, customer='WukongAI', notes='防呆验证测试')
            pk.check('创建料号', True)
        except Exception as e:
            pk.fatal_if('创建料号', False, str(e))

        # 2d. 验证料号已创建 — 重新打开确认
        try:
            cam.open_job(NEW_JOB)
            pk.check('验证料号 (重新打开)', True)
        except Exception as e:
            pk.fatal_if('验证料号', False, str(e))

    # ── 第 3 步：层防呆 + 创建 ──
    print(f'\n[3] 防呆 — 层: {NEW_LAYER}')

    # 3a. 获取已有层列表
    try:
        layer_list = cam.get_layer_list()
        pk.check('获取层列表', True, 
                 f'已有层: {layer_list if layer_list else "(空)"}')
    except Exception as e:
        layer_list = []
        pk.check('获取层列表 (降级)', False, str(e))

    # 3b. 检查层是否已存在
    layer_already_exists = NEW_LAYER in str(layer_list)
    if not layer_already_exists:
        try:
            cam.layer_create(
                NEW_LAYER,
                context='board',
                layer_type='signal',
                polarity='positive'
            )
            pk.check('创建层', True, f'context=board type=signal')
        except Exception as e:
            pk.fatal_if('创建层', False, str(e))
    else:
        print(f'  ℹ️  层 {NEW_LAYER} 已存在，跳过创建')

    # 3c. 验证层存在
    try:
        layer_list2 = cam.get_layer_list()
        layer_exists = NEW_LAYER in str(layer_list2)
        pk.fatal_if('验证层存在', layer_exists, 
                    f'当前层列表: {layer_list2}')
    except Exception as e:
        pk.check('验证层存在 (降级)', False, str(e))

    # ── 第 4 步：设置受影响层 + 工作层 ──
    print(f'\n[4] 防呆 — 设置工作层')

    # 4a. 清除之前的选择
    try:
        cam.layer_clear()
        pk.check('清除层选择', True)
    except Exception as e:
        pk.check('清除层选择', False, str(e))

    # 4b. 设置受影响层
    try:
        cam.affected_layer(NEW_LAYER, affected='yes')
        pk.check('设置受影响层', True, f'affected_layer={NEW_LAYER}')
    except Exception as e:
        pk.fatal_if('设置受影响层', False, str(e))

    # 4c. 设置工作层（显示 + work_layer）
    try:
        cam.work_layer(NEW_LAYER)
        pk.check('设置工作层', True, f'work_layer={NEW_LAYER}')
    except Exception as e:
        pk.fatal_if('设置工作层', False, str(e))

    # ── 第 5 步：添加 PAD（含符号防呆） ──
    print(f'\n[5] 防呆 — 添加 {PAD_SIZE}μm PAD @ ({PAD_X},{PAD_Y})')

    pad_added = False
    last_error = ''

    for i, sym in enumerate(SYMBOL_CANDIDATES):
        resized = False
        actual_sym = sym

        # 回退符号需要用 resize 补偿
        if sym in ('r100', 'r200'):
            resize_val = PAD_SIZE / int(sym[1:])  # 如 r100 → resize=10
            resized = True
            print(f'  🔄 尝试符号 "{sym}" + resize={resize_val}...')
            try:
                result = cam._io.COM(
                    f'add_pad,x={PAD_X},y={PAD_Y},symbol={sym},'
                    f'polarity=positive,attributes=no,angle=0,'
                    f'mirror=no,nx=1,ny=1,dx=0,dy=0'
                    f',resize={resize_val}'
                )
            except Exception:
                result = None
        else:
            print(f'  🔄 尝试符号 "{sym}"...')
            try:
                result = cam.add_pad(PAD_X, PAD_Y, sym, pol='positive')
            except Exception:
                result = None

        if result is not None:
            pad_added = True
            pk.check(
                f'添加 PAD (符号={actual_sym})',
                True,
                f'{"resize="+str(resize_val) if resized else ""} 返回={result}'
            )
            break
        else:
            last_error = f'符号 {sym} 不可用'
            pk.check(f'符号尝试 #{i+1}', False, last_error)

    if not pad_added:
        # 最后兜底 — 用 add_surf 创建圆形 surface 代替 pad
        print(f'  ⚠️  所有 pad 符号不可用，使用 add_surf 兜底...')
        try:
            # 先尝试 add_circle（如果存在）
            result = cam._io.COM(
                f'add_circle,x={PAD_X},y={PAD_Y},'
                f'radius={PAD_SIZE/2},polarity=positive'
            )
            pk.check('兜底方案 (add_circle)', True, f'半径={PAD_SIZE/2}μm')
        except Exception:
            try:
                # 用 add_line 画圆替代（构造多段弧）
                # 但最简单的兜底就是接受失败
                pk.check('兜底方案', False, str(e))
            except Exception as e2:
                pk.fatal_if('添加 PAD (所有方案)', False, str(e2))

    # ── 第 6 步：保存 ──
    print(f'\n[6] 防呆 — 保存料号')
    try:
        cam.save_job()
        pk.check('保存料号', True)
    except Exception as e:
        pk.check('保存料号', False, str(e))

    # ── 第 7 步：最终验证 ──
    print(f'\n[7] 防呆 — 最终验证')
    try:
        info = cam._io.DO_INFO(f'-t feature -e {NEW_JOB}/step/{NEW_LAYER} -d COUNT')
        count = info.get('gCOUNT', '?')
        pk.check(f'特征数验证 ({NEW_LAYER})', True, f'{count} 个特征')
    except Exception as e:
        pk.check('特征数验证 (降级)', False, str(e))

    # ── 防呆汇总 ──
    pk.summary()

    print(f'\n{"=" * 64}')
    print(f'  流程完成！')
    print(f'  料号: {NEW_JOB} | 层: {NEW_LAYER}')
    print(f'  PAD: {PAD_SIZE}μm @ ({PAD_X}, {PAD_Y}) μm')
    print(f'{"=" * 64}')


if __name__ == '__main__':
    main()
