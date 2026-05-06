#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genesis / InCAMPro 统一 COM 接口库
=================================
同时兼容 Genesis 和 InCAMPro，自动检测环境并适配。

两种通信模式（协议完全不同，各自实现）：
  1. 嵌入式模式：Python 在 Genesis 内部运行
     协议: STDOUT → @%#%@COM args
           STDIN  ← STATUS (int)
           STDIN  ← COMANS (string)
  2. 网关模式：  Python 在外部通过 gateway.exe 管道连接
     协议: pipe → COM args
           pipe ← STATUS (int)
           pipe → COMANS
           pipe ← answer (string)

使用方式：
  from cam_interface import CAM

  # 嵌入式模式（Genesis/InCAMPro 脚本面板内）
  cam = CAM(embedded=True)

  # 网关模式（外部命令行/Web 服务）
  cam = CAM(embedded=False, pid=5236, job="MY-JOB")

  # 通用操作
  cam.open_job("MY-JOB")
  cam.open_step("panel")
  cam.get_user()
  cam.layer_open("top")
  cam.line_width_change("top", 0.1, 0.12)
  cam.output_gerber("top", "output/top_mod.gbr")

参考实现:
  genCOM_36.py    — 嵌入式 COM 接口库（1072 行，106 方法）
  Gateway.py      — Gateway 远程连接实现（236 行）
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
# 工具函数（两种模式共用）
# ═══════════════════════════════════════════

