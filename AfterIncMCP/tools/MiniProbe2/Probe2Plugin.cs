extern alias GameAssembly;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using System.Net;
using System.Text;
using System.Threading.Tasks;

namespace AfterIncProbe2;

[BepInPlugin("local.afterinc.probe2", "After Inc Type Probe", "0.1.0")]
public sealed class Probe2Plugin : BasePlugin
{
    private HttpListener? listener;

    public override void Load()
    {
        // Touch a game type from the generated interop assembly only (no instances, no MonoBehaviour).
        var type = typeof(GameAssembly::com.ndemiccreations.survivors.GameController);
        Log.LogInfo("probe2: resolved game type " + type.FullName);
        listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:17842/");
        listener.Start();
        _ = Task.Run(async () =>
        {
            while (listener.IsListening)
            {
                try
                {
                    var context = await listener.GetContextAsync();
                    var bytes = Encoding.UTF8.GetBytes("probe2-ok " + type.Name);
                    context.Response.ContentLength64 = bytes.Length;
                    await context.Response.OutputStream.WriteAsync(bytes);
                    context.Response.Close();
                }
                catch { break; }
            }
        });
        Log.LogInfo("probe2: listening on 127.0.0.1:17842");
    }

    public override bool Unload()
    {
        listener?.Close();
        return true;
    }
}
