// StartupWatch — 极轻量开机启动项行为记录器
// 编译：csc /nologo /target:winexe /codepage:65001 /r:System.Management.dll /out:StartupWatch.exe StartupWatch.cs
// 目标：.NET Framework 4.0+（Win10/11 自带），GUI 子系统（自身不产生控制台窗口）
//
// 用法：StartupWatch.exe [秒数] [-o 输出目录]
//   默认采集 120 秒；输出 <exe目录>\logs\startup-watch-<时间戳>.tsv 与 .txt

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32;

internal sealed class ProcInfo
{
    public int Pid;
    public int Ppid;
    public string Name = "";
    public string Path = "";
    public string CmdLine = "";
    public int Sub = -1;                 // 2=GUI  3=控制台
    public DateTime Started = DateTime.MinValue;
    public DateTime FirstSeen;
    public DateTime LastSeen;
    public DateTime EndedAt = DateTime.MinValue;
    public bool Ended;
    public bool Self;
    public bool PreExisting;
    public bool Old;                     // 启动前就存在且早于回溯窗口：只登记，不报告
}

internal static class Program
{
    // ---------- Win32 ----------
    private const uint TH32CS_SNAPPROCESS = 0x00000002;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct PROCESSENTRY32
    {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExeFile;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint dwFlags, uint th32ProcessID);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool Process32First(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool Process32Next(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    // ---------- 状态 ----------
    private static readonly Dictionary<int, ProcInfo> Known = new Dictionary<int, ProcInfo>();
    private static readonly Dictionary<int, string> NameHistory = new Dictionary<int, string>();
    private static readonly Dictionary<int, int> PpidHistory = new Dictionary<int, int>();
    private static readonly Dictionary<string, int> SubCache =
        new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
    private static readonly List<ProcInfo> AllNew = new List<ProcInfo>();
    private static readonly List<ProcInfo> PreList = new List<ProcInfo>();
    private static readonly List<string[]> InvItems = new List<string[]>();   // source,name,command,state

    private static StreamWriter tsv;
    private static bool WmiOk;
    private static int selfPid;
    private static string selfName = "StartupWatch.exe";
    private static DateTime T0;
    private static int pollCount;

    // 本工具启动瞬间抓到的第一帧进程表。登录那一刻启动、又在 WMI 探测/清单枚举期间
    // 就退出的短命进程（就是「黑框一闪」），只会存在于这一帧里。
    private static List<PROCESSENTRY32> earlySnap = new List<PROCESSENTRY32>();

    // 自检：本进程在哪个会话、父进程是谁、以及「当前能看到多少个进程」。
    // 这三项用来识别「进程视图被沙箱/受限令牌缩小」的情况 —— 视图不完整时采集结果不可信。
    private static int snapTotal;
    private static int selfPpid;
    private static int selfSession = -1;
    private static int wmiProcCount = -1;

    // 事件追踪（ETW）：零轮询，能抓到存活仅几十毫秒的进程
    private static ManagementEventWatcher watcher;
    private static bool traceStarted;
    private static int traceCount;
    private static readonly List<string[]> TraceEvents = new List<string[]>();
    private static readonly List<string[]> TraceAll = new List<string[]>();

    // 已知的控制台程序名（事件追踪拿不到路径时的兜底判定）
    private static readonly string[] ConsoleNames = new string[]
    {
        "cmd.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe",
        "ping.exe", "tasklist.exe", "taskkill.exe", "net.exe", "net1.exe", "sc.exe", "reg.exe",
        "robocopy.exe", "findstr.exe", "certutil.exe", "bitsadmin.exe", "mshta.exe", "wmic.exe",
        "npm.cmd", "node.exe", "python.exe", "java.exe", "ffmpeg.exe", "ffprobe.exe", "ffplay.exe",
        "git.exe", "where.exe", "whoami.exe", "ipconfig.exe"
    };

    // ---------- 入口 ----------
    private static int Main(string[] args)
    {
        int seconds = 120;
        string outDir = null;
        try
        {
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a == "-o" && i + 1 < args.Length) { outDir = args[++i]; continue; }
                int n;
                if (int.TryParse(a, out n)) { seconds = n; continue; }
            }
        }
        catch { }
        if (seconds < 5) seconds = 5;
        if (seconds > 3600) seconds = 3600;

        T0 = DateTime.Now;
        Process self = Process.GetCurrentProcess();
        selfPid = self.Id;

        // 第一帧必须最早抓：后面 ProbeWmi 最多等 3 秒、自启项枚举可能等更久，
        // 这几秒正是登录自启项集中启动的窗口，放到后面就永远是盲区。
        try { earlySnap = Snapshot(); } catch { }

        try { selfSession = Process.GetCurrentProcess().SessionId; } catch { selfSession = -1; }
        try
        {
            foreach (PROCESSENTRY32 sp in earlySnap)
            {
                if ((int)sp.th32ProcessID == selfPid) { selfPpid = (int)sp.th32ParentProcessID; break; }
            }
        }
        catch { }

        string baseDir = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
        if (baseDir == null || baseDir.Length == 0) baseDir = ".";
        if (outDir == null || outDir.Length == 0) outDir = Path.Combine(baseDir, "logs");
        try { Directory.CreateDirectory(outDir); } catch { }

        string stamp = T0.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
        string tsvPath = Path.Combine(outDir, "startup-watch-" + stamp + ".tsv");
        string txtPath = Path.Combine(outDir, "startup-watch-" + stamp + ".txt");

        try
        {
            tsv = new StreamWriter(tsvPath, false, new UTF8Encoding(false));
            tsv.AutoFlush = true;
        }
        catch { tsv = null; }

        try
        {
            Line("HDR", "start=" + Iso(T0), "pid=" + selfPid, "arg_seconds=" + seconds,
                 "user=" + SafeUser(), "out=" + outDir, "firstframe=" + earlySnap.Count,
                 "session=" + selfSession);

            ProbeWmi();
            Line("HDR", "wmi=" + (WmiOk ? "on" : "off"), "wmi_proc_count=" + wmiProcCount);

            Baseline();
            Line("HDR", "view=" + snapTotal, "self_ppid=" + selfPpid,
                 "self_parent=" + NameOf(selfPpid));
            if (snapTotal < 40)
                Line("ERR", "process-view-suspicious", "visible=" + snapTotal,
                     "hint=本进程看到的进程数远少于正常系统，可能运行在沙箱/受限令牌中，本次结果不可信");

            StartTrace();
            Line("HDR", "trace=" + (traceStarted ? "started" : "unavailable"));
            RunWatch(seconds);
            DrainTrace();
            try { if (watcher != null) watcher.Stop(); } catch { }

            // 自启项清单挪到采集结束之后：schtasks 兜底路径可能耗时数秒，
            // 放在前面会把最关键的登录瞬间变成盲区。清单本身在本次运行期间不会变。
            try { DumpInventory(); }
            catch (Exception exInv) { Line("ERR", "inventory", OneLine(exInv.Message)); }

            WriteReport(txtPath, tsvPath, seconds);
        }
        catch (Exception ex)
        {
            try { Line("ERR", ex.GetType().Name, OneLine(ex.Message)); } catch { }
        }
        finally
        {
            try { if (tsv != null) { tsv.Flush(); tsv.Close(); } } catch { }
        }
        return 0;
    }

