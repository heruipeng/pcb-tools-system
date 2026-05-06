#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genesis / InCAMPro 统一 COM 接口库
=================================
同时兼容 Genesis 和 InCAMPro，自动检测环境并适配。

两种通信模式：
  1. 嵌入式模式（genCOM）：Python 在 Genesis 内部运行，通过 STDOUT/STDIN 通信
  2. 网关模式（Gateway）：Python 在外部运行，通过 gateway.exe 管道连接

使用方式：
  from cam_interface import CAM

  # 嵌入式模式（Genesis 内运行）
  cam = CAM(embedded=True)

  # 网关模式（外部连接）
  cam = CAM(job="MY-JOB", pid=5236)

  # 通用操作
  cam.layer_open("top")
  cam.line_width_change("top", 0.1, 0.12)
  cam.output_gerber("top", "output/top_mod.gbr")

环境要求：
  Genesis:  GENESIS_EDIR 环境变量
  InCAMPro: INCAM_PRODUCT 环境变量
"""

import os
import sys
import re
import time
import socket
import getpass
import subprocess
import platform


# ═══════════════════════════════════════════
# 环境检测
# ═══════════════════════════════════════════

IS_INCAM = 'INCAM_PRODUCT' in os.environ
IS_GENESIS = 'GENESIS_EDIR' in os.environ
IS_WINDOWS = platform.system() == 'Windows'

if IS_INCAM:
    CAM_SOFTWARE = 'InCAMPro'
    CAM_EDIR = os.environ['INCAM_PRODUCT']
    CAM_DIR = '/incam'
elif IS_GENESIS:
    CAM_SOFTWARE = 'Genesis'
    CAM_EDIR = os.environ['GENESIS_EDIR']
    CAM_DIR = os.environ.get('GENESIS_DIR', '/genesis')
else:
    CAM_SOFTWARE = 'Unknown'
    CAM_EDIR = None
    CAM_DIR = None


# ═══════════════════════════════════════════
# 底层通信基础类
# ═══════════════════════════════════════════

class _BaseCOM:
    """底层 COM 通信"""

    def __init__(self):
        self.prefix = '@%#%@'
        self.STATUS = None
        self.READANS = None
        self.COMANS = None
        tmp = f'cam_{os.getpid()}.{time.time()}'
        if IS_WINDOWS:
            self.tmpfile = os.path.join(os.environ.get('GENESIS_TMP', 'c:/tmp'), tmp)
        else:
            self.tmpfile = os.path.join('/tmp', tmp)

    def __del__(self):
        if os.path.isfile(self.tmpfile):
            os.unlink(self.tmpfile)

    def _send(self, cmd, args=''):
        wsp = ' ' if args else ''
        msg = f'{self.prefix}{cmd}{wsp}{args}\n'
        sys.stdout.write(msg)
        sys.stdout.flush()
        return 0

    def COM(self, args):
        """通用 COM 命令"""
        self._send('COM', args)
        self.STATUS = int(sys.stdin.readline())
        self.READANS = sys.stdin.readline().strip()
        self.COMANS = self.READANS[:]
        return self.STATUS

    def PAUSE(self, msg):
        """暂停并显示消息"""
        self._send('PAUSE', msg)
        self.STATUS = int(sys.stdin.readline())
        self.READANS = sys.stdin.readline()
        return self.STATUS

    def INFO(self, args, units='mm'):
        """获取信息（输出到临时文件后读取）"""
        self.COM(f'info,out_file={self.tmpfile},write_mode=replace,units={units},args={args}')
        with open(self.tmpfile, 'r') as f:
            lines = f.readlines()
        os.unlink(self.tmpfile)
        return lines

    def DO_INFO(self, args, units='mm'):
        """获取信息并以字典形式返回"""
        lines = self.INFO(args, units)
        return self._parse_info(lines)

    def _parse_info(self, info_list):
        """解析 INFO 命令返回的 csh 格式数据"""
        result = {}
        for line in info_list:
            parts = line.split(' = ', 1)
            if len(parts) == 2:
                key = parts[0].strip()[4:]  # 去掉 "set " 前缀
                val = parts[1].strip()
                val_list = val.split("'")
                if '(' in val:
                    result[key] = [val_list[i] for i in range(1, len(val_list), 2)]
                elif len(val_list) == 3:
                    result[key] = val_list[1]
                elif len(val_list) == 1:
                    result[key] = val
        return result


class _GatewayCOM:
    """Gateway 管道通信模式"""

    def __init__(self, pid=None):
        self.host = socket.gethostname().split('.')[0]
        self.user = getpass.getuser()
        self.pipe = None
        self.COMANS = ''
        self.STATUS = 0
        tmp = f'gateway_{pid}.info' if pid else f'cam_gw_{time.time()}'
        if IS_WINDOWS:
            self.tmpfile = os.path.join(os.environ.get('GENESIS_TMP', 'c:/tmp'), tmp)
        else:
            self.tmpfile = os.path.join('/tmp', tmp)

        if pid:
            self.connect(pid)

    def connect(self, pid):
        """连接到指定 PID 的 Genesis/InCAMPro 进程"""
        self.address = f'%{pid}@{self.host}'
        edir = os.path.realpath(CAM_EDIR).rstrip('/get/get')
        gw_exe = os.path.join(edir, 'misc', 'gateway')

        if not os.path.isfile(gw_exe):
            raise FileNotFoundError(f'Gateway not found: {gw_exe}')

        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.pipe = subprocess.Popen(
            [gw_exe, self.address],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding='utf-8',
            creationflags=creationflags,
        )
        return True

    def disconnect(self):
        if self.pipe:
            self.pipe.stdin.close()
            self.pipe.stdout.close()
            self.pipe = None

    def _gw_cmd(self, command):
        """通过管道发送命令"""
        if not self.pipe:
            raise ConnectionError('Gateway 未连接')
        self.pipe.stdin.write(f'{command}\n')
        self.pipe.stdin.flush()
        return self.pipe.stdout.readline().strip()

    def COM(self, args):
        self.STATUS = 0
        self.COMANS = self._gw_cmd(f'COM {args}')
        return self.STATUS

    def PAUSE(self, msg):
        return int(self._gw_cmd(f'PAUSE {msg}'))

    def INFO(self, args, units='mm'):
        self.COM(f'info,out_file={self.tmpfile},write_mode=replace,units={units},args={args}')
        with open(self.tmpfile, 'r') as f:
            lines = f.readlines()
        os.unlink(self.tmpfile)
        return lines

    def DO_INFO(self, args, units='mm'):
        lines = self.INFO(args, units)
        return _BaseCOM()._parse_info(lines)


# ═══════════════════════════════════════════
# CAM 统一操作类
# ═══════════════════════════════════════════

class CAM:
    """
    Genesis / InCAMPro 统一操作接口

    参数:
        embedded: True=嵌入式（脚本在CAM内运行）
        pid: Gateway 模式指定进程PID
        job: 当前料号名
        step: 当前Step名
    """

    def __init__(self, embedded=True, pid=None, job=None, step=None):
        self.job = job
        self.step = step
        self.software = CAM_SOFTWARE

        if embedded:
            self._io = _BaseCOM()
        elif pid:
            self._io = _GatewayCOM(pid)
        else:
            raise ValueError("必须指定 embedded=True 或 pid=进程号")

    # ─── 基本信息 ───
    def get_user(self):
        self._io.COM('get_user_name')
        return self._io.COMANS

    def get_units(self):
        self._io.COM('get_units')
        return self._io.COMANS

    def get_job_list(self):
        """获取数据库中的料号列表"""
        lines = self._io.INFO('jobs -d')
        return [l.strip() for l in lines if l.strip()]

    # ─── 资料操作 ───
    def open_job(self, job, database='database'):
        self.job = job
        return self._io.COM(f'open_job,job={job},database={database}')

    def save_job(self):
        return self._io.COM(f'save_job,job={self.job}')

    def close_job(self):
        self._io.VOF = getattr(self._io, 'VOF', lambda: self._io.COM('vof'))
        self._io.COM(f'close_job,job={self.job}')
        return self._io.STATUS

    def check_out(self, job):
        """Check out 防止其他人修改"""
        return self._io.COM(f'check_inout,mode=out,type=job,job={job}')

    def check_in(self, job):
        return self._io.COM(f'check_inout,mode=in,type=job,job={job}')

    # ─── Step 操作 ───
    def open_step(self, step):
        self.step = step
        return self._io.COM(f'editor_page_open,page=edit,step={step}')

    def close_step(self):
        return self._io.COM('editor_page_close')

    def create_step(self, step, profile_step=''):
        return self._io.COM(
            f'create_entity,job={self.job},is_fw=no,type=step,name={step},db={self.job}'
        )

    # ─── 层操作 ───
    def get_layer_list(self):
        """获取当前 Step 的所有层"""
        info = self._io.DO_INFO('layers')
        return info.get('gROWname', [])

    def layer_open(self, layer):
        """打开指定层进入编辑"""
        return self._io.COM(f'open_layer,layer={layer}')

    def layer_create(self, layer, context='misc', layer_type='signal',
                     polarity='positive', ins_layer='', location='after'):
        return self._io.COM(
            f'create_layer,layer={layer},context={context},type={layer_type},'
            f'polarity={polarity},ins_layer={ins_layer},location={location}'
        )

    def layer_delete(self, layer):
        return self._io.COM(f'delete_layer,layer={layer}')

    def layer_clear(self):
        self._io.COM('clear_layers')
        self._io.COM('affected_layer,name=,mode=all,affected=no')

    def affected_layer(self, name, affected='yes'):
        return self._io.COM(f'affected_layer,name={name},mode=single,affected={affected}')

    # ─── 选择与过滤 ───
    def sel_clear(self):
        self._io.COM('clear_highlight')
        self._io.COM('sel_clear_feat')

    def filter_reset(self):
        self._io.COM('filter_reset,area=all')
        self._io.COM('filter_area_strt')

    def filter_set_line(self, min_width=0, max_width=999, layer=''):
        return self._io.COM(
            f'filter_set,feat_type=line,min_len=0,'
            f'min_seg=0,max_seg=99999,min_width={min_width},'
            f'max_width={max_width},layer={layer}'
        )

    def filter_select_all(self, layer=''):
        self.filter_reset()
        self.filter_set_line(layer=layer)
        return self._io.COM('filter_area_end,layer=,mode=select,filter=filtered_only')

    def filter_select_width(self, width, tolerance=0.005, layer=''):
        """选择指定线宽的所有线路"""
        self.filter_reset()
        self.filter_set_line(
            min_width=width - tolerance,
            max_width=width + tolerance,
            layer=layer,
        )
        return self._io.COM('filter_area_end,layer=,mode=select,filter=filtered_only')

    # ─── 编辑操作 ───
    def sel_resize(self, new_width):
        """修改选中线路的宽度"""
        return self._io.COM(f'sel_resize,width={new_width},corner=round')

    def sel_delete(self):
        return self._io.COM('sel_delete_feat')

    def sel_copy(self, dx=0, dy=0, nx=1, ny=1, angle=0, mirror='no'):
        return self._io.COM(
            f'sel_copy_other,x_offset={dx},y_offset={dy},'
            f'nx={nx},ny={ny},angle={angle},mirror={mirror}'
        )

    def sel_move(self, dx=0, dy=0):
        return self._io.COM(f'sel_transform,mode=move,x_offset={dx},y_offset={dy}')

    def sel_scale(self, factor, origin='center'):
        return self._io.COM(f'sel_transform,mode=scale,factor={factor},origin={origin}')

    def sel_mirror(self, axis='x'):
        mode = 'mirror_x' if axis == 'x' else 'mirror_y'
        return self._io.COM(f'sel_transform,mode={mode}')

    # ─── 钻孔 ───
    def drill_select_type(self, drill_type='plated'):
        return self._io.COM(f'drill_select,type={drill_type}')

    def drill_resize(self, offset, layer='drill'):
        return self._io.COM(f'drill_resize,layer={layer},offset={offset},mode=oversize')

    def drill_report(self, layer='drill'):
        return self._io.COM(f'drill_report,layer={layer},output={self._io.tmpfile}')

    # ─── 阻焊 ───
    def soldermask_create(self, from_layer, to_layer, pad_expansion=0.05, trace_expansion=0.03):
        return self._io.COM(
            f'soldermask_create,from_layer={from_layer},to_layer={to_layer},'
            f'pad_expansion={pad_expansion},trace_expansion={trace_expansion},mode=auto'
        )

    def soldermask_update(self, pad_expansion=0.05):
        return self._io.COM(f'soldermask_update,pad_expansion={pad_expansion},mode=auto')

    # ─── 铜皮 ───
    def copper_pour_param(self, clearance=0.3, thermal_width=0.25, min_width=0.2):
        return self._io.COM(
            f'copper_pour,param,clearance={clearance},'
            f'thermal_width={thermal_width},min_width={min_width}'
        )

    def copper_pour_fill(self, layer, mode='repour_all'):
        return self._io.COM(f'copper_pour,fill,layer={layer},mode={mode}')

    # ─── DRC ───
    def drc_check(self, layers, rule='spacing', min_val=0.1):
        return self._io.COM(
            f'drc,check,layers={layers},rule={rule},'
            f'min={min_val},output={self._io.tmpfile}'
        )

    # ─── 资料比对 ───
    def compare_layers(self, layer1, job2, layer2, tol=0.0254):
        result = self._io.COM(
            f'compare_layers,layer1={layer1},job2={job2},layer2={layer2},'
            f'tol={tol},consider_sr=yes,area=global'
        )
        return self._io.COMANS

    # ─── 面积计算 ───
    def copper_area(self, layer, units='sqmm'):
        info = self._io.DO_INFO(f'copper,layer={layer}')
        return float(info.get('area_copper', 0))

    def exposed_area(self, layer):
        info = self._io.DO_INFO(f'exposed,layer={layer}')
        return float(info.get('area_exposed', 0))

    # ─── 输出 ───
    def output_gerber(self, layer, output_path, mirrors='no', optimize='yes'):
        """输出 Gerber 文件"""
        return self._io.COM(
            f'output,type=gerber,layer={layer},out_file={output_path},'
            f'mirrors={mirrors},optimize={optimize},units={self.get_units()}'
        )

    def output_excellon(self, layer, output_path):
        """输出钻孔文件"""
        return self._io.COM(
            f'output,type=excellon,layer={layer},out_file={output_path}'
        )

    # ─── 高级：线宽修改完整流程 ───
    def line_width_change(self, layer, from_width, to_width, tolerance=0.005):
        """
        在指定层中修改线宽（完整工作流）
        1. 打开层
        2. 按线宽筛选线路
        3. 批量修改宽度
        4. 清除选择
        """
        results = []

        self.layer_open(layer)

        # 选择指定线宽的线路
        status = self.filter_select_width(from_width, tolerance, layer)
        if status != 0:
            return {'error': '选择线路失败', 'status': status}

        # 执行修改
        status = self.sel_resize(to_width)
        results.append({
            'layer': layer,
            'from_width': from_width,
            'to_width': to_width,
            'status': 'OK' if status == 0 else 'FAIL',
        })

        # 清除
        self.sel_clear()
        self.filter_reset()

        return {'results': results, 'count': len(results)}

    # ─── 高级：焊盘批量替换 ───
    def pad_replace(self, layer, old_symbol, new_symbol):
        """
        批量替换焊盘
        """
        self.layer_open(layer)
        self.filter_reset()
        self._io.COM(f'filter_set,feat_type=pad,symbol={old_symbol},layer={layer}')
        self._io.COM('filter_area_end,layer=,mode=select,filter=filtered_only')
        status = self._io.COM(
            f'sel_change_symbol,symbol={new_symbol},mode=replace'
        )
        self.sel_clear()
        return status

    # ─── 高级：丝印调整 ───
    def silk_adjust(self, layer, dx=0, dy=0, scale=1.0):
        """调整丝印层"""
        self.layer_open(layer)
        self.filter_reset()
        self._io.COM(f'filter_set,feat_type=text;line,min_len=0,layer={layer}')
        self._io.COM(f'filter_area_end,layer=,mode=select,filter=filtered_only')
        results = []
        if dx != 0 or dy != 0:
            results.append({'op': 'move', 'dx': dx, 'dy': dy})
            self.sel_move(dx, dy)
        if scale != 1.0:
            results.append({'op': 'scale', 'factor': scale})
            self.sel_scale(scale)
        self.sel_clear()
        return results

    # ─── 高级：自动拼版 ───
    def panelize(self, x_count=2, y_count=3, border=10, fiducial=True):
        """自动拼版"""
        self.step = 'panel'
        self.create_step('panel')
        self.open_step('panel')

        self._io.COM(
            f'panel_create,x_count={x_count},y_count={y_count},'
            f'border={border},fiducial={"yes" if fiducial else "no"}'
        )
        self._io.COM('panel_place,mode=auto')
        return f'{x_count}x{y_count} 拼版完成'


# ═══════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════

if __name__ == '__main__':
    """
    使用示例：

    # 方式1: Gateway 模式（外部命令行/服务器调用）
    python cam_interface.py --job MY-JOB

    # 方式2: 指定 PID 连接
    python cam_interface.py --pid 5236 --job MY-JOB

    # 方式3: 在 Genesis/InCAMPro 内部运行时自动嵌入式模式
    直接在 CAM 软件的 Script 面板中运行此文件即可
    """

    # 自动检测运行环境
    # Genesis 内部运行时会设置 GENESIS_TMP，外部运行时没有
    is_inside_cam = bool(os.environ.get('GENESIS_TMP') or os.environ.get('INCAM_PRODUCT'))

    if '--pid' in sys.argv:
        # Gateway 模式：指定 PID
        pid_idx = sys.argv.index('--pid')
        pid = int(sys.argv[pid_idx + 1])
        job = sys.argv[sys.argv.index('--job') + 1] if '--job' in sys.argv else None
        print(f'🔗 Gateway 模式 → PID:{pid}')
        cam = CAM(embedded=False, pid=pid, job=job)
        print(f'   用户: {cam.get_user()}')
        print(f'   料号列表: {cam.get_job_list()[:5]}...')

    elif '--job' in sys.argv and not is_inside_cam:
        # Gateway 模式：自动发现 PID
        job = sys.argv[sys.argv.index('--job') + 1]

        # 发现 Genesis/InCAMPro 进程 PID
        gen_pids = []
        try:
            import psutil
            target = 'get.exe' if IS_WINDOWS else 'get'
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    if p.info['name'] and target in p.info['name'].lower():
                        gen_pids.append(p.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            # 不用 psutil，用系统命令
            if IS_WINDOWS:
                out = os.popen('tasklist /FI "IMAGENAME eq get.exe" /FO CSV').read()
                for line in out.split('\n'):
                    parts = line.replace('"','').split(',')
                    if len(parts)>=2 and 'get.exe' in parts[0].lower():
                        try: gen_pids.append(int(parts[1]))
                        except: pass
            else:
                out = os.popen('ps -elf|grep get|grep -v grep').read()
                for line in out.split('\n'):
                    parts = line.split()
                    if len(parts)>=4:
                        try: gen_pids.append(int(parts[3]))
                        except: pass

        if not gen_pids:
            print(f'❌ 未找到运行中的 {CAM_SOFTWARE} 进程')
            print('   请确保 Genesis/InCAMPro 已启动并打开了料号')
            sys.exit(1)

        pid = gen_pids[0]
        print(f'🔗 Gateway 模式 → 自动发现 PID:{pid} (共 {len(gen_pids)} 个进程)')
        cam = CAM(embedded=False, pid=pid, job=job)
        print(f'   用户: {cam.get_user()}')
        print(f'   料号: {job}')

    elif is_inside_cam:
        # 嵌入式模式（在 CAM 内部运行）
        print(f'📌 嵌入式模式 → {CAM_SOFTWARE}')
        cam = CAM(embedded=True)
        print(cam.get_user())

    else:
        print(f'❌ 未检测到 {CAM_SOFTWARE} 环境')
        print('   用法:')
        print('   python cam_interface.py --job JOB-NAME       # Gateway 自动连接')
        print('   python cam_interface.py --pid 5236 --job JOB  # Gateway 指定PID')
        print('   或在 Genesis/InCAMPro 内部直接运行（嵌入式模式）')
        sys.exit(1)
