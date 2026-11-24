extern alias FoundationAssembly;
using FoundationAssembly::com.ndemiccreations.foundation;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using UObj = UnityEngine.Object;
using Hits = Il2CppSystem.Collections.Generic.List<UnityEngine.EventSystems.RaycastResult>;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private sealed record NativeButton(string id, string name, string label, Action click);
    private static bool OnScreen(Component component, out Vector2 point)
    {
        point = default;
        var rt = component.transform.TryCast<RectTransform>();
        var canvas = component.GetComponentInParent<Canvas>();
        if (rt == null || canvas == null || !canvas.isActiveAndEnabled) return false;
        var root = canvas.rootCanvas;
        var cam = root.renderMode == RenderMode.ScreenSpaceOverlay ? null : root.worldCamera;
        point = RectTransformUtility.WorldToScreenPoint(cam, rt.TransformPoint(rt.rect.center));
        return point.x >= 0 && point.y >= 0 && point.x < Screen.width && point.y < Screen.height;
    }

    private static bool Hit(Behaviour button, out Vector2 point)
    {
        if (!OnScreen(button, out point) || EventSystem.current == null) return false;
        var pointer = new PointerEventData(EventSystem.current) { position = point };
        var hits = new Hits();
        EventSystem.current.RaycastAll(pointer, hits);
        if (hits.Count == 0 || hits[0].gameObject == null) return false;
        var top = hits[0].gameObject.transform;
        return top.IsChildOf(button.transform);
    }

    private IEnumerable<NativeButton> VisibleButtons()
    {
        var seen = new HashSet<int>();
        foreach (var b in UObj.FindObjectsOfType<InputEventsButton>()) {
            if (!b.isActiveAndEnabled || !b.interactable || b.onClick == null || !Hit(b, out _)) continue;
            seen.Add(b.gameObject.GetInstanceID());
            yield return DescribeButton(b, b.onClick.Invoke);
        }
        foreach (var b in UObj.FindObjectsOfType<Button>()) {
            if (!b.isActiveAndEnabled || !b.IsInteractable() || seen.Contains(b.gameObject.GetInstanceID()) || !Hit(b, out _)) continue;
            yield return DescribeButton(b, b.onClick.Invoke);
        }
    }

    private NativeButton DescribeButton(Component button, Action click)
    {
        var parts = button.GetComponentsInChildren<TMP_Text>().Where(t => t.isActiveAndEnabled && !string.IsNullOrWhiteSpace(t.text))
            .Select(t => t.text.Trim()).Distinct();
        var label = string.Join(" ", parts);
        var name = button.gameObject.name;
        var id = Hash(new { session, instance = button.GetInstanceID(), name, label });
        return new NativeButton(id, name, label, click);
    }

    private (object view, string revision, List<NativeButton> buttons) ReadControls()
    {
        var buttons = VisibleButtons().OrderBy(b => b.id).ToList();
        var items = buttons.Select(b => new { control_id = b.id, b.name, b.label }).ToList();
        var labels = new List<string>();
        foreach (var t in UObj.FindObjectsOfType<TextMeshProUGUI>()) {
            if (!t.isActiveAndEnabled || string.IsNullOrWhiteSpace(t.text) || t.canvasRenderer.cull ||
                t.canvasRenderer.GetInheritedAlpha() <= 0.01f || !OnScreen(t, out _)) continue;
            labels.Add(t.text.Trim());
        }
        var scene = SceneManager.GetActiveScene().name;
        labels.Sort(StringComparer.Ordinal);
        var revision = Hash(new { session, scene, items, labels });
        return (new { scene, revision, controls = items, screen_text = labels.Distinct() }, revision, buttons);
    }

    private object ClickControl(Work work)
    {
        RequireOwner(work);
        var snapshot = ReadControls();
        if (work.Args.GetProperty("expected_revision").GetString() != snapshot.revision)
            throw new BridgeError("STALE_STATE", "Read current game controls again.");
        var id = work.Args.GetProperty("control_id").GetString();
        var button = snapshot.buttons.SingleOrDefault(b => b.id == id);
        if (button == null) throw new BridgeError("CONTROL_UNAVAILABLE", "The control is no longer visible and interactive.");
        button.click();
        return new { submitted = true, control_id = id, label = button.label };
    }
}
