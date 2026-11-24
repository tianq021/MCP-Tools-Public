using BepInEx;
using BepInEx.Unity.IL2CPP;
using System.Net;
using System.Text;
using UnityEngine;

namespace AfterIncProbe3;

[BepInPlugin("local.afterinc.probe3", "After Inc Empty Component Probe", "0.1.0")]
public sealed class Probe3Plugin : BasePlugin
{
    private HttpListener? listener;

    public override void Load()
    {
        Log.LogInfo("probe3: Load() entered");

        try
        {
            AddComponent<EmptyProbeBehaviour>();
            Log.LogInfo("probe3: AddComponent<EmptyProbeBehaviour> succeeded");
        }
        catch (Exception e)
        {
            Log.LogError("probe3: AddComponent failed: " + e); throw; throw;
        }

        listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:17843/");
        listener.Start();

        _ = Task.Run(async () =>
        {
            while (listener.IsListening)
            {
                try
                {
                    var context = await listener.GetContextAsync();
                    var bytes = Encoding.UTF8.GetBytes("probe3-ok empty-component");
                    context.Response.ContentLength64 = bytes.Length;
                    await context.Response.OutputStream.WriteAsync(bytes);
                    context.Response.Close();
                }
                catch
                {
                    break;
                }
            }
        });

        Log.LogInfo("probe3: listening on 127.0.0.1:17843");
    }

    public override bool Unload()
    {
        listener?.Close();
        return true;
    }
}

public sealed class EmptyProbeBehaviour : MonoBehaviour
{
    public EmptyProbeBehaviour(IntPtr pointer) : base(pointer) { }
}


