# DSH 与 Codex 鼠标键盘控制

当前版本 2.0.0：同一套接口控制 Windows 应用与游戏。普通控件、截图点按和本地连续键鼠输入共用一个控制锁。目标程序从观察获得，不再硬编码某个游戏。游戏控制的入口也在现有的 input MCP 内。

## 游戏与连续操作

新增四个 DSH 工具，Codex 的 client.mjs 使用同一 MCP 协议：

| DSH 工具 | Codex 命令 | 用途 |
|---|---|---|
| capture_target | capture | 捕获前台程序客户区，返回图片、程序路径、客户区尺寸、session 和新观察编号 |
| run_input_sequence | sequence | 本地执行按住、多键、点击、拖拽、相对视角和滚轮，返回最终图片 |
| teach_visual_profile | teach | 从已查看的截图裁剪提示，保存绑定目标程序及尺寸的视觉规则 |
| run_visual_control | watch | 在本机处理新画面并执行已审核的规则动作，报告帧数和延迟 |

先 observe，再 capture，再根据真实画面发 sequence；每次操作后查看新画面。request-file 始终为 UTF-8 JSON 文件的绝对路径。sequence 的参数为 observation_id、target_executable、dry_run 和 steps；dry_run 默认为 true，实际操作须设为 false。客户区坐标直接对应 capture 返回的原图像素，旧 act 命令仍使用主显示器归一化坐标，不能混用。

示例动作（程序路径和观察编号须替换为最新返回值）：

~~~json
{
  "observation_id": "LATEST_ID",
  "target_executable": "ABSOLUTE_EXECUTABLE_FROM_CAPTURE",
  "dry_run": false,
  "steps": [
    {"type":"key_down","keys":["shift","w"]},
    {"type":"relative_move","dx":80,"dy":0,"duration_s":0.2},
    {"type":"wait","duration_s":0.3},
    {"type":"key_up","keys":["w","shift"]}
  ]
}
~~~

单次最多 100 步、20 秒。支持 a-z、数字、F1-F12、方向键、shift/ctrl/alt、space/enter/tab、backspace/delete/insert/home/end/pageup/pagedown。Esc 保留为急停。拖拽参数是 x、y、to_x、to_y、duration_s 和可选 button。每次调用结束释放其持有的所有输入，不将按键遗留到下次调用。

本地视觉循环复用 DXcam 与 OpenCV，识别稳定出现的模板后执行 notify、click_match、click、key 或 sequence。规则可由两边共享，但必须来自已经检查过的画面。它提供视觉反馈和键鼠能力，尚不包含一个会玩任意游戏的通用策略模型；复杂玩法仍由调用它的模型判断。模型/网络往返时间也不会因为采集频率提高而消失。

窗口失焦、移动、缩放、显示器变化、观察更新、暂停或断连都会停止连续输入。用户移动鼠标也会停止；相对模式只额外允许游戏将鼠标重置到窗口中央。暂停命令可中断持有控制锁的会话。独立监护进程利用输入记录，在工作进程意外退出后释放本次按键与鼠标按钮，然后退出。

## 更新与测试

在此工程目录执行 upgrade-game.ps1 -PreviewOnly 检查安装预览，upgrade-game.ps1 安装 2.0.0。脚本先验证依赖并编译，再备份现有运行时和技能，复制文件，最后只修改 DSH 对应项的版本号与超时；既有其他配置保持语义一致。DXcam 与 comtypes 从已经存在的环境复制到安装目录 realtime/vendor，保留包的许可证与元数据；使用系统 Python 启动，避免现有虚拟环境解释器的 DLL 故障。

源码检查：python -B -m unittest discover -s realtime -p "test_*.py" -v。

真实桌面验证需要在登录用户会话运行 node verify.mjs 和 node test-game.mjs。后者在专用 WinForms 窗口检查持续按键、组合键、点击、拖拽、相对鼠标、滚轮、DXGI 视觉反应、取消调用、失焦和强制结束进程后的按键释放。test-game-report.json 为本次测试证据；实际游戏验证另存到 acceptance-report.json。

