// csc /target:winexe /r:System.Management.dll /out:BootCapture.exe BootCapture.cs
// Boot/session capture only. Existing StartupWatch remains available for inventories.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class BootCapture
{
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    private struct Entry {
        public uint size, usage, pid; public IntPtr heap;
        public uint module, threads, parent; public int priority; public uint flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)] public string name;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool Process32First(IntPtr h, ref Entry e);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool Process32Next(IntPtr h, ref Entry e);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    private static readonly object gate = new object();
    private static readonly Queue<string[]> pending = new Queue<string[]>();
    private static readonly Queue<string[]> baselineDetails = new Queue<string[]>();
    private static StreamWriter log;
    private static volatile bool stopping, tracing;
    private static int starts, stops, polls, dropped;
    private static DateTime desktop = DateTime.MinValue;
    private static string output, prefix;

    static string Clean(object x) { return Convert.ToString(x, CultureInfo.InvariantCulture).Replace("\t", " ").Replace("\r", " ").Replace("\n", " "); }
    static void Row(params object[] items) {
        lock (gate) {
            if (log == null) return;
            StringBuilder b = new StringBuilder(DateTime.UtcNow.ToString("o"));
            foreach (object item in items) b.Append('\t').Append(Clean(item));
            log.WriteLine(b.ToString());
        }
    }
    static void Enqueue(string pid, string name, string time, string source) {
        lock(pending) {
            if (pending.Count + baselineDetails.Count >= 2048) { Interlocked.Increment(ref dropped); return; }
            (source=="BASE" ? baselineDetails : pending).Enqueue(new string[] {pid,name,time,source});
        }
    }
    static void DetailWorker() {
        while (!stopping) {
            string[] item=null;
            lock(pending) {
                if(pending.Count>0) item=pending.Dequeue();
                else if(baselineDetails.Count>0) item=baselineDetails.Dequeue();
            }
            if(item==null) { Thread.Sleep(20); continue; }
            try {
                EnumerationOptions options=new EnumerationOptions();
                options.Timeout=TimeSpan.FromSeconds(2);
                using(ManagementObjectSearcher s=new ManagementObjectSearcher("root\\cimv2",
                    "SELECT ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,SessionId,CreationDate FROM Win32_Process WHERE ProcessId="+item[0],options)) {
                    bool found=false;
                    foreach(ManagementObject p in s.Get()) {
                        using(p) {
                            found=true;
                            DateTime created=ManagementDateTimeConverter.ToDateTime(Convert.ToString(p["CreationDate"])).ToUniversalTime();
                            DateTime observed=DateTime.Parse(item[2],null,DateTimeStyles.RoundtripKind).ToUniversalTime();
                            // A later process may have reused the PID before enrichment ran.
                            if(created>observed.AddSeconds(1) || !String.Equals(item[1],Convert.ToString(p["Name"]),StringComparison.OrdinalIgnoreCase)) {
                                Row("DETAIL_SKIPPED_PID_REUSED",item[0],item[1]); continue;
                            }
                            Row("DETAIL",item[0],p["Name"],"ppid="+p["ParentProcessId"],"session="+p["SessionId"],
                                "created="+created.ToString("o"),"path="+p["ExecutablePath"],"cmd="+p["CommandLine"],"source="+item[3]);
                            if(String.Equals(item[1],"explorer.exe",StringComparison.OrdinalIgnoreCase) && Convert.ToInt32(p["SessionId"])>0) {
                                lock(gate) { if(desktop==DateTime.MinValue) { desktop=DateTime.UtcNow; Row("DESKTOP_SEEN",p["SessionId"]); } }
                            }
                        }
                    }
                    if(!found) Row("DETAIL_UNAVAILABLE_EXITED",item[0],item[1]);
                }
            } catch(Exception ex) { Row("DETAIL_ERROR",item[0],ex.Message); }
        }
    }
    static void TraceWorker() {
        while(!stopping) {
            ManagementEventWatcher start=null,stop=null;
            try {
                start=new ManagementEventWatcher(new WqlEventQuery("SELECT * FROM Win32_ProcessStartTrace"));
                start.EventArrived+=delegate(object sender,EventArrivedEventArgs args) {
                    try {
                        ManagementBaseObject p=args.NewEvent;
                        string time=DateTime.FromFileTimeUtc(Convert.ToInt64(p["TIME_CREATED"])).ToString("o");
                        Row("START",p["ProcessID"],p["ProcessName"],"ppid="+p["ParentProcessID"],"session="+p["SessionID"],"event="+time);
                        Interlocked.Increment(ref starts);
                        Enqueue(Convert.ToString(p["ProcessID"]),Convert.ToString(p["ProcessName"]),time,"event");
                    } catch(Exception ex) { Row("EVENT_ERROR",ex.Message); }
                };
                start.Start(); tracing=true; Row("TRACE_START_READY");
                try {
                    stop=new ManagementEventWatcher(new WqlEventQuery("SELECT * FROM Win32_ProcessStopTrace"));
                    stop.EventArrived+=delegate(object sender,EventArrivedEventArgs args) {
                        try { ManagementBaseObject p=args.NewEvent;
                            Row("STOP",p["ProcessID"],p["ProcessName"],"session="+p["SessionID"],"event="+DateTime.FromFileTimeUtc(Convert.ToInt64(p["TIME_CREATED"])).ToString("o"));
                            Interlocked.Increment(ref stops);
                        } catch(Exception ex) { Row("EVENT_ERROR",ex.Message); }
                    };
                    stop.Start(); Row("TRACE_STOP_READY");
                } catch(Exception ex) { Row("TRACE_STOP_ERROR",ex.Message); }
                while(!stopping) Thread.Sleep(200);
            } catch(Exception ex) { tracing=false; Row("TRACE_ERROR_RETRY",ex.Message); }
            finally {
                if(start!=null) { try { start.Stop(); start.Dispose(); } catch {} }
                if(stop!=null) { try { stop.Stop(); stop.Dispose(); } catch {} }
            }
            if(!stopping) Thread.Sleep(5000);
        }
    }
    static Dictionary<uint,Entry> Snapshot() {
        Dictionary<uint,Entry> result=new Dictionary<uint,Entry>();
        IntPtr h=CreateToolhelp32Snapshot(2,0);
        if(h==new IntPtr(-1)) { Row("SNAPSHOT_ERROR",Marshal.GetLastWin32Error()); return null; }
        try {
            Entry e=new Entry(); e.size=(uint)Marshal.SizeOf(typeof(Entry));
            if(!Process32First(h,ref e)) { Row("FIRST_ERROR",Marshal.GetLastWin32Error()); return null; }
            do { result[e.pid]=e; e.size=(uint)Marshal.SizeOf(typeof(Entry)); } while(Process32Next(h,ref e));
            int err=Marshal.GetLastWin32Error();
            if(err!=18) { Row("NEXT_ERROR",err,"partial="+result.Count); return null; }
        } finally { CloseHandle(h); }
        return result;
    }
    static int Main(string[] args) {
        int seconds=0;
        output=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"boot-logs");
        for(int i=0;i<args.Length;i++) {
            if(args[i]=="--seconds" && i+1<args.Length) seconds=int.Parse(args[++i]);
            else if(args[i]=="--out" && i+1<args.Length) output=args[++i];
        }
        try {
            Directory.CreateDirectory(output);
            prefix="boot-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Process.GetCurrentProcess().Id;
            log=new StreamWriter(new FileStream(Path.Combine(output,prefix+".tsv"),FileMode.CreateNew,FileAccess.Write,FileShare.ReadWrite),new UTF8Encoding(false));
            log.AutoFlush=true;
            DateTime began=DateTime.UtcNow;
            Row("HEADER","version=1","user="+Environment.UserDomainName+"\\"+Environment.UserName,"session="+Process.GetCurrentProcess().SessionId,"pid="+Process.GetCurrentProcess().Id);
            Thread detail=new Thread(DetailWorker); detail.IsBackground=true; detail.Start();
            Thread trace=new Thread(TraceWorker); trace.IsBackground=true; trace.Start();
            Dictionary<uint,Entry> previous=new Dictionary<uint,Entry>();
            DateTime nextHealth=began;
            while(true) {
                DateTime now=DateTime.UtcNow;
                if(seconds>0 && (now-began).TotalSeconds>=seconds) break;
                DateTime seen; lock(gate) seen=desktop;
                if(seconds==0 && ((seen!=DateTime.MinValue && (now-seen).TotalSeconds>=180) || (now-began).TotalMinutes>=30)) break;
                Dictionary<uint,Entry> current=Snapshot();
                if(current!=null) {
                    foreach(KeyValuePair<uint,Entry> pair in current) {
                        Entry e=pair.Value;
                        if(!previous.ContainsKey(pair.Key) || previous[pair.Key].name!=e.name) {
                            string kind=polls==0?"BASE":"POLL_START";
                            Row(kind,e.pid,e.name,"ppid="+e.parent);
                            Enqueue(e.pid.ToString(),e.name,now.ToString("o"),kind);
                        }
                    }
                    foreach(KeyValuePair<uint,Entry> pair in previous) if(!current.ContainsKey(pair.Key)) Row("POLL_STOP",pair.Key,pair.Value.name);
                    previous=current;
                }
                if(now>=nextHealth) {
                    Process[] independent=Process.GetProcesses();
                    Row("HEALTH","toolhelp="+previous.Count,"dotnet="+independent.Length,"trace="+tracing,"dropped="+dropped);
                    foreach(Process p in independent) {
                        try { if(p.ProcessName=="explorer" && p.SessionId>0) lock(gate) { if(desktop==DateTime.MinValue) { desktop=now; Row("DESKTOP_SEEN",p.SessionId); } } }
                        catch {} finally { p.Dispose(); }
                    }
                    nextHealth=now.AddSeconds(5);
                }
                polls++; Thread.Sleep(100);
            }
            stopping=true; trace.Join(3000); detail.Join(3000);
            int remaining; lock(pending) remaining=pending.Count+baselineDetails.Count;
            Row("END","start_events="+starts,"stop_events="+stops,"polls="+polls,"pending_details="+remaining,"dropped="+dropped);
            string summary="BootCapture completed\r\nStart events: "+starts+"\r\nStop events: "+stops+"\r\nPolls: "+polls+
                "\r\nTrace enabled: "+tracing+"\r\nPending details: "+remaining+"\r\nDropped details: "+dropped+
                "\r\nRaw log: "+Path.Combine(output,prefix+".tsv")+
                "\r\nUTC timestamps. START/STOP are events; POLL records are approximate.\r\nConsole process creation alone does not prove a visible window.\r\n";
            File.WriteAllText(Path.Combine(output,prefix+".txt"),summary,new UTF8Encoding(false));
            File.WriteAllText(Path.Combine(output,"latest.txt"),summary,new UTF8Encoding(false));
            lock(gate) { log.Dispose(); log=null; }
            return 0;
        } catch(Exception ex) {
            try { File.WriteAllText(Path.Combine(output,"failure.txt"),ex.ToString()); } catch {}
            return 1;
        }
    }
}
