using System;
using DialogueSystem.Runtime.Core;
using Newtonsoft.Json.Linq;
using UnityEngine.Events;

namespace IfzMcp;

public sealed partial class Plugin
{
    private static Guid? assistantCallId;
    private static string assistantTarget;
    private static bool assistantClaimed;
    private static double assistantDeadline;

    private static void AddAssistantOption()
    {
        if (dialogueManager?.DialoguePanel == null) return;
        var panel = dialogueManager.DialoguePanel;
        bool first = RadioOptions(panel).Count == 0;
        AddRadioButton("menu", "呼叫助手", () =>
        {
            panel.ClearDialogOptions();
            AddRadioButton("dsh", "呼叫 DSH", () => RequestAssistant("DSH"));
            AddRadioButton("codex", "呼叫 Codex", () => RequestAssistant("Codex"));
            panel.ShowAnswersButtons(false);
        });
        // The native menu already started its reveal when it had unlocked broadcasts.
        if (first) panel.ShowAnswersButtons(false);
    }

    private static void AddRadioButton(string id, string label, UnityAction action)
    {
        var line = new DialogueLine { ID = "ifz_assistant_" + id, Text = label, _speaker = "operator" };
        dialogueManager.DialoguePanel.CreateDialogueOptionObject(line).AssignActionToBtn(action);
    }

    private static void RequestAssistant(string target)
    {
        radioCanvas.HideTransmissionPanel();
        if (assistantCallId.HasValue)
        {
            AssistantNotice("正在等待 " + assistantTarget + " 回应。");
            return;
        }
        assistantCallId = Guid.NewGuid();
        assistantTarget = target;
        assistantClaimed = false;
        assistantDeadline = Now + 20;
        AssistantNotice("正在呼叫 " + target + "…");
    }

    private static void AssistantNotice(string message)
    {
        hudMessage = message;
        hudMessageUntil = Now + 30;
    }

    private static void ExpireAssistantCall()
    {
        if (!assistantCallId.HasValue || Now < assistantDeadline) return;
        AssistantNotice(assistantClaimed ? assistantTarget + " 回应超时。" : "呼叫超时，请检查 IFZ Runtime 是否连接到本局。");
        assistantCallId = null;
    }

    private object HandleAssistantCall(string method, JObject args)
    {
        if (assistantCallId != Id(args, "request_id"))
            throw new BridgeException("stale_assistant_call", "This radio call is no longer pending.");
        if (method == "claim_assistant_call")
        {
            if (assistantClaimed) throw new BridgeException("already_claimed", "Another receiver claimed this call.");
            assistantClaimed = true;
            assistantDeadline = Now + 150;
        }
        else
        {
            if (!assistantClaimed) throw new BridgeException("unclaimed_call", "Claim the call first.");
            AssistantNotice(assistantTarget + " · " + Text(args, "text", 500));
            assistantCallId = null;
        }
        return new { target = assistantTarget };
    }
}