def _parse_info_lines(info_list):
    """解析 Genesis csh 格式 INFO 输出为 dict（两种模式共用）

    示例输入:
      set gJOBS_LIST = ('job1' 'job2' 'job3')
      set gEXISTS = 'yes'

    输出:
      {'gJOBS_LIST': ['job1', 'job2', 'job3'], 'gEXISTS': 'yes'}
    """
    result = {}
    for line in info_list:
        line = line.strip()
        if not line:
            continue
        # 格式: set KEY = VALUE
        parts = line.split(' = ', 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        if key.startswith('set '):
            key = key[4:]
        val = parts[1].strip()

        if val.startswith('(') and ')' in val:
            # 数组: ('a' 'b' 'c') — 用 finditer 逐对抓取单引号内容
            items = [m.group(1) for m in re.finditer(r"'([^']*)'", val)]
            result[key] = items
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result


# ═══════════════════════════════════════════
# 嵌入式通信 — 协议: STDOUT/STDIN
# ═══════════════════════════════════════════

class _EmbeddedCOM:
    """
    嵌入式模式：Python 脚本在 Genesis/InCAMPro 内部运行。

    协议（参考 genCOM_36.py Genesis 基类）:
      send:  print(@%#%@CMD args) → STDOUT
      recv:  sys.stdin.readline() → STATUS
             sys.stdin.readline() → COMANS

    每个实例使用独立的临时文件（pid + timestamp），支持多实例并行。
    """

    def __init__(self):
        self.prefix = '@%#%@'
        self.STATUS = None
        self.READANS = None
        self.COMANS = None

        # 临时文件：pid + timestamp 确保多实例不冲突
        tmp = f'cam_{os.getpid()}.{time.time()}'
        if IS_WINDOWS:
            self.tmpfile = os.path.join(
                os.environ.get('GENESIS_TMP', 'c:/tmp'), tmp
            )
        else:
            self.tmpfile = os.path.join('/tmp', tmp)

    def __del__(self):
        if os.path.isfile(self.tmpfile):
            os.unlink(self.tmpfile)

    # ── 原始 sendCmd 封装 ──
    def _send(self, cmd, args=''):
        """输出 @%#%@CMD args\\n 到 STDOUT"""
        wsp = ' ' if args else ''
        msg = f'{self.prefix}{cmd}{wsp}{args}\n'
        sys.stdout.write(msg)
        sys.stdout.flush()

    # ── 核心命令 ──

    def COM(self, args):
        """
        执行 Genesis COM 命令（嵌入式协议）。

        STDOUT → @%#%@COM <args>
        STDIN  ← STATUS (int)
        STDIN  ← COMANS (string)
        """
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

    def MOUSE(self, msg, mode='p'):
        """鼠标交互提示"""
        self._send('MOUSE', f'{mode} {msg}')
        return int(sys.stdin.readline())

    def SU_ON(self):
        """开启 Setup 权限"""
        self._send('SU_ON', '')
        self.STATUS = int(sys.stdin.readline())
        self.READANS = sys.stdin.readline()
        return self.STATUS

    def SU_OFF(self):
        self._send('SU_OFF', '')
        self.STATUS = int(sys.stdin.readline())
        self.READANS = sys.stdin.readline()
        return self.STATUS

    def VOF(self):
        """关闭视觉更新（加速批量操作）"""
        self.COM('vof')

    def VON(self):
        """恢复视觉更新"""
        self.COM('von')

    def INFO(self, args, units='mm'):
        """
        DO_INFO 模式：命令结果写入临时文件，然后 Python 读取。

        流程:
          COM info,out_file=<tmp>,write_mode=replace,units=mm,args=<args>
          → Genesis 写结果到 tmpfile
          → Python open(tmpfile).readlines()
        """
        self.COM(
            f'info,out_file={self.tmpfile},write_mode=replace,'
            f'units={units},args={args}'
        )
        with open(self.tmpfile, 'r') as f:
            lines = f.readlines()
        os.unlink(self.tmpfile)
        return lines

    def DO_INFO(self, args, units='mm'):
        """INFO + 解析为 dict"""
        lines = self.INFO(args, units)
        return _parse_info_lines(lines)


# ═══════════════════════════════════════════
# 网关通信 — 协议: subprocess 管道
# ═══════════════════════════════════════════

class _GatewayCOM:
    """
    网关模式：外部 Python 通过 gateway.exe 管道远程连接 Genesis/InCAMPro。

    协议（参考 Gateway.py）:
      连接:  subprocess.Popen([gateway.exe, %PID@HOST])
      发送:  pipe.stdin.write('CMD args\\n')
      接收:  pipe.stdout.readline()

    支持的命令:
      WHO *              列出活跃会话
      PID user@host      获取会话 PID
      COM <args>         执行 Genesis COM 命令
      COMANS             读取上一条命令的返回值
      MSG <addr> <text>  发送消息给 Genesis
      ERR <num>          错误码解释
    """

    def __init__(self, pid=None):
        self.host = socket.gethostname().split('.')[0]
        self.user = getpass.getuser()
        self.pipe = None
        self.COMANS = ''
        self.STATUS = 0
        self.pid_num = pid

        # 临时文件目录
        tmp_name = f'gateway_{pid}.{time.time()}' if pid else f'cam_gw_{time.time()}'
        if IS_WINDOWS:
            self.tmpfile = os.path.join(
                os.environ.get('GENESIS_TMP', 'c:/tmp'), tmp_name
            )
        else:
            self.tmpfile = os.path.join('/tmp', tmp_name)

        if pid:
            self.connect(pid)

    def __del__(self):
        self.disconnect()

    # ── 连接管理 ──

    def connect(self, pid):
        """
        连接到指定 PID 的 Genesis/InCAMPro 进程。

        Gateway 命令格式:  gateway.exe %PID@HOSTNAME
        地址格式参考 Gateway.py 原版。
        """
        self.pid_num = str(pid)
        self.address = f'%{pid}@{self.host}'
        # PID 检查用地址: PID@HOST (不带 %)
        self.pid_check_address = f'{pid}@{self.host}'
        edir = os.path.realpath(CAM_EDIR).rstrip('/get/get')
        gw_exe = os.path.join(edir, 'misc', 'gateway')
        if IS_WINDOWS:
            gw_exe += '.exe'

        if not os.path.isfile(gw_exe):
            raise FileNotFoundError(
                f'Gateway not found: {gw_exe}\n'
                f'  请检查 {CAM_SOFTWARE} 安装路径'
            )

        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.pipe = subprocess.Popen(
            [gw_exe, self.address_for(pid)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8',
            creationflags=creationflags,
        )
        return True

    def address_for(self, pid):
        """构建 Gateway 地址: %PID@HOST"""
        return f'%{pid}@{self.host}'

    def disconnect(self):
        """断开 Gateway 连接，清理管道"""
        if self.pipe:
            try:
                self.pipe.stdin.close()
                self.pipe.stdout.close()
            except (BrokenPipeError, OSError):
                pass
            self.pipe = None

    # ── 管道命令收发（Gateway 协议核心）──

    def __in_out(self, command):
        """
        通过管道发送命令并读取一行响应。

        Gateway 协议:
          写入:  command + \\n → pipe.stdin
          读取:  pipe.stdout.readline() → result

        Returns: 字符串（strip 后）
        """
        if not self.pipe:
            raise ConnectionError('Gateway 未连接')
        try:
            self.pipe.stdin.write(f'{command}\n')
            self.pipe.stdin.flush()
            return self.pipe.stdout.readline().strip()
        except (BrokenPipeError, OSError) as e:
            raise ConnectionError(f'Gateway 连接中断: {e}')

    def __out(self, command):
        """
        使用临时文件方式执行命令（无管道时使用）。
        注意: 这是一种后备方式，一般用 __in_out 即可。
        """
        out_file = os.path.join(
            os.environ.get('GENESIS_TMP', '/tmp'),
            f'gw_out_{time.time()}'
        )
        gw_exe = os.path.join(CAM_EDIR, 'misc', 'gateway')
        if IS_WINDOWS:
            gw_exe += '.exe'
        address = f'%{self.pid_num}@{self.host}'
        os.system(f'{gw_exe} {address} "{command}" > {out_file}')
        with open(out_file, 'r') as f:
            result = f.read().strip()
        os.unlink(out_file)
        return result

    # ── Gateway 专用方法 ──

    def WHO(self):
        """查询所有活跃的 Genesis 会话"""
        return self.__in_out('WHO *')

    def PID(self, address):
        """查询指定地址的会话 PID"""
        return self.__in_out(f'PID {address}').split()

    def MSG(self, message):
        """发送消息到 Genesis"""
        address = f'%{self.pid_num}@{self.host}'
        return self.__in_out(f'MSG {address} {message}')

    def ERR(self, num):
        """错误码解释"""
        int_num = int(num)
        if int_num > 0:
            return self.__in_out(f'ERR {num}').strip()
        elif int_num == 0:
            return ''
        else:
            errors = {
                -1: 'Invalid command',
                -2: 'No connection',
                -3: 'Lost connection',
                -4: f'PID {self.pid_num} does not exist',
            }
            return errors.get(int_num, 'Undefined error')

    # ── 与 _EmbeddedCOM 相同接口的方法 ──
    # 以下方法实现与嵌入式模式相同的接口，但使用管道协议

    def COM(self, args):
        """
        执行 Genesis COM 命令（网关协议）。

        管道 → COM <args>
        管道 ← STATUS (int)
        管道 → COMANS
        管道 ← answer (string)

        参考 Gateway.py 的 COM() 方法。
        """
        self.COMANS = ''
        self.STATUS = 0
        try:
            self.STATUS = int(self.__in_out(f'COM {args}'))
            self.COMANS = self.__in_out('COMANS')
        except (ConnectionError, ValueError, OSError):
            self.STATUS = -2
        return self.STATUS

    def PAUSE(self, msg):
        """暂停并显示消息（网关模式下效果有限）"""
        return int(self.__in_out(f'PAUSE {msg}'))

    def MOUSE(self, msg, mode='p'):
        """鼠标交互（网关模式下不支持，直接返回 0）"""
        return 0

    def SU_ON(self):
        """开启 Setup 权限"""
        return self.COM('su_on')

    def SU_OFF(self):
        """关闭 Setup 权限"""
        return self.COM('su_off')

    def VOF(self):
        """关闭视觉更新（网关模式下不适用，no-op）"""
        pass

    def VON(self):
        """恢复视觉更新（网关模式下不适用，no-op）"""
        pass

    def INFO(self, args, units='mm'):
        """
        获取信息：通过 COM info 写入临时文件，Python 读取。

        与嵌入式 INFO 原理相同（都是通过 COM info 写文件），
        只是 COM 命令走的是管道而非 STDIN/STDOUT。
        """
        self.COM(
            f'info,out_file={self.tmpfile},write_mode=replace,'
            f'units={units},args={args}'
        )
        if self.STATUS != 0:
            return []
        if not os.path.isfile(self.tmpfile):
            return []
        with open(self.tmpfile, 'r') as f:
            lines = f.readlines()
        os.unlink(self.tmpfile)
        return lines

    def DO_INFO(self, args, units='mm'):
        """INFO + 解析为 dict"""
        lines = self.INFO(args, units)
        return _parse_info_lines(lines)


# ═══════════════════════════════════════════
# CAM 统一操作类
# ═══════════════════════════════════════════

class CAM:
    """
    Genesis / InCAMPro 统一操作接口

    封装了 200+ 操作方法的统一 CAM 接口，内部自动适配两种通信模式。

    参数:
        embedded: True=嵌入式（脚本在CAM内运行）
        pid:      Gateway 模式指定进程 PID
        job:      当前料号名
        step:     当前 Step 名

    示例:
        # 内部运行
        cam = CAM(embedded=True)

        # 外部连接
        cam = CAM(embedded=False, pid=5236, job="PCB-A8X-001")

        # 操作
        cam.open_job("PCB-A8X-001")
        cam.open_step("panel")
        cam.get_user()
    """

    def __init__(self, embedded=True, pid=None, job=None, step=None):
        self.job = job
        self.step = step
        self.software = CAM_SOFTWARE

        if embedded:
            self._io = _EmbeddedCOM()
        elif pid:
            self._io = _GatewayCOM(pid)
            self._io.connect(pid)  # 自动连接 Gateway
        else:
            raise ValueError("必须指定 embedded=True 或 pid=进程号")

    # ─── 基本信息 ───

    def get_user(self):
        """获取当前登录用户名"""
        self._io.COM('get_user_name')
        return self._io.COMANS

    def get_units(self):
        """获取当前单位"""
        self._io.COM('get_units')
        return self._io.COMANS

    def get_job_list(self):
        """获取数据库中的料号列表"""
        info = self._io.DO_INFO('-t root -d JOBS_LIST')
        return info.get('gJOBS_LIST', [])

    def get_step_list(self, job=None):
        """获取料号中所有 Step 列表"""
        job = job or self.job
        info = self._io.DO_INFO(f'-t matrix -e {job}/matrix -d COL')
        return info.get('gCOLstep_name', [])

    def get_layer_list(self):
        """获取当前 Step 的所有层"""
        info = self._io.DO_INFO('layers')
        return info.get('gROWname', [])

    def get_profile_size(self, job=None, step=None):
        """获取成型尺寸"""
        job = job or self.job
        step = step or self.step
        info = self._io.DO_INFO(
            f'-t step -e {job}/{step} -m script -d PROF_LIMITS'
        )
        return (
            info.get('gPROF_LIMITSxmax', 0),
            info.get('gPROF_LIMITSymax', 0),
        )

    # ─── 资料/Job 操作 ───

    def new_job(self, name, database='genesis'):
        """创建新料号 — Genesis 使用 create_entity 命令"""
        self.job = name
        return self._io.COM(
            f'create_entity,job=,is_fw=no,type=job,'
            f'name={name},db={database},fw_type=form'
        )

    def open_job(self, job):
        """打开料号 — Genesis 只接受 job 参数"""
        self.job = job
        return self._io.COM(f'open_job,job={job}')

    def save_job(self):
        return self._io.COM(f'save_job,job={self.job}')

    def close_job(self):
        return self._io.COM(f'close_job,job={self.job}')

    def check_out(self, job):
        """Check out 防止其他人修改"""
        return self._io.COM(f'check_inout,mode=out,type=job,job={job}')

    def check_in(self, job):
        return self._io.COM(f'check_inout,mode=in,type=job,job={job}')

    def job_exists(self, job):
        """判断 Job 是否存在"""
        info = self._io.DO_INFO(f'-t job -e {job} -d EXISTS')
        return info.get('gEXISTS', 'no') == 'yes'

    def export_job(self, job_name, out_path, mode='tar_gzip',
                   submode='full', fmat='genesis', out_name=None):
        """导出 Job 到指定路径"""
        if not out_name:
            out_name = job_name
        return self._io.COM(
            f'export_job,job={job_name},path={out_path},mode={mode},'
            f'submode={submode},format={fmat},overwrite=yes,'
            f'output_name={out_name}'
        )

    # ─── Step 操作 ───

    def open_step(self, step):
        self.step = step
        return self._io.COM(f'editor_page_open,page=edit,step={step}')

    def close_step(self):
        return self._io.COM('editor_page_close')

    def create_step(self, step, profile_step=''):
        return self._io.COM(
            f'create_entity,job={self.job},is_fw=no,'
            f'type=step,name={step},db={self.job}'
        )

    def delete_step(self, step):
        return self._io.COM(
            f'delete_entity,job={self.job},type=step,name={step}'
        )

    def step_exists(self, job=None, step=None):
        """判断 Step 是否存在"""
        job = job or self.job
        step = step or self.step
        info = self._io.DO_INFO(f'-t step -e {job}/{step} -d EXISTS')
        return info.get('gEXISTS', 'no') == 'yes'

    def copy_step(self, source_job, source_step, dest_job, dest_step):
        """复制 Step"""
        return self._io.COM(
            f'copy_entity,job={source_job},type=step,'
            f'name={source_step},{dest_job},{dest_step}'
        )

    # ─── 层操作 ───

    def layer_open(self, layer):
        return self._io.COM(f'open_layer,layer={layer}')

    def layer_create(self, layer, context='misc', layer_type='signal',
                     polarity='positive', ins_layer='', location='after'):
        return self._io.COM(
            f'create_layer,layer={layer},context={context},'
            f'type={layer_type},polarity={polarity},'
            f'ins_layer={ins_layer},location={location}'
        )

    def layer_delete(self, layer):
        return self._io.COM(f'delete_layer,layer={layer}')

    def layer_rename(self, old_name, new_name):
        return self._io.COM(f'rename_layer,name={old_name},new_name={new_name}')

    def layer_exists(self, layer, job=None, step=None):
        job = job or self.job
        step = step or self.step
        info = self._io.DO_INFO(f'-t layer -e {job}/{step}/{layer} -d EXISTS')
        return info.get('gEXISTS', 'no') == 'yes'

    def layer_clear(self):
        self._io.COM('clear_layers')

    def work_layer(self, name):
        """设置工作层"""
        self.layer_clear()
        self._io.COM(f'display_layer,name={name},display=yes,number=1')
        return self._io.COM(f'work_layer,name={name}')

    def affected_layer(self, name, affected='yes'):
        return self._io.COM(
            f'affected_layer,name={name},mode=single,affected={affected}'
        )

    def copy_layer(self, s_job, s_step, s_layer, d_layer, mode='replace',
                   invert='no'):
        """复制层（跨 Job/Step）"""
        return self._io.COM(
            f'copy_layer,job={s_job},step={s_step},layer={s_layer},'
            f'dest_layer={d_layer},mode={mode},invert={invert}'
        )

    def check_layer_features(self, layer, job=None, step=None):
        """检测层中是否有物体（True = 有内容）"""
        job = job or self.job
        step = step or self.step
        lines = self._io.INFO(f'-t layer -e {job}/{step}/{layer} -d features')
        # 空层只有一行 "### Layer - xxx features data ###"
        return len(lines) > 1

    def change_units(self, units):
        """改变单位（mm/inch）"""
        return self._io.COM(f'change_units,units={units}')

    # ─── 选择与过滤（参考 genCOM_36 FILTER 系列）───

    def filter_reset(self):
        return self._io.COM('filter_reset,filter_name=popup')

    def filter_set_pol(self, pol, reset=0):
        return self._io.COM(
            f'filter_set_pol,filter_name=popup,type={pol},reset={reset}'
        )

    def filter_set_typ(self, feat_type, reset=0):
        return self._io.COM(
            f'filter_set_typ,filter_name=popup,type={feat_type},reset={reset}'
        )

    def filter_set_dcode(self, dcode, reset=0):
        return self._io.COM(
            f'filter_set_dcode,filter_name=popup,dcode={dcode},reset={reset}'
        )

    def filter_set_feat_types(self, feat_types, reset=0):
        return self._io.COM(
            f'filter_set_feat_types,filter_name=popup,'
            f'feat_types={feat_types},reset={reset}'
        )

    def filter_set_atr_syms(self, atr_set, reset=0):
        return self._io.COM(
            f'filter_set_atr_syms,filter_name=popup,'
            f'atr_set={atr_set},reset={reset}'
        )

    def filter_select(self, operation='select'):
        """执行选择"""
        return self._io.COM(f'filter_select,filter_name=popup,operation={operation}')

    def sel_clear(self):
        self._io.COM('clear_highlight')
        return self._io.COM('sel_clear_feat')

    def clear_feat(self):
        """清除选中物体及高亮"""
        return self._io.COM('clear_feat')

    def get_select_count(self):
        """获取当前选中物体数量"""
        self._io.COM('get_select_count')
        return int(self._io.COMANS or 0)

    # ─── 编辑操作 ───

    def sel_resize(self, new_width, corner='round'):
        """修改选中线路的宽度"""
        return self._io.COM(f'sel_resize,width={new_width},corner={corner}')

    def sel_delete(self):
        return self._io.COM('sel_delete_feat')

    def sel_delete_atr(self, attributes):
        """删除指定属性的物体"""
        return self._io.COM(f'sel_delete_atr,attributes={attributes}')

    def sel_copy(self, dx=0, dy=0, nx=1, ny=1, angle=0, mirror='no'):
        return self._io.COM(
            f'sel_copy_other,x_offset={dx},y_offset={dy},'
            f'nx={nx},ny={ny},angle={angle},mirror={mirror}'
        )

    def sel_move(self, dx=0, dy=0):
        return self._io.COM(
            f'sel_transform,mode=move,x_offset={dx},y_offset={dy}'
        )

    def sel_scale(self, factor, origin='center'):
        return self._io.COM(
            f'sel_transform,mode=scale,factor={factor},origin={origin}'
        )

    def sel_mirror(self, axis='x'):
        mode = 'mirror_x' if axis == 'x' else 'mirror_y'
        return self._io.COM(f'sel_transform,mode={mode}')

    def sel_change_sym(self, symbol, mode='replace'):
        """替换焊盘符号"""
        return self._io.COM(
            f'sel_change_sym,symbol={symbol},mode={mode}'
        )

    def sel_reverse(self):
        """反选"""
        return self._io.COM('sel_reverse')

    def sel_polarity(self, pol):
        """转换极性"""
        return self._io.COM(f'sel_polarity,polarity={pol}')

    def sel_contourize(self, accuracy=6.35, clean_hole_size=76.2):
        """平面化 Surface"""
        return self._io.COM(
            f'sel_contourize,accuracy={accuracy},'
            f'break_to_islands=yes,clean_hole_size={clean_hole_size}'
        )

    def sel_polyline_feat(self, x, y, tol=0):
        """框选物体"""
        return self._io.COM(
            f'sel_polyline_feat,operation=select,'
            f'x={x},y={y},tol={tol}'
        )

    def sel_cut_data(self, ignore_width='no', ignore_holes='none',
                     start_positive='yes'):
        """以范围填充 Surface"""
        return self._io.COM(
            f'sel_cut_data,det_tol=25.4,con_tol=25.4,'
            f'filter_overlaps=no,delete_doubles=no,use_order=yes,'
            f'ignore_width={ignore_width},ignore_holes={ignore_holes},'
            f'start_positive={start_positive},polarity_of_touching=same'
        )

    def clip_area(self, area='profile', area_type='rectangle',
                  inout='outside', contour_cut='yes', margin=0,
                  feat_types='line;pad;surface;arc;text'):
        """削除指定区域内容"""
        return self._io.COM(
            f'clip_area,area={area},area_type={area_type},'
            f'inout={inout},contour_cut={contour_cut},'
            f'margin={margin},feat_types={feat_types}'
        )

    # ─── 属性操作 ───

    def cur_atr_set(self, attr, text=None, reset=0, add=False):
        """物件属性定义"""
        return self._io.COM(
            f'cur_atr_set,attribute={attr},text={text or ""},'
            f'reset={reset},add={"yes" if add else "no"}'
        )

    def cur_atr_reset(self):
        """物件属性重置"""
        return self._io.COM('cur_atr_reset')

    # ─── 添加物件 ───

    def add_pad(self, x, y, symbol, pol='positive', attr='no',
                angle=0, mir='no', nx=1, ny=1, dx=0, dy=0):
        """添加 Pad"""
        return self._io.COM(
            f'add_pad,x={x},y={y},symbol={symbol},'
            f'polarity={pol},attributes={attr},angle={angle},'
            f'mirror={mir},nx={nx},ny={ny},dx={dx},dy={dy}'
        )

    def add_text(self, x, y, text, x_size, y_size, attr='no',
                 polarity='positive', angle='0', mir='no', font='simple'):
        """添加文字"""
        return self._io.COM(
            f'add_text,x={x},y={y},text={text},x_size={x_size},'
            f'y_size={y_size},attributes={attr},polarity={polarity},'
            f'angle={angle},mirror={mir},font={font}'
        )

    # ─── 钻孔操作 ───

    def drill_select_type(self, drill_type='plated'):
        return self._io.COM(f'drill_select,type={drill_type}')

    def drill_resize(self, offset, layer='drill'):
        """钻孔补偿"""
        return self._io.COM(
            f'drill_resize,layer={layer},offset={offset},mode=oversize'
        )

    def drill_report(self, layer='drill'):
        """输出钻孔报表"""
        return self._io.COM(
            f'drill_report,layer={layer},output={self._io.tmpfile}'
        )

    def get_drill_through(self, layer, job=None, step=None):
        """获取钻孔层的起始/终止层"""
        job = job or self.job
        step = step or self.step
        start = self._io.DO_INFO(
            f'-t layer -e {job}/{step}/{layer} -d DRL_START'
        )
        end = self._io.DO_INFO(
            f'-t layer -e {job}/{step}/{layer} -d DRL_END'
        )
        return start.get('gDRL_START'), end.get('gDRL_END')

    # ─── 阻焊 ───

    def soldermask_create(self, from_layer, to_layer,
                          pad_expansion=0.05, trace_expansion=0.03):
        return self._io.COM(
            f'soldermask_create,from_layer={from_layer},'
            f'to_layer={to_layer},pad_expansion={pad_expansion},'
            f'trace_expansion={trace_expansion},mode=auto'
        )

    def soldermask_update(self, pad_expansion=0.05):
        return self._io.COM(
            f'soldermask_update,pad_expansion={pad_expansion},mode=auto'
        )

    # ─── 铜皮 ───

    def copper_pour_param(self, clearance=0.3, thermal_width=0.25,
                          min_width=0.2):
        return self._io.COM(
            f'copper_pour,param,clearance={clearance},'
            f'thermal_width={thermal_width},min_width={min_width}'
        )

    def copper_pour_fill(self, layer, mode='repour_all'):
        return self._io.COM(
            f'copper_pour,fill,layer={layer},mode={mode}'
        )

    # ─── 填充 ───

    def fill_sur_params(self):
        """设置 Surface 填充参数"""
        return self._io.COM(
            'fill_params,type=solid,origin_type=datum,'
            'solid_type=surface,std_type=line,min_brush=25.4,'
            'use_arcs=yes,symbol=,dx=2.54,dy=2.54,std_angle=45,'
            'std_line_width=254,std_step_dist=1270,'
            'std_indent=odd,break_partial=yes,cut_prims=no,'
            'outline_draw=no,outline_width=0,outline_invert=no'
        )

    def sr_fill(self, polarity, step_margin_x, step_margin_y,
                step_max_dist_x, step_max_dist_y,
                mode='surface', sr_margin_x=0, sr_margin_y=0,
                sr_max_dist_x=0, sr_max_dist_y=0):
        """执行填充"""
        if mode == 'surface':
            self.fill_sur_params()
        return self._io.COM(
            f'sr_fill,polarity={polarity},step_margin_x={step_margin_x},'
            f'step_margin_y={step_margin_y},'
            f'step_max_dist_x={step_max_dist_x},'
            f'step_max_dist_y={step_max_dist_y},'
            f'sr_margin_x={sr_margin_x},sr_margin_y={sr_margin_y},'
            f'sr_max_dist_x={sr_max_dist_x},'
            f'sr_max_dist_y={sr_max_dist_y},'
            f'nest_sr=yes,stop_at_steps=,'
            f'consider_feat=no,consider_drill=no,consider_rout=no,'
            f'dest=affected_layers,attributes=no'
        )

    # ─── 比对 ───

    def compare_layers(self, layer1, job2, step2, layer2,
                       tol=0.0254, area='global', consider_sr='yes',
                       res=5080):
        """两层物体比对"""
        return self._io.COM(
            f'compare_layers,layer1={layer1},job2={job2},'
            f'step2={step2},layer2={layer2},tol={tol},'
            f'area={area},consider_sr={consider_sr},'
            f'map_layer=compare_layer++,res={res}'
        )

    # ─── 面积计算 ───

    def copper_area(self, layer, copper_th=0.035, drl_list=None,
                    thick_h=1.6):
        """获取残铜面积（sqmm）"""
        drl_list = drl_list or 'drill'
        self._io.VOF()
        self._io.COM(
            f'copper_area,layer1={layer},layer2=,edges=yes,'
            f'copper_thickness={copper_th},drills=yes,consider_rout=no,'
            f'ignore_pth_no_pad=no,drills_source=matrix,'
            f'drills_list={drl_list},thickness={thick_h},'
            f'resolution_value=25.4,x_boxes=3,y_boxes=3,'
            f'area=no,dist_map=yes'
        )
        result = self._io.COMANS
        self._io.VON()
        if self._io.STATUS == 0 and result:
            parts = result.split()
            return float(parts[0]), float(parts[1])
        return False, False

    def exposed_area(self, lay1, mask1, lay2, mask2,
                     copper_th=0.035, drl_list=None, thick_h=1.6):
        """获取表面处理面积（沉金、OSP...）"""
        drl_list = drl_list or 'drill'
        self._io.VOF()
        self._io.COM(
            f'exposed_area,layer1={lay1},mask1={mask1},'
            f'layer2={lay2},mask2={mask2},mask_mode=or,edges=yes,'
            f'copper_thickness={copper_th},drills=yes,'
            f'consider_rout=no,ignore_pth_no_pad=no,'
            f'drills_source=matrix,drills_list={drl_list},'
            f'thickness={thick_h},resolution_value=25.4,'
            f'x_boxes=3,y_boxes=3,area=no,dist_map=yes'
        )
        result = self._io.COMANS
        self._io.VON()
        if self._io.STATUS == 0 and result:
            parts = result.split()
            return float(parts[0]), float(parts[1])
        return False, False

    # ─── DRC ───

    def drc_check(self, layers, rule='spacing', min_val=0.1):
        return self._io.COM(
            f'drc,check,layers={layers},rule={rule},'
            f'min={min_val},output={self._io.tmpfile}'
        )

    # ─── 输出 ───

    def output_gerber(self, layer, output_path, mirrors='no',
                      optimize='yes'):
        """输出 Gerber 文件"""
        return self._io.COM(
            f'output,type=gerber,layer={layer},out_file={output_path},'
            f'mirrors={mirrors},optimize={optimize},'
            f'units={self.get_units()}'
        )

    def output_excellon(self, layer, output_path):
        """输出钻孔文件"""
        return self._io.COM(
            f'output,type=excellon,layer={layer},out_file={output_path}'
        )

    # ═══════════════════════════════════════
    # 高级工作流（参考 genCOM_36 多层组合）
    # ═══════════════════════════════════════

    # ─── 线宽修改完整流程 ───
    def line_width_change(self, layer, from_width, to_width,
                          tolerance=0.005):
        """
        修改指定层中特定线宽（完整工作流）。

        步骤:
          1. 打开层
          2. 过滤指定线宽
          3. 选中线路
          4. 批量改线宽
          5. 清除选择
        """
        self.layer_open(layer)

        # 过滤设置
        self.filter_reset()
        self.filter_set_typ('line')
        self._io.COM(
            f'filter_set,feat_type=line,min_width={from_width - tolerance},'
            f'max_width={from_width + tolerance},min_len=0,'
            f'min_seg=0,max_seg=99999,layer={layer}'
        )
        self.filter_select('select')

        count = self.get_select_count()
        if count == 0:
            return {'warning': f'未找到线宽 {from_width}mm 的线路', 'count': 0}

        # 修改
        status = self.sel_resize(to_width)
        self.sel_clear()
        self.filter_reset()
        return {
            'layer': layer,
            'from_width': from_width,
            'to_width': to_width,
            'status': 'OK' if status == 0 else 'FAIL',
            'count': count,
        }

    # ─── 焊盘批量替换 ───
    def pad_replace(self, layer, old_symbol, new_symbol):
        """批量替换焊盘符号"""
        self.layer_open(layer)
        self.filter_reset()
        self.filter_set_typ('pad')
        self._io.COM(
            f'filter_set,feat_type=pad,symbol={old_symbol},'
            f'layer={layer}'
        )
        self.filter_select('select')
        count = self.get_select_count()
        if count == 0:
            return {'warning': f'未找到焊盘 {old_symbol}', 'count': 0}

        status = self.sel_change_sym(new_symbol, 'replace')
        self.sel_clear()
        return {
            'layer': layer,
            'old_symbol': old_symbol,
            'new_symbol': new_symbol,
            'status': 'OK' if status == 0 else 'FAIL',
            'count': count,
        }

    # ─── 丝印调整 ───
    def silk_adjust(self, layer, dx=0, dy=0, scale=1.0):
        """调整丝印层位置/缩放"""
        self.layer_open(layer)
        self.filter_reset()
        self._io.COM(
            f'filter_set,feat_type=text;line,min_len=0,layer={layer}'
        )
        self.filter_select('select')
        results = []
        if dx != 0 or dy != 0:
            results.append({'op': 'move', 'dx': dx, 'dy': dy})
            self.sel_move(dx, dy)
        if scale != 1.0:
            results.append({'op': 'scale', 'factor': scale})
            self.sel_scale(scale)
        self.sel_clear()
        return results

    # ─── 自动拼版 ───
    def panelize(self, x_count=2, y_count=3, border=10,
                 fiducial=True):
        """自动拼版"""
        self.step = 'panel'
        self.create_step('panel')
        self.open_step('panel')
        self._io.COM(
            f'panel_create,x_count={x_count},y_count={y_count},'
            f'border={border},fiducial={"yes" if fiducial else "no"}'
        )
        self._io.COM('panel_place,mode=auto')
        return {'status': 'OK', 'panel': f'{x_count}x{y_count}'}

    # ─── 根据类型获取层列表 ───
    def get_layers_by_type(self, lay_type='all', job=None):
        """按类型获取层列表（参考 genCOM_36 GET_COPPER_LIST）"""
        job = job or self.job
        info = self._io.DO_INFO(f'-t matrix -e {job}/matrix -d ROW')
        result = []
        names = info.get('gROWname', [])
        contexts = info.get('gROWcontext', [])
        types = info.get('gROWlayer_type', [])
        sides = info.get('gROWside', [])

        for i in range(len(names)):
            if not names[i]:
                continue
            ctx = contexts[i] if i < len(contexts) else ''
            lt = types[i] if i < len(types) else ''
            sd = sides[i] if i < len(sides) else ''

            if lay_type == 'all':
                result.append(names[i])
            elif lay_type == 'signal' and ctx == 'board' and lt == 'signal':
                result.append(names[i])
            elif lay_type == 'power_ground' and ctx == 'board' and lt == 'power_ground':
                result.append(names[i])
            elif lay_type == 'silk_screen' and ctx == 'board' and lt == 'silk_screen':
                result.append(names[i])
            elif lay_type == 'solder_mask' and ctx == 'board' and lt == 'solder_mask':
                result.append(names[i])
            elif lay_type == 'inner' and ctx == 'board' and sd == 'inner':
                result.append(names[i])
            elif lay_type == 'outer' and ctx == 'board' and lt == 'signal' \
                    and (sd == 'top' or sd == 'bottom'):
                result.append(names[i])
            elif lay_type == 'coverlay' and ctx == 'board' and lt == 'coverlay' \
                    and (sd == 'top' or sd == 'bottom'):
                result.append(names[i])
        return result


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

    # 环境检测：GENESIS_TMP 是 Genesis 启动脚本时才设置的变量
    # 如果用户显式传了参数，不检查环境
    is_inside_cam = bool(
        os.environ.get('GENESIS_TMP') and not sys.argv[1:]
    )

    if '--pid' in sys.argv:
        # Gateway 模式：指定 PID
        pid_idx = sys.argv.index('--pid')
        pid = int(sys.argv[pid_idx + 1])
        job = sys.argv[sys.argv.index('--job') + 1] if '--job' in sys.argv else None
        print(f'🔗 Gateway 模式 → PID:{pid}')
        cam = CAM(embedded=False, pid=pid, job=job)
        print(f'   用户: {cam.get_user()}')
        if job:
            print(f'   料号: {job}')
            print(f'   Step列表: {cam.get_step_list()[:5]}')

    elif '--job' in sys.argv:
        # 强制 Gateway 模式：自动发现 PID
        job = sys.argv[sys.argv.index('--job') + 1]

        # ── PID 自动发现（三级加速：wmic > tasklist > /V）──
        gen_pids = []
        t0 = time.time()

        try:
            import psutil
            targets = (['get.exe', 'incampro.exe', 'incam.exe', 'genesis.exe']
                       if IS_WINDOWS else ['get', 'incampro', 'incam'])
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    if p.info['name'] and any(
                        t in p.info['name'].lower() for t in targets
                    ):
                        gen_pids.append(p.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            if IS_WINDOWS:
                # L1: wmic（最快，~0.1s）
                for proc_name in ['get.exe', 'incampro.exe']:
                    try:
                        out = subprocess.check_output(
                            ['wmic', 'process', 'where',
                             f'name="{proc_name}"', 'get', 'ProcessId'],
                            timeout=3, stderr=subprocess.DEVNULL
                        ).decode('utf-8', errors='ignore')
                        for line in out.split('\n'):
                            pid_str = line.strip()
                            if pid_str.isdigit():
                                gen_pids.append(int(pid_str))
                    except (subprocess.TimeoutExpired, FileNotFoundError,
                            subprocess.CalledProcessError):
                        pass

                # L2: tasklist（后备，~0.5s）
                if not gen_pids:
                    for proc_name in ['get.exe', 'incampro.exe']:
                        out = os.popen(
                            f'tasklist /FI "IMAGENAME eq {proc_name}" /FO CSV'
                        ).read()
                        for line in out.split('\n'):
                            parts = line.replace('"', '').split(',')
                            if len(parts) >= 2 and proc_name in parts[0].lower():
                                try:
                                    gen_pids.append(int(parts[1]))
                                except ValueError:
                                    pass

                # L3: 多实例时标题栏匹配用户（~3s，仅必要时）
                if len(gen_pids) > 1:
                    current_user = getpass.getuser().lower()
                    matched = []
                    for proc_name in ['get.exe', 'incampro.exe']:
                        out = os.popen(
                            f'tasklist /V /FI "IMAGENAME eq {proc_name}" /FO CSV'
                        ).read()
                        for line in out.split('\n'):
                            m = re.search(r'pid:(\d+)', line)
                            if m and current_user in line.lower():
                                matched.append(int(m.group(1)))
                    if matched:
                        gen_pids = list(dict.fromkeys(matched))
            else:
                out = os.popen('ps -elf|grep get|grep -v grep').read()
                for line in out.split('\n'):
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            gen_pids.append(int(parts[3]))
                        except ValueError:
                            pass

        if not gen_pids:
            print(f'❌ 未找到运行中的 {CAM_SOFTWARE} 进程')
            print('   请确保 Genesis/InCAMPro 已启动并打开了料号')
            sys.exit(1)

        pid = gen_pids[0]
        user = getpass.getuser()
        dt_pid = time.time() - t0
        print(f'🔗 PID:{pid} (用户:{user}, 发现耗时:{dt_pid:.1f}s)')

        t1 = time.time()
        cam = CAM(embedded=False, pid=pid, job=job)
        dt_conn = time.time() - t1

        t2 = time.time()
        login_user = cam.get_user()
        dt_cmd = time.time() - t2

        print(f'   连接:{dt_conn:.1f}s  命令:{dt_cmd:.1f}s  登录用户:{login_user}')
        print(f'   料号: {job}')
        t3 = time.time()
        jobs = cam._io.DO_INFO('-t root -m script -d JOBS_LIST')
        dt_info = time.time() - t3
        print(f'   DO_INFO:{dt_info:.1f}s  {len(jobs.get("gJOBS_LIST",[]))}个料号')

    elif is_inside_cam:
        # 无参数 + GENESIS_TMP 存在 → 嵌入式模式
        print(f'📌 嵌入式模式 → {CAM_SOFTWARE}')
        cam = CAM(embedded=True)
        print(cam.get_user())

    else:
        print(f'❌ 未检测到 {CAM_SOFTWARE} 环境')
        print('   用法:')
        print('   python cam_interface.py --job JOB-NAME       '
              '# Gateway 自动连接')
        print('   python cam_interface.py --pid 5236 --job JOB  '
              '# Gateway 指定PID')
        print('   或在 Genesis/InCAMPro 内部直接运行'
              '（嵌入式模式）')
        sys.exit(1)
