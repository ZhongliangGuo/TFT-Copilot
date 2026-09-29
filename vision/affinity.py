#!/usr/bin/env python3
"""CPU 亲和性与异构核心（Intel 大小核 / 混合架构）自动调度优化模块。

背景与原理:
  在搭载混合架构（如 Intel 12/13/14 代 Alder Lake、Raptor Lake、Arrow Lake 及 Core Ultra）的现代 CPU 上，
  处理器同时包含性能大核（P-Core）与能效小核（E-Core）。
  当 Python 进程作为控制台或后台子服务启动时，Windows 调度器可能会将其计算线程压制/分配到小核上运行；
  同时，ONNX Runtime 和 OpenMP 等推理引擎默认会创建覆盖全部逻辑核的并行线程池。
  在大核与小核协同计算矩阵乘法时，大核算完后必须原地阻塞等待慢 4~5 倍的小核（Straggler 效应），
  导致端到端视觉推理耗时从正常的 ~3.2s 严重劣化至 18s+。

本模块通过 Win32 API 动态枚举系统处理器的物理核心及其能效等级 (EfficiencyClass):
  - 动态识别 P-Core 掩码（支持不同大核数量配置，如 6P+4E、8P+8E、8P+16E 等）；
  - 对同构处理器（如常规 AMD Ryzen 或传统 Intel 平台，所有核心能效等级相同）自动保持全核调度，绝不误杀；
  - 跨平台安全保障：在 Linux / macOS 或 API 调用异常时安全降级，绝不影响服务正常启动。
"""
import ctypes
import os
import sys

_applied = False


def get_hybrid_cpu_affinity() -> tuple[int | None, dict]:
    """探测当前系统是否为异构混合架构（大小核），若为大小核则返回 P-Core 逻辑掩码。

    Returns:
        (mask, info): mask 为适配 P-Core 的整型位掩码（若无需限制或同构 CPU 则为 None）；
                      info 为包含诊断信息的字典。
    """
    info = {
        "platform": sys.platform,
        "is_hybrid": False,
        "total_physical_cores": 0,
        "pcore_count": 0,
        "ecore_count": 0,
        "applied_mask": None,
        "reason": "unsupported_platform",
    }

    if sys.platform != "win32":
        return None, info

    try:
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        RelationProcessorCore = 0  # 关系类型: 物理核心

        # 1. 首次调用获取缓冲区所需字节数
        buf_len = wintypes.DWORD(0)
        k32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(buf_len))
        if buf_len.value == 0:
            info["reason"] = "failed_query_size"
            return None, info

        # 2. 第二次调用获取核心结构体数组
        buf = (ctypes.c_ubyte * buf_len.value)()
        if not k32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ctypes.byref(buf_len)):
            info["reason"] = "failed_query_info"
            return None, info

        # 3. 解析 SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX 链表
        offset = 0
        cores = []
        while offset < buf_len.value:
            raw = bytes(buf[offset : offset + 48])
            rel = int.from_bytes(raw[0:4], "little")
            size = int.from_bytes(raw[4:8], "little")
            if size == 0:
                break
            if rel == RelationProcessorCore:
                flags = raw[8]  # LTP_PC_SMT (bit 0 表示是否支持超线程)
                eff = raw[9]  # EfficiencyClass: 0 为能效小核，数值越大代表性能越高
                mask = int.from_bytes(raw[32:40], "little")  # KAFFINITY
                group = int.from_bytes(raw[40:42], "little")  # Processor Group
                if group == 0 and mask > 0:
                    cores.append({"eff": eff, "smt": bool(flags & 1), "mask": mask})
            offset += size

        info["total_physical_cores"] = len(cores)
        if not cores:
            info["reason"] = "no_cores_detected"
            return None, info

        # 4. 判断能效等级多样性
        eff_classes = sorted({c["eff"] for c in cores})
        if len(eff_classes) <= 1:
            # 所有核心能效等级完全相同 -> 同构处理器 (如标准 AMD Ryzen 或传统 Intel)
            info["reason"] = "homogeneous_cpu"
            return None, info

        # 5. 异构大小核检测成立 -> 选取最高能效等级的核心（P-Core 性能大核）
        max_eff = eff_classes[-1]
        p_mask = 0
        p_count = 0
        e_count = 0
        for c in cores:
            if c["eff"] == max_eff:
                p_mask |= c["mask"]
                p_count += 1
            else:
                e_count += 1

        info["is_hybrid"] = True
        info["pcore_count"] = p_count
        info["ecore_count"] = e_count

        # 6. 安全校验: P-Core 逻辑线程数至少为 2，避免极特殊情况下识别异常限制为单核
        logical_pcore_threads = bin(p_mask).count("1")
        if logical_pcore_threads < 2:
            info["reason"] = "safety_check_insufficient_pcores"
            return None, info

        info["applied_mask"] = hex(p_mask)
        info["reason"] = f"hybrid_detected_{p_count}P_{e_count}E"
        return p_mask, info

    except Exception as e:
        info["reason"] = f"exception_{e}"
        return None, info


def optimize_cpu_affinity(verbose: bool = False) -> bool:
    """自动应用 P-Core 亲和性优化。

    在混合大小核系统上将当前进程锁定至大核逻辑掩码，消除能效核拖累；在常规系统上静默保持原样。
    每个进程生命周期内只执行一次。

    Args:
        verbose: 是否打印详细调度日志。

    Returns:
        bool: 是否成功应用了异构亲和性优化。
    """
    global _applied
    if _applied:
        return False
    _applied = True

    mask, info = get_hybrid_cpu_affinity()
    if mask is None:
        if verbose:
            print(f"[CPU Affinity] 跳过优化: {info['reason']}")
        return False

    try:
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        k32.SetProcessAffinityMask.restype = wintypes.BOOL
        h_proc = k32.GetCurrentProcess()
        success = k32.SetProcessAffinityMask(h_proc, mask)
        if success and verbose:
            print(f"[CPU Affinity] 已优化: 检测到异构架构 (P核: {info['pcore_count']}, E核: {info['ecore_count']})，已绑定 P-Core 掩码 {hex(mask)}")
        return bool(success)
    except Exception as e:
        if verbose:
            print(f"[CPU Affinity] 设置亲和性失败: {e}")
        return False


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    mask, info = get_hybrid_cpu_affinity()
    print("=== CPU 架构亲和性检测 ===")
    for k, v in info.items():
        print(f"  {k:<22}: {v}")
    if mask:
        print(f"\n[建议优化] 发现异构大小核，可用 P-Core 掩码: {hex(mask)}")
        applied = optimize_cpu_affinity(verbose=True)
        print(f"[执行状态] 优化生效: {applied}")
    else:
        print("\n[无需优化] 当前为同构 CPU 或无需调整亲和性。")
