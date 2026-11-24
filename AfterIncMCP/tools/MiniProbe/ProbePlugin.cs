using BepInEx;
using BepInEx.Unity.IL2CPP;
using System.Net;
using System.Text;
using System.Threading.Tasks;

namespace AfterIncProbe;

[BepInPlugin("local.afterinc.probe", "After Inc Minimal Probe", "0.1.0")]
public sealed class ProbePlugin : BasePlugin
{
    private HttpListener? listener;

    public override void Load()
    {
        Log.LogInfo("probe: Load() entered");
        listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:17841/");
        listener.Start();
        _ = Task.Run(async () =>
        {
            while (listener.IsListening)
            {
                try
                {
                    var context = await listener.GetContextAsync();
                    var bytes = Encoding.UTF8.GetBytes("probe-ok");
                    context.Response.ContentLength64 = bytes.Length;
                    await context.Response.OutputStream.WriteAsync(bytes);
                    context.Response.Close();
                }
                catch { break; }
            }
        });
        Log.LogInfo("probe: listening on 127.0.0.1:17841");
    }

    public override bool Unload()
    {
        listener?.Close();
        return true;
    }
}
