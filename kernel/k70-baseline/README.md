# K70 同源内核构建与技术计划

| 项目 | 当前设计 |
| --- | --- |
| 目标设备 | Redmi K70 / vermeer，历史系统 OS3.0.302.0.WNKCNXM |
| 已知可启动内核 | 5.15.178-android13-8-00021-g6f2f96be86b9-ab13729987 |
| 首轮源码 | AOSP common `6f2f96be86b93de6ed58a2c46b456fb49734c382` |
| 官方清单参考 | common-android13-5.15-2025-07，清单提交 `c60e613d82bce49bdd7f28aca0aa36f74e522ceb` |
| 编译器 | 官方清单工具链中的 clang-r450784e |
| 构建方式 | 官方 Android 13 分支的 `build/build.sh`，保留 defconfig、KMI 符号裁剪与严格检查 |
| 首版额外补丁 | 无；不集成 KPM / KernelSU / SUSFS，不叠加调度或压缩算法补丁 |
| 产物 | 原始 Image、压缩内核、配置、符号表、校验值和构建日志；不是 Root 模块或刷机 ZIP |
| 验证边界 | 编译及静态检查通过，不等于设备驱动兼容、启动或省电验证通过 |

## 为什么先构建基线

原仓库的 `--no-kpm` 原先只跳过镜像后处理，仍向内核配置写入 `CONFIG_KPM=y`。
此分支修正该开关，并针对先启用、再禁用、再次启用的情况作离线回归测试。
这项缺陷已经确认；此前 Actions #1 的具体失败原因尚无完整日志，不能归因于这一个缺陷。

首轮锁定原内核版本中出现的源码提交，避免把相同的 5.15.178 误当作完全相同的源码。
保留真实编译版本和来源；不伪造 OEM 签名、设备指纹或游戏检测结果。
同源仍不能证明与厂家构建逐字节一致，也不能保证 vendor 模块或游戏兼容。

## 构建与资料

`source-lock.json` 固定全部源码、构建工具和工具链提交。同步采用普通 Git 浅检出，
只包含官方 legacy 构建所需的项目，并按官方清单恢复 linkfile。没有远程脚本管道执行。
构建不会改写 common 源码；出现非预期修改、错误提交或不完整依赖时停止。

在至少有 35 GiB 空间的 Linux 环境：

```bash
python3 -m unittest discover -s kernel/k70-baseline/tests -v
bash kernel/k70-baseline/run.sh /absolute/build-root /absolute/artifacts
```

GitHub Actions 使用标准 `ubuntu-22.04` runner，不用付费大型 runner；不创建 Release、
不发送通知、不缓存工具链。产物仅保留一天，应及时下载。公开仓库标准 runner 计算免费，
产物存储仍受 GitHub 配额约束。

构建日志由 Actions 的 always 步骤保存；成功时另检查 ARM64 Image 头、内核版本/KMI、
编译配置、Image 内嵌配置、全部源码提交及安全配置，生成 `audit.json` 和 `SHA256SUMS.txt`。
日志与完成时间在采集期间变化，不纳入静态文件校验清单。没有完整 ABI 布局对比，
没有实机启动/联网/蓝牙/休眠验证，报告会明确保留这些未验证项。

## 可以带来实际体验变化的方向

以下能力依据 2026-10-02 的历史设备快照；接入手机后需重新核对，不能代表当前状态。

| 方向 | 已有能力及是否需要移植 | 后续实现方式 | 验收指标 |
| --- | --- | --- | --- |
| 内存回收 MGLRU | 已编译，历史默认关闭；无需重复移植 | 基线完成后单独测试运行时开关，与 LMKD 协同 | 内存 PSI、refault、后台重载、应用返回 p95/p99 |
| zram 压缩 | 已有 lzo-rle、LZ4、Zstd；快照当时 SwapTotal=0 | 先核对系统初始化，再单独对比算法及容量；不能在已初始化设备直接热换 | 实际占用、压缩 CPU 时间、换入延迟、温升 |
| 帧截止时间与调度 | 已有 WALT/EAS/uclamp、PSI、sched tracepoints | 先定位 wake→run 排队，策略在用户态按场景短时生效并恢复 | 帧时间尾部、丢帧、线程等待、能耗和温升 |
| 后台冻结与恢复 | 依赖 Android framework、cgroup freezer 和 Binder 协同 | 使用系统已有冻结能力，限制冻结对象，前台恢复；不承诺进程永久保活 | 解冻尾延迟、通知/音频/通话正确、后台耗电、重载率 |
| 网络延迟 | 取决于基带/WLAN 驱动、网络环境和协议 | 先区分无线、DNS、UDP/TCP 问题；BBR 仅作为 TCP 单项实验 | 同环境 RTT/抖动/丢包、吞吐和耗电 |
| 触控与显示 | 依赖触控驱动、显示模式、合成器和应用 | 保留系统接口，先量触控到显示延迟；仅使用面板已报告模式 | 端到端延迟、误触、帧节奏和耗电 |

优先落实前三项，不宣称更换算法就能同时提升所有指标。手机用户态策略与真正的内核补丁
分别维护，不能把一个 Root 调参模块称为新内核。

## 首版暂不添加

| 项目 | 原因 |
| --- | --- |
| KPM 与多套 Root/隐藏补丁叠加 | 前次启动故障涉及这类安装链；先消除叠加变量，再独立评估 Root 需求 |
| 直接移植 EEVDF / sched_ext / 实时内核 | 与现有 5.15 厂商 WALT 驱动和 Android 调度接口存在移植成本，收益尚无证据 |
| 关闭温控、超频、144 Hz 强制模式 | 需要硬件、驱动、热设计和面板模式依据，不能只靠一个配置开关实现 |
| 全局游戏插帧 | 涉及图形渲染/合成或专用硬件路径，内核无法独自访问正确的运动信息与绘制语义 |
| “AI 内核”或改成微内核 | 尚无具体模型、预算和对照收益；微内核属于架构迁移，不能作为现有 GKI 的简单补丁 |

## 刷入前的独立步骤

构建脚本不包含 adb、fastboot、分区写入或自动刷机操作。
先保留已知可启动 boot 的独立备份与 SHA256；本构建不覆盖它。
实机测试另行读取当前系统、槽位和分区布局，核验 vendor 模块依赖及符号，再按当前设备
制作候选启动镜像、优先临时启动。只有启动、联网、休眠和回退通过，才进入单项优化对照。
完整系统升级或换 ROM 后，旧备份和本构建基线必须重新核对。

## 一手资料

- [AOSP 构建内核](https://source.android.com/docs/setup/build/building-kernels)
- [GKI 版本与 KMI](https://source.android.com/docs/core/architecture/kernel/gki-versioning)
- [MGLRU 运行接口](https://docs.kernel.org/admin-guide/mm/multigen_lru.html)
- [zram 接口及已初始化限制](https://docs.kernel.org/admin-guide/blockdev/zram.html)
- [GitHub Actions 计费与存储](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