回退时先停止连续会话，再使用安装日志给出的 backup-before-game 目录恢复运行时与技能，并仅还原 DSH 对应项的版本及超时。只有确认备份后没有其他配置变化时，才恢复整份 profile 备份。

优先通过 Windows UI Automation 读取和操作应用控件，默认不截图。控件未暴露或需要视觉判断时，再使用原生鼠标键盘与截图。

## 使用

在 DSH 或 Codex 中描述具体的桌面任务，例如：`在这个测试窗口里输入“你好”，然后截图检查结果。`

DSH 工具命名空间为 `mcp__input__`：

- `inspect_controls`：读取前台控件、名称、可用操作及值，默认不截图。
- `control_action`：直接设置输入值、调用按钮、选择条目或设置焦点；返回控件状态，默认不截图。
- `observe_desktop`：按需返回主屏截图、前台窗口和新的观察编号。
- `desktop_action`：基于最新观察执行一次鼠标键盘操作，默认返回轻量窗口状态；`include_screenshot:true` 时附带截图。
- `list_windows`：列出窗口，不切换窗口。
- `pause_control` / `resume_control`：暂停和按要求恢复控制。

Codex 使用 `$desktop-control` 技能；可直接说“用鼠标键盘完成……”。

## 容错方式

- 优先回读原生控件状态核验结果；需要视觉核验时显式取得截图。
- 观察超过 120 秒、前台窗口变化、窗口移动或缩放后，拒绝继续输入。
- 两边共用 Windows 操作锁和最新观察编号；另一边更新观察后，旧编号不能继续用。
- 操作失败可能已部分执行，必须先观察，不能自动重复提交或输入。
- 中文通过 Unicode 输入，不需要改写剪贴板；按下的快捷键会释放。
- 坐标为主显示器归一化 `0..1000`，无需把缩小预览的像素直接用作屏幕坐标。

按住 Esc 进入下一次输入检查，或在下一次动作前将鼠标移到主屏左上角，可触发持续暂停。也可以明确要求暂停控制。没有后台键盘监听器。恢复必须由用户要求。

## 文件与限制

- 程序：`F:\deepseekA\integrations\desktop-control`
- Codex 技能：`C:\Users\hp\.codex\skills\desktop-control`
- 共享观察、暂停标记和截图：`G:\Codex_mmx\desktop-control-data`
- DSH 配置：当前 Web profile 的 `cordis.patch.yml`；安装前保存完整备份。

原生控件操作依赖应用暴露 UI Automation 能力，游戏、自绘画布等可能仍需截图。`set_value` 替换整个控件值并回读核验；`invoke` 表示调用已提交，需按返回状态判断任务是否完成。密码值不返回。控件扫描限制为300节点，默认最多返回60项。

鼠标指针及客户区采集目前要求目标完整位于主显示器内，连续接口支持拖拽。需要已登录、解锁的 Windows 桌面。Windows 可能拒绝向更高权限的应用输入；工具不会自行提升权限。锁只协调 DSH 与 Codex，不能阻止使用者移动鼠标或同一窗口内容自行变化。

## 验证与回退

`node verify.mjs` 检查 MCP 工具、返回图片和无效观察拒绝，不注入输入。原生测试在专用 WinForms 窗口验证中文、点击、快捷键和暂停。UIA测试验证不截图的控件读取、中文值设置与回读、按钮调用和旧观察拒绝。

卸载时移除 Web patch 中 `id: mcp-desktop-control` 的新增段。安装备份名为 `cordis.patch.yml.before-desktop-control-时间.bak`；仅在确认之后无其他配置变化时恢复整份备份。

实现前比较了 Windows-MCP、WindowsOSMCP 和微软 UI Automation 方案。当前版本复用 [UI Automation 控件接口](https://learn.microsoft.com/windows/win32/winauto/uiauto-controlpatternsoverview)、[SendInput](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput) 与 [Unicode 键盘输入](https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-keybdinput)，不额外安装 Python 运行时或桌面自动化框架。此处优化省去图片生成、传输和模型识图；模型推理、网络和目标应用耗时仍会影响整体响应。