    private static string SafeUser()
    {
        try { return Environment.UserDomainName + "\\" + Environment.UserName; }
        catch { return "?"; }
    }

    private static string Iso(DateTime t)
    {
        return t.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture);
    }

    private static void Line(params string[] parts)
    {
        if (tsv == null) return;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.Length; i++)
        {
            if (i > 0) sb.Append('\t');
            string p = parts[i];
            if (p == null) p = "";
            sb.Append(p.Replace("\t", " ").Replace("\r", " ").Replace("\n", " "));
        }
        try { tsv.WriteLine(sb.ToString()); } catch { }
    }

    private static string OneLine(string s)
    {
        if (s == null) return "";
        return s.Replace("\r", " ").Replace("\n", " ").Trim();
    }

    // ---------- WMI（仅用于取命令行，可选增强；全部带超时，卡住就降级） ----------
    private static void ProbeWmi()
    {
        bool ok = false;
        Thread t = new Thread(delegate()
        {
            try
            {
                using (ManagementObjectSearcher s =
                    new ManagementObjectSearcher("SELECT ProcessId FROM Win32_Process WHERE ProcessId=4"))
                {
                    foreach (ManagementBaseObject o in s.Get()) { }
                }
                ok = true;

                // 用完全独立的另一套 API 再数一遍进程总数：
                // 若 WMI 能数出 100+ 而 Toolhelp 只有几个，说明是 Toolhelp 视图被沙箱限制，不是代码问题。
                int n = 0;
                using (ManagementObjectSearcher s2 =
                    new ManagementObjectSearcher("SELECT ProcessId FROM Win32_Process"))
                {
                    foreach (ManagementBaseObject o2 in s2.Get()) { n++; }
                }
                wmiProcCount = n;
            }
            catch { ok = false; }
        });
        t.IsBackground = true;
        t.Start();
        if (!t.Join(3000)) { WmiOk = false; return; }   // 3 秒内没回来就当不可用
        WmiOk = ok;
    }

    private static string GetCmdLine(int pid)
    {
        if (!WmiOk) return "";
        string result = "";
        int target = pid;
        Thread t = new Thread(delegate()
        {
            try
            {
                using (ManagementObjectSearcher s =
                    new ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE ProcessId=" + target))
                {
                    foreach (ManagementBaseObject o in s.Get())
                    {
                        object v = o["CommandLine"];
                        if (v != null) { result = v.ToString(); return; }
                    }
                }
            }
            catch { }
        });
        t.IsBackground = true;
        t.Start();
        if (!t.Join(800)) return "";                     // 单次最多等 0.8 秒
        return result;
    }

    // ---------- 事件追踪：Win32_ProcessStartTrace（ETW，零轮询） ----------
    private static void StartTrace()
    {
        try
        {
            WqlEventQuery q = new WqlEventQuery("SELECT * FROM Win32_ProcessStartTrace");
            watcher = new ManagementEventWatcher(q);
            watcher.EventArrived += new EventArrivedEventHandler(OnTraceEvent);
            watcher.Start();
            traceStarted = true;
        }
        catch { traceStarted = false; }
    }

    private static void OnTraceEvent(object sender, EventArrivedEventArgs e)
    {
        try
        {
            string name = Convert.ToString(e.NewEvent["ProcessName"]);
            string pid = Convert.ToString(e.NewEvent["ProcessID"]);
            string ppid = Convert.ToString(e.NewEvent["ParentProcessID"]);
            lock (TraceEvents)
            {
                TraceEvents.Add(new string[] { Iso(DateTime.Now), name, pid, ppid });
            }
            traceCount++;
        }
        catch { }
    }

    private static void DrainTrace()
    {
        string[][] batch;
        lock (TraceEvents)
        {
            if (TraceEvents.Count == 0) return;
            batch = TraceEvents.ToArray();
            TraceEvents.Clear();
        }
        foreach (string[] ev in batch)
        {
            int pid = ParseInt(ev[2]);
            int ppid = ParseInt(ev[3]);
            string pname = NameOfLive(ppid);
            TraceAll.Add(new string[] { ev[0], ev[1], ev[2], ev[3], pname });
            Line("TRC", ev[0], ev[1], "pid=" + ev[2], "ppid=" + ev[3],
                 "parent=" + (pname.Length == 0 ? "?" : pname),
                 "console=" + (IsConsoleName(ev[1]) ? "yes" : "?"));
        }
    }

    private static int ParseInt(string s)
    {
        int v;
        if (int.TryParse(s, out v)) return v;
        return 0;
    }

    private static string NameOfLive(int pid)
    {
        string n = NameOf(pid);
        if (n.Length > 0) return n;
        try
        {
            using (Process p = Process.GetProcessById(pid)) return p.ProcessName + ".exe";
        }
        catch { return ""; }
    }

    private static bool IsConsoleName(string name)
    {
        if (name == null) return false;
        if (name.StartsWith("ffmpeg", StringComparison.OrdinalIgnoreCase)) return true;
        foreach (string c in ConsoleNames)
        {
            if (string.Equals(name, c, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }

    // ---------- 外部命令捕获（带超时，超时杀进程） ----------
    private static string RunCapture(string exe, string args, int timeoutMs)
    {
        Process p = null;
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo(exe, args);
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.CreateNoWindow = true;
            p = Process.Start(psi);
            string result = null;
            Process pp = p;
            Thread t = new Thread(delegate() { try { result = pp.StandardOutput.ReadToEnd(); } catch { } });
            t.IsBackground = true;
            t.Start();
            if (!t.Join(timeoutMs))
            {
                try { p.Kill(); } catch { }
                return null;
            }
            try { p.WaitForExit(timeoutMs); } catch { }
            return result;
        }
        catch { return null; }
        finally { try { if (p != null) p.Dispose(); } catch { } }
    }

    // ---------- 进程枚举（Toolhelp，无需 WMI / 无需管理员） ----------
    private static List<PROCESSENTRY32> Snapshot()
    {
        List<PROCESSENTRY32> list = new List<PROCESSENTRY32>();
        IntPtr h = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (h == IntPtr.Zero || h.ToInt64() == -1) return list;
        try
        {
            PROCESSENTRY32 pe = new PROCESSENTRY32();
            pe.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            if (!Process32First(h, ref pe)) return list;
            do
            {
                list.Add(pe);
                pe.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            } while (Process32Next(h, ref pe));
        }
        catch { }
        finally { CloseHandle(h); }
        return list;
    }

    private static string GetPath(int pid)
    {
        try
        {
            using (Process p = Process.GetProcessById(pid))
            {
                return p.MainModule.FileName;
            }
        }
        catch { return ""; }
    }

    private static DateTime GetStart(int pid)
    {
        try
        {
            using (Process p = Process.GetProcessById(pid))
            {
                return p.StartTime;
            }
        }
        catch { return DateTime.MinValue; }
    }

    // ---------- PE 子系统（2=GUI 3=控制台） ----------
    private static int GetSubsystem(string path)
    {
        if (path == null || path.Length == 0) return -1;
        int cached;
        if (SubCache.TryGetValue(path, out cached)) return cached;
        int sub = -1;
        try
        {
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                byte[] buf = new byte[4];
                if (fs.Read(buf, 0, 4) == 4 && buf[0] == 0x4D && buf[1] == 0x5A)
                {
                    fs.Position = 0x3C;
                    byte[] p4 = new byte[4];
                    if (fs.Read(p4, 0, 4) == 4)
                    {
                        int pe = BitConverter.ToInt32(p4, 0);
                        fs.Position = pe + 0x5C;
                        byte[] s2 = new byte[2];
                        if (fs.Read(s2, 0, 2) == 2) sub = BitConverter.ToUInt16(s2, 0);
                    }
                }
            }
        }
        catch { sub = -1; }
        SubCache[path] = sub;
        return sub;
    }

    private static string SubText(int sub)
    {
        if (sub == 2) return "GUI";
        if (sub == 3) return "控制台";
        return "未知";
    }

    // ---------- 基线：抓“在我们启动之前已经跑起来”的进程 ----------
    private static void Baseline()
    {
        DateTime lookback = T0.AddSeconds(-180);
        HashSet<int> seenPids = new HashSet<int>();
        List<PROCESSENTRY32> snap = new List<PROCESSENTRY32>();
        // 先合并「启动首帧」，再补当前快照：首帧里那些已经退出的进程就此被保住
        foreach (PROCESSENTRY32 pe in earlySnap)
        {
            if (seenPids.Add((int)pe.th32ProcessID)) snap.Add(pe);
        }
        foreach (PROCESSENTRY32 pe in Snapshot())
        {
            if (seenPids.Add((int)pe.th32ProcessID)) snap.Add(pe);
        }
        snapTotal = snap.Count;
        foreach (PROCESSENTRY32 pe in snap)
        {
            int pid = (int)pe.th32ProcessID;
            NameHistory[pid] = pe.szExeFile;
            PpidHistory[pid] = (int)pe.th32ParentProcessID;

            ProcInfo pi = new ProcInfo();
            pi.Pid = pid;
            pi.Ppid = (int)pe.th32ParentProcessID;
            pi.Name = pe.szExeFile;
            pi.FirstSeen = T0;
            pi.LastSeen = T0;
            pi.PreExisting = true;
            pi.Self = (pi.Pid == selfPid) || (pi.Ppid == selfPid) || IsSelfName(pi.Name);
            pi.Started = GetStart(pi.Pid);
            bool recent = (pi.Started != DateTime.MinValue) && (pi.Started >= lookback);
            if (recent)
            {
                pi.Path = GetPath(pi.Pid);
                pi.Sub = GetSubsystem(pi.Path);
                pi.CmdLine = GetCmdLine(pi.Pid);
                PreList.Add(pi);
                Line("PRE", pi.Name, "pid=" + pi.Pid, "ppid=" + pi.Ppid,
                     "start=" + Iso(pi.Started),
                     "sub=" + SubText(pi.Sub), "path=" + pi.Path,
                     "cmd=" + pi.CmdLine, pi.Self ? "SELF" : "");
            }
            else
            {
                pi.Old = true;   // 早于回溯窗口：登记进 Known（避免被误判为“新建”），但不报告
            }
            Known[pi.Pid] = pi;
        }
    }

    // ---------- 采集循环：前 20 秒 50ms，之后 200ms，60 秒后 1000ms ----------
    private static void RunWatch(int seconds)
    {
        DateTime deadline = T0.AddSeconds(seconds);
        while (DateTime.Now < deadline)
        {
            double elapsed = (DateTime.Now - T0).TotalSeconds;
            int interval;
            if (elapsed < 20) interval = 50;
            else if (elapsed < 60) interval = 200;
            else interval = 1000;

            Tick();
            DrainTrace();
            pollCount++;
            Thread.Sleep(interval);
        }
        Tick();
    }

    private static void Tick()
    {
        DateTime now = DateTime.Now;
        HashSet<int> seen = new HashSet<int>();
        List<PROCESSENTRY32> snap = Snapshot();

        foreach (PROCESSENTRY32 pe in snap)
        {
            int pid = (int)pe.th32ProcessID;
            seen.Add(pid);
            ProcInfo pi;
            if (!Known.TryGetValue(pid, out pi))
            {
                pi = new ProcInfo();
                pi.Pid = pid;
                pi.Ppid = (int)pe.th32ParentProcessID;
                pi.Name = pe.szExeFile;
                pi.FirstSeen = now;
                pi.LastSeen = now;
                pi.Self = (pid == selfPid) || (pi.Ppid == selfPid) || IsSelfName(pi.Name);
                pi.Started = GetStart(pid);
                pi.Path = GetPath(pid);
                pi.Sub = GetSubsystem(pi.Path);
                pi.CmdLine = GetCmdLine(pid);
                Known[pid] = pi;
                NameHistory[pid] = pi.Name;
                PpidHistory[pid] = pi.Ppid;
                if (!pi.PreExisting && !pi.Self) AllNew.Add(pi);

                string pname = NameOf(pi.Ppid);
                Line("NEW", Iso(now), pi.Name, "pid=" + pid, "ppid=" + pi.Ppid,
                     "parent=" + (pname.Length == 0 ? "?" : pname),
                     "sub=" + SubText(pi.Sub), "path=" + pi.Path,
                     "cmd=" + pi.CmdLine,
                     pi.Self ? "SELF" : "");
            }
            else
            {
                pi.LastSeen = now;
            }
        }

        // 消失的进程
        List<int> gone = new List<int>();
        foreach (KeyValuePair<int, ProcInfo> kv in Known)
        {
            if (kv.Value.PreExisting || kv.Value.Old) continue;
            if (!seen.Contains(kv.Key))
            {
                ProcInfo pi = kv.Value;
                pi.Ended = true;
                pi.EndedAt = now;
                long life = (long)(now - pi.FirstSeen).TotalMilliseconds;
                Line("STP", Iso(now), pi.Name, "pid=" + pi.Pid,
                     "lifetime_ms=" + life, "parent=" + NameOf(pi.Ppid),
                     "sub=" + SubText(pi.Sub), pi.Self ? "SELF" : "");
                gone.Add(kv.Key);
            }
        }
        foreach (int g in gone) Known.Remove(g);
    }

    private static bool IsSelfName(string name)
    {
        return string.Equals(name, selfName, StringComparison.OrdinalIgnoreCase);
    }

    private static string NameOf(int pid)
    {
        string n;
        if (NameHistory.TryGetValue(pid, out n)) return n;
        return "";
    }

    private static int PpidOf(int pid)
    {
        int v;
        if (PpidHistory.TryGetValue(pid, out v)) return v;
        return 0;
    }

    private static string Chain(int pid)
    {
        StringBuilder sb = new StringBuilder();
        int cur = pid;
        for (int depth = 0; depth < 4 && cur > 0; depth++)
        {
            string nm = NameOf(cur);
            if (nm.Length == 0) { sb.Append("pid" + cur); break; }
            sb.Append(nm);
            int pp = PpidOf(cur);
            if (pp <= 0 || pp == cur) break;
            sb.Append(" <- ");
            cur = pp;
        }
        return sb.ToString();
    }

    // ---------- 自启项清单 ----------
    private static void DumpInventory()
    {
        string[] runSubs = new string[]
        {
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            @"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Run",
            @"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\RunOnce"
        };
        string[] roots = new string[] { "HKCU", "HKLM" };

        foreach (string rootName in roots)
        {
            RegistryKey root = rootName == "HKCU" ? Registry.CurrentUser : Registry.LocalMachine;
            foreach (string sub in runSubs)
            {
                DumpRunKey(root, sub, rootName + "\\" + sub);
            }
        }

        // 启动文件夹
        try
        {
            string p = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
            DumpFolder(p, "启动文件夹(用户)");
        }
        catch { }
        try
        {
            string p = Environment.GetFolderPath(Environment.SpecialFolder.CommonStartup);
            DumpFolder(p, "启动文件夹(公共)");
        }
        catch { }

        // 登录/开机触发的计划任务：优先 Task Scheduler COM API，其次 schtasks CSV，最后 schtasks XML
        // （schtasks /query /xml 需要管理员权限，普通会话会返回空）
        bool tasksOk = false;
        try { tasksOk = DumpTasksCom(); }
        catch { tasksOk = false; }
        if (tasksOk) { Line("HDR", "tasks-source=COM"); return; }

        try { tasksOk = DumpTasksCsv(); }
        catch { tasksOk = false; }
        if (tasksOk) { Line("HDR", "tasks-source=schtasks-csv"); return; }

        try
        {
            string xml = RunCapture("schtasks.exe", "/query /xml", 15000);
            if (xml != null && xml.Trim().Length > 0)
            {
                Line("HDR", "tasks-source=schtasks-xml");
            }
            else
            {
                Line("HDR", "tasks-source=none");
                AddInv("计划任务", "(清单获取失败)",
                       "Task Scheduler COM API、schtasks CSV、schtasks XML 三条路都拿不到任务清单（权限或被安全软件拦截），本节的计划任务未纳入检查", "");
            }
            if (xml != null && xml.Trim().Length > 0)
            {
                MatchCollection blocks = Regex.Matches(xml, "<Task\\b.*?</Task>", RegexOptions.Singleline);
                foreach (Match b in blocks)
                {
                    string body = b.Value;
                    bool logon = body.IndexOf("<LogonTrigger", StringComparison.OrdinalIgnoreCase) >= 0;
                    bool boot = body.IndexOf("<BootTrigger", StringComparison.OrdinalIgnoreCase) >= 0;
                    if (!logon && !boot) continue;
                    Match mu = Regex.Match(body, "<URI>(.*?)</URI>", RegexOptions.Singleline);
                    string uri = mu.Success ? mu.Groups[1].Value : "?";
                    string cmd = "";
                    string arg = "";
                    foreach (Match mc in Regex.Matches(body, "<Command>(.*?)</Command>", RegexOptions.Singleline))
                        cmd = cmd.Length == 0 ? mc.Groups[1].Value : cmd + " ; " + mc.Groups[1].Value;
                    foreach (Match ma in Regex.Matches(body, "<Arguments>(.*?)</Arguments>", RegexOptions.Singleline))
                        arg = arg.Length == 0 ? ma.Groups[1].Value : arg + " ; " + ma.Groups[1].Value;
                    Match uid = Regex.Match(body, "<UserId>(.*?)</UserId>", RegexOptions.Singleline);
                    string user = uid.Success ? uid.Groups[1].Value : "";
                    string trig = (logon ? "Logon" : "") + (boot ? (logon ? "+Boot" : "Boot") : "");
                    string full = (cmd + " " + arg).Trim();
                    AddInv("计划任务[" + trig + "]", uri, full, user);
                }
            }
        }
        catch (Exception ex) { AddInv("计划任务", "(读取失败)", OneLine(ex.Message), ""); }
    }

    // ---------- 计划任务：Task Scheduler COM API（普通用户即可，不依赖 schtasks 文本解析） ----------
    private static object ComGet(object o, string name)
    {
        return o.GetType().InvokeMember(name, System.Reflection.BindingFlags.GetProperty, null, o, null);
    }

    private static object ComGetIdx(object o, string name, int index)
    {
        return o.GetType().InvokeMember(name, System.Reflection.BindingFlags.GetProperty, null, o,
            new object[] { index });
    }

    private static object ComCall(object o, string name, params object[] args)
    {
        return o.GetType().InvokeMember(name, System.Reflection.BindingFlags.InvokeMethod, null, o, args);
    }

    private static int taskCount;

    private static bool DumpTasksCom()
    {
        try
        {
            Type t = Type.GetTypeFromProgID("Schedule.Service");
            if (t == null) return false;
            object svc = Activator.CreateInstance(t);
            try { ComCall(svc, "Connect"); }
            catch
            {
                ComCall(svc, "Connect", Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing);
            }
            object root = ComCall(svc, "GetFolder", "\\");
            WalkTaskFolder(root, "\\");
            return true;
        }
        catch { return false; }
    }

    private static void WalkTaskFolder(object folder, string path)
    {
        try
        {
            object tasks = ComCall(folder, "GetTasks", 1);   // 1 = 包含隐藏任务
            int n = Convert.ToInt32(ComGet(tasks, "Count"));
            for (int i = 1; i <= n; i++)
            {
                if (taskCount > 3000) return;
                taskCount++;
                object task = null;
                try { task = ComGetIdx(tasks, "Item", i); }
                catch { continue; }
                try
                {
                    string name = Convert.ToString(ComGet(task, "Name"));
                    string full = path.TrimEnd('\\') + "\\" + name;
                    object def = ComGet(task, "Definition");

                    StringBuilder trig = new StringBuilder();
                    object trigs = ComGet(def, "Triggers");
                    int tn = Convert.ToInt32(ComGet(trigs, "Count"));
                    for (int k = 1; k <= tn; k++)
                    {
                        object tr = null;
                        try { tr = ComGetIdx(trigs, "Item", k); }
                        catch { continue; }
                        int ty = Convert.ToInt32(ComGet(tr, "Type"));
                        string label = null;
                        if (ty == 9) label = "Logon";
                        else if (ty == 8) label = "Boot";
                        if (label == null) continue;
                        bool trEnabled = true;
                        try { trEnabled = Convert.ToBoolean(ComGet(tr, "Enabled")); }
                        catch { }
                        if (trig.Length > 0) trig.Append('+');
                        trig.Append(label);
                        if (!trEnabled) trig.Append("(未启用)");
                    }
                    if (trig.Length == 0) continue;   // 只收 登录/开机 触发的

                    StringBuilder cmds = new StringBuilder();
                    object acts = ComGet(def, "Actions");
                    int an = Convert.ToInt32(ComGet(acts, "Count"));
                    for (int k = 1; k <= an; k++)
                    {
                        try
                        {
                            object ac = ComGetIdx(acts, "Item", k);
                            string ap = "";
                            string ar = "";
                            try { ap = Convert.ToString(ComGet(ac, "Path")); }
                            catch { }
                            try { ar = Convert.ToString(ComGet(ac, "Arguments")); }
                            catch { }
                            string one = (ap + " " + ar).Trim();
                            if (one.Length == 0) one = "(非可执行动作)";
                            if (cmds.Length > 0) cmds.Append(" ; ");
                            cmds.Append(one);
                        }
                        catch { }
                    }

                    string user = "";
                    try
                    {
                        object pr = ComGet(def, "Principal");
                        user = Convert.ToString(ComGet(pr, "UserId"));
                    }
                    catch { }

                    string state = "启用";
                    try { state = Convert.ToBoolean(ComGet(task, "Enabled")) ? "启用" : "禁用"; }
                    catch { }

                    AddInv("计划任务[" + trig + "]", full, cmds.ToString().Trim(),
                           state + (user.Length > 0 ? "  " + user : ""));
                }
                catch { }
            }
        }
        catch { }

        try
        {
            object subs = ComCall(folder, "GetFolders", 0);
            int m = Convert.ToInt32(ComGet(subs, "Count"));
            for (int i = 1; i <= m; i++)
            {
                object sub = null;
                try { sub = ComGetIdx(subs, "Item", i); }
                catch { continue; }
                string subName;
                try { subName = Convert.ToString(ComGet(sub, "Name")); }
                catch { continue; }
                WalkTaskFolder(sub, path.TrimEnd('\\') + "\\" + subName);
            }
        }
        catch { }
    }

    // ---------- 计划任务兜底：schtasks /query /v /fo CSV（普通用户可用，/xml 需要管理员） ----------
    private static List<string> ParseCsvLine(string line)
    {
        List<string> res = new List<string>();
        StringBuilder cur = new StringBuilder();
        bool q = false;
        for (int i = 0; i < line.Length; i++)
        {
            char ch = line[i];
            if (q)
            {
                if (ch == '"')
                {
                    if (i + 1 < line.Length && line[i + 1] == '"') { cur.Append('"'); i++; }
                    else q = false;
                }
                else cur.Append(ch);
            }
            else
            {
                if (ch == '"') q = true;
                else if (ch == ',') { res.Add(cur.ToString()); cur.Length = 0; }
                else cur.Append(ch);
            }
        }
        res.Add(cur.ToString());
        return res;
    }

    private static string Cell(List<string> c, int i)
    {
        return (i >= 0 && i < c.Count) ? c[i].Trim() : "";
    }

    private static int FindCol(List<string> head, string en, string zh)
    {
        for (int i = 0; i < head.Count; i++)
        {
            string h = head[i];
            if (h.IndexOf(en, StringComparison.OrdinalIgnoreCase) >= 0) return i;
            if (zh.Length > 0 && h.IndexOf(zh, StringComparison.Ordinal) >= 0) return i;
        }
        return -1;
    }

    private static bool DumpTasksCsv()
    {
        string csv = RunCapture("schtasks.exe", "/query /v /fo CSV", 25000);
        if (csv == null || csv.Trim().Length == 0) return false;
        string[] lines = csv.Replace("\r\n", "\n").Split('\n');
        if (lines.Length < 2) return false;

        List<string> head = ParseCsvLine(lines[0]);
        int iName = FindCol(head, "TaskName", "任务名");
        int iRun = FindCol(head, "Task To Run", "要运行的任务");
        int iType = FindCol(head, "Schedule Type", "计划类型");
        int iState = FindCol(head, "Scheduled Task State", "计划任务状态");
        int iUser = FindCol(head, "Run As User", "作为用户运行");
        if (iName < 0) iName = 1;
        if (iRun < 0) iRun = 8;
        if (iState < 0) iState = 11;
        if (iUser < 0) iUser = 14;
        if (iType < 0) iType = 18;

        int found = 0;
        for (int i = 1; i < lines.Length; i++)
        {
            if (lines[i].Trim().Length == 0) continue;
            List<string> c = ParseCsvLine(lines[i]);
            string type = Cell(c, iType);
            if (type.Length == 0) continue;
            bool logon = type.IndexOf("logon", StringComparison.OrdinalIgnoreCase) >= 0 || type.IndexOf("登录", StringComparison.Ordinal) >= 0;
            bool boot = type.IndexOf("start", StringComparison.OrdinalIgnoreCase) >= 0 || type.IndexOf("启动", StringComparison.Ordinal) >= 0;
            if (!logon && !boot) continue;
            string trig = (logon ? "Logon" : "") + (boot ? (logon ? "+Boot" : "Boot") : "");
            string user = Cell(c, iUser);
            string state = Cell(c, iState);
            if (state.Length == 0) state = "?";
            AddInv("计划任务[" + trig + "]", Cell(c, iName), Cell(c, iRun),
                   state + (user.Length > 0 ? "  " + user : ""));
            found++;
        }
        return found > 0;
    }

    private static void DumpRunKey(RegistryKey root, string sub, string label)
    {
        try
        {
            using (RegistryKey k = root.OpenSubKey(sub))
            {
                if (k == null) return;
                string[] names = k.GetValueNames();
                foreach (string n in names)
                {
                    object v = k.GetValue(n);
                    string cmd = v == null ? "" : v.ToString();
                    AddInv(label, n, cmd, StateOf(root, n));
                }
            }
        }
        catch (Exception ex) { AddInv(label, "(读取失败)", OneLine(ex.Message), ""); }
    }

    private static void DumpFolder(string dir, string label)
    {
        try
        {
            if (!Directory.Exists(dir)) return;
            string[] files = Directory.GetFiles(dir);
            foreach (string f in files)
            {
                string n = Path.GetFileName(f);
                if (string.Equals(n, "desktop.ini", StringComparison.OrdinalIgnoreCase)) continue;
                string target = "";
                if (n.EndsWith(".lnk", StringComparison.OrdinalIgnoreCase)) target = ResolveLnk(f);
                string cmd = (target.Length > 0) ? ("\"" + target + "\"  [快捷方式: " + f + "]") : f;
                AddInv(label, n, cmd, StateOfFolder(n));
            }
        }
        catch (Exception ex) { AddInv(label, "(读取失败)", OneLine(ex.Message), ""); }
    }

    // 解析 .lnk 的真实目标（走 WScript.Shell COM，失败就返回空）
    private static string ResolveLnk(string lnkPath)
    {
        try
        {
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            if (t == null) return "";
            object sh = Activator.CreateInstance(t);
            object sc = t.InvokeMember("CreateShortcut", System.Reflection.BindingFlags.InvokeMethod,
                null, sh, new object[] { lnkPath });
            if (sc == null) return "";
            object tp = sc.GetType().InvokeMember("TargetPath", System.Reflection.BindingFlags.GetProperty,
                null, sc, null);
            return tp == null ? "" : tp.ToString();
        }
        catch { return ""; }
    }

    // StartupApproved：首字节 02/06=启用，03/07=禁用
    private static string StateOf(RegistryKey root, string name)
    {
        string[] subs = new string[]
        {
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"
        };
        foreach (string s in subs)
        {
            try
            {
                using (RegistryKey k = root.OpenSubKey(s))
                {
                    if (k == null) continue;
                    object v = k.GetValue(name);
                    byte[] b = v as byte[];
                    if (b != null && b.Length > 0) return DecodeState(b[0]);
                }
            }
            catch { }
        }
        return "默认(启用)";
    }

    private static string StateOfFolder(string fileName)
    {
        try
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(
                @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"))
            {
                if (k != null)
                {
                    object v = k.GetValue(fileName);
                    byte[] b = v as byte[];
                    if (b != null && b.Length > 0) return DecodeState(b[0]);
                }
            }
        }
        catch { }
        return "默认(启用)";
    }

    private static string DecodeState(byte b)
    {
        if (b == 2 || b == 6) return "启用";
        if (b == 3 || b == 7) return "禁用";
        return "其他(0x" + b.ToString("X2") + ")";
    }

    private static void AddInv(string source, string name, string command, string state)
    {
        InvItems.Add(new string[] { source, name, command, state });
        Line("INV", source, name, state, command);
    }

    // ---------- 报告 ----------
    private static void WriteReport(string txtPath, string tsvPath, int seconds)
    {
        StringBuilder sb = new StringBuilder();
        sb.AppendLine("=== StartupWatch 开机启动项行为报告 ===");
        sb.AppendLine("采集开始 : " + Iso(T0));
        sb.AppendLine("采集时长 : " + seconds + " 秒（前 20s 每 50ms 采样，20-60s 每 200ms，之后每 1000ms）");
        sb.AppendLine("采样次数 : " + pollCount);
        sb.AppendLine("WMI 增强 : " + (WmiOk ? "可用（已记录命令行）" : "不可用（仅记录进程名/路径/父子关系）"));
        sb.AppendLine("事件追踪 : " + (traceStarted
            ? (traceCount > 0
                ? ("已启用，收到 " + traceCount + " 个进程创建事件（可捕获存活仅几十毫秒的进程）")
                : "已启用但一个事件都没收到（很可能被系统限制，本次实际以轮询为准）")
            : "不可用（此机器上可能需要管理员权限），本次仅靠轮询采样"));
        sb.AppendLine("原始数据 : " + tsvPath);
        sb.AppendLine("运行环境 : 会话=" + selfSession + "  父进程=" +
            (NameOf(selfPpid).Length == 0 ? "pid" + selfPpid : NameOf(selfPpid) + "(pid" + selfPpid + ")") +
            "  首帧可见进程数=" + earlySnap.Count + "  基线可见进程数=" + snapTotal +
            "  WMI可见进程数=" + (wmiProcCount < 0 ? "?" : wmiProcCount.ToString()));
        if (snapTotal < 40)
        {
            sb.AppendLine();
            sb.AppendLine("!!! 警告：本进程只能看到 " + snapTotal + " 个进程，远少于正常运行的系统（通常 100 以上）。");
            if (wmiProcCount > 40 && wmiProcCount > snapTotal * 2)
                sb.AppendLine("!!! 同一时刻 WMI 却能数出 " + wmiProcCount + " 个进程 —— 证明是进程枚举视图被限制（沙箱/受限令牌），不是系统真的很空。");
            sb.AppendLine("!!! 这种情况下它对桌面进程几乎没有可见性，下面的结果不可信，请改用正常方式启动本程序。");
        }
        sb.AppendLine("自启项总数: " + InvItems.Count + "    本次新进程数: " + AllNew.Count
            + "    启动前已存在(近180s): " + PreList.Count);
        sb.AppendLine();

        // 1) 疑似黑框
        sb.AppendLine("--- 一、疑似「黑框一闪」的进程（控制台程序 且【存活短 / 产生过 conhost / 与本工具几乎同时启动】） ---");
        List<ProcInfo> suspect = new List<ProcInfo>();
        suspect.AddRange(AllNew);
        suspect.AddRange(PreList);   // 抓首帧时已存在、随后消失的进程也在里面
        int n1 = 0;
        foreach (ProcInfo pi in suspect)
        {
            if (pi.Self) continue;
            bool console = (pi.Sub == 3);
            bool brief = pi.Ended && (pi.EndedAt - pi.FirstSeen).TotalMilliseconds < 3000;
            bool hasConhost = false;
            foreach (ProcInfo q in suspect)
            {
                if (q.Ppid == pi.Pid && string.Equals(q.Name, "conhost.exe", StringComparison.OrdinalIgnoreCase))
                    hasConhost = true;
            }
            // 在抓首帧之前就启动的进程测不到寿命；但启动时间与本工具几乎重合的，
            // 就是登录瞬间那一批，同样算嫌疑对象。
            bool instant = pi.PreExisting && pi.Started != DateTime.MinValue
                && Math.Abs((pi.Started - T0).TotalSeconds) <= 5.0;
            if ((console && (brief || hasConhost || instant)) || hasConhost)
            {
                n1++;
                DateTime shown = (pi.PreExisting && pi.Started != DateTime.MinValue) ? pi.Started : pi.FirstSeen;
                sb.AppendLine(string.Format("  [{0}] {1}  pid={2}  存活={3}  子系统={4}{5}{6}",
                    shown.ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture),
                    pi.Name, pi.Pid, LifeText(pi), SubText(pi.Sub),
                    hasConhost ? "  已创建 conhost" : "",
                    instant ? "  ← 与本工具同时启动（抓首帧时已在）" : ""));
                sb.AppendLine("        父链: " + Chain(pi.Pid));
                if (pi.Path.Length > 0) sb.AppendLine("        路径: " + pi.Path);
                if (pi.CmdLine.Length > 0) sb.AppendLine("        命令行: " + pi.CmdLine);
            }
        }
        if (n1 == 0) sb.AppendLine("  （轮询窗口内没有发现控制台程序启动）");

        // 事件追踪独有的（存活太短，轮询没采到）
        HashSet<string> polledPids = new HashSet<string>();
        foreach (ProcInfo pi in AllNew) polledPids.Add(Convert.ToString(pi.Pid));
        int n2 = 0;
        foreach (string[] ev in TraceAll)
        {
            if (string.Equals(ev[1], selfName, StringComparison.OrdinalIgnoreCase)) continue;
            if (polledPids.Contains(ev[2])) continue;
            bool console = IsConsoleName(ev[1]);
            if (!console) continue;
            if (n2 == 0)
            {
                sb.AppendLine();
                sb.AppendLine("  ★ 仅被事件追踪捕获（存活太短，轮询漏采 —— 这就是「一闪而过」的形态）:");
            }
            n2++;
            sb.AppendLine(string.Format("  [{0}] {1}  pid={2}  父={3}",
                ev[0].Substring(11), ev[1], ev[2], ev[4].Length == 0 ? "pid" + ev[3] : ev[4]));
        }
        if (n2 == 0 && !traceStarted)
            sb.AppendLine("  （事件追踪不可用，无法排除 <50ms 的极短进程）");
        sb.AppendLine();

        // 2) 全部新进程
        sb.AppendLine("--- 二、本次采集窗口内新建的进程（按时间） ---");
        foreach (ProcInfo pi in AllNew)
        {
            sb.AppendLine(string.Format("  {0}  {1,-28} pid={2,-6} 存活={3,-10} {4}  父={5}{6}",
                pi.FirstSeen.ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture),
                pi.Name, pi.Pid, LifeText(pi), SubText(pi.Sub).PadRight(4),
                (NameOf(pi.Ppid).Length == 0 ? "pid" + pi.Ppid : NameOf(pi.Ppid)),
                pi.Self ? "  [本工具自身]" : ""));
        }
        if (AllNew.Count == 0) sb.AppendLine("  （无）");
        sb.AppendLine();

        // 3) 自启项是否真的跑起来了
        sb.AppendLine("--- 三、自启项对照：哪些真的跑起来了 ---");
        foreach (string[] it in InvItems)
        {
            string exe = ExeOf(it[2]);
            if (exe.Length == 0) continue;
            string want = BaseName(exe);
            ProcInfo hit = null;
            foreach (ProcInfo pi in AllNew)
            {
                if (MatchProc(pi, exe, want)) { hit = pi; break; }
            }
            if (hit == null)
            {
                foreach (ProcInfo pi in PreList)
                {
                    if (MatchProc(pi, exe, want)) { hit = pi; break; }
                }
                if (hit != null)
                {
                    sb.AppendLine(string.Format("  [{0}] {1}  ({2})  ->  启动于本工具之前（start={3}）  存活={4}  {5}",
                        it[3], it[1], want,
                        hit.Started == DateTime.MinValue ? "?" : hit.Started.ToString("HH:mm:ss", CultureInfo.InvariantCulture),
                        LifeText(hit), SubText(hit.Sub)));
                    continue;
                }
            }
            string result;
            if (hit != null)
            {
                result = string.Format("已启动  {0}  pid={1}  存活={2}  {3}",
                    hit.FirstSeen.ToString("HH:mm:ss", CultureInfo.InvariantCulture), hit.Pid,
                    LifeText(hit), SubText(hit.Sub));
            }
            else
            {
                result = "未观察到";
            }
            sb.AppendLine(string.Format("  [{0}] {1}  ({2})  ->  {3}",
                it[3], it[1], want, result));
        }
        sb.AppendLine();

        // 4) 原始自启项清单
        sb.AppendLine("--- 四、自启项清单（原始） ---");
        foreach (string[] it in InvItems)
        {
            sb.AppendLine(string.Format("  [{0}] {1}  [{2}]", it[0], it[1], it[3]));
            sb.AppendLine("        " + it[2]);
        }
        sb.AppendLine();
        sb.AppendLine("=== 报告结束 ===");

        try
        {
            File.WriteAllText(txtPath, sb.ToString(), new UTF8Encoding(false));
            // 固定别名，方便直接打开最近一次报告
            string dir = Path.GetDirectoryName(txtPath);
            if (dir != null && dir.Length > 0)
            {
                try { File.Copy(txtPath, Path.Combine(dir, "latest.txt"), true); } catch { }
                try { if (tsvPath != null) File.Copy(tsvPath, Path.Combine(dir, "latest.tsv"), true); } catch { }
            }
        }
        catch { }
        Line("RPT", "txt=" + txtPath);
    }

    private static string LifeText(ProcInfo pi)
    {
        if (!pi.Ended) return "仍在运行";
        long ms = (long)(pi.EndedAt - pi.FirstSeen).TotalMilliseconds;
        if (ms < 1000) return ms + "ms";
        return (ms / 1000.0).ToString("0.0", CultureInfo.InvariantCulture) + "s";
    }

    private static bool MatchProc(ProcInfo pi, string full, string want)
    {
        if (pi.Path.Length > 0 && string.Equals(pi.Path, full, StringComparison.OrdinalIgnoreCase)) return true;
        if (pi.Name.Length > 0 && want.Length > 0 &&
            string.Equals(pi.Name, want, StringComparison.OrdinalIgnoreCase)) return true;
        if (pi.CmdLine.Length > 0 && full.Length > 0 &&
            pi.CmdLine.IndexOf(full, StringComparison.OrdinalIgnoreCase) >= 0) return true;
        return false;
    }

    private static string BaseName(string path)
    {
        try { return Path.GetFileName(path); }
        catch { return path; }
    }

    private static string ExeOf(string cmd)
    {
        if (cmd == null || cmd.Length == 0) return "";
        Match m = Regex.Match(cmd, "\"([^\"]+\\.(exe|cmd|bat|vbs|ps1|com|lnk))\"", RegexOptions.IgnoreCase);
        if (m.Success) return m.Groups[1].Value;
        m = Regex.Match(cmd, "([A-Za-z]:\\\\[^\\s\"]+\\.(exe|cmd|bat|vbs|ps1|com|lnk))", RegexOptions.IgnoreCase);
        if (m.Success) return m.Groups[1].Value;
        m = Regex.Match(cmd, "([^\\s\\\\/]+\\.(exe|cmd|bat|vbs|ps1|com|lnk))", RegexOptions.IgnoreCase);
        if (m.Success) return m.Groups[1].Value;
        return "";
    }
}
