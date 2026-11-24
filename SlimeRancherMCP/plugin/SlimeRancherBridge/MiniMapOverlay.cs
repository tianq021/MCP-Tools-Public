using System.Collections.Generic;
using MonomiPark.SlimeRancher.Regions;
using UnityEngine;
using UnityEngine.UI;

namespace SlimeRancherBridge
{
    public sealed class MiniMapOverlay : MonoBehaviour
    {
        const float PanelSize = 235f;
        const float MapScale = 0.60f;

        // The two coordinate systems used by MapUI in Slime Rancher 1.4.4.
        static readonly Vector2 HomeWorld = new Vector2(89.3f, -144.5f);
        static readonly Vector2 HomeMap = new Vector2(2468f, -2532f);
        static readonly Vector2 HomeSlope = new Vector2(187f / 104.5f, 209f / 116.3f);
        static readonly Vector2 DesertWorld = new Vector2(119.4345f, 918.0937f);
        static readonly Vector2 DesertMap = new Vector2(4219f, -2497f);
        static readonly Vector2 DesertSlope = new Vector2(214f / -132.12832f, 812f / -501.6654f);

        readonly Dictionary<Image, ZoneDirector.Zone> fogLayers = new Dictionary<Image, ZoneDirector.Zone>();
        readonly Vector3[] worldCorners = new Vector3[4];
        Canvas overlay;
        RectTransform viewport;
        RectTransform mapArt;
        RectTransform marker;
        MapUI nativeMap;
        PlayerDisplayOnMap nativePlayerMarker;
        Vector2 markerOrigin;
        float nextBind;
        bool visible = true;

        void LateUpdate()
        {
            SceneContext scene = SceneContext.Instance;
            if (scene == null || scene.Player == null || scene.PlayerState == null || scene.PlayerZoneTracker == null
                    || scene.TimeDirector == null || scene.RegionRegistry == null)
            {
                SetVisible(false);
                return;
            }

            HudUI hud = HudUI.Instance;
            Map map = Map.Instance;
            MapUI ui = map == null ? null : map.mapUI;
            if (hud == null || !hud.isActiveAndEnabled || hud.uiContainer == null || !hud.uiContainer.activeInHierarchy
                    || ui == null || ui.gameObject.activeInHierarchy || ChatOverlay.IsOpen || scene.TimeDirector.HasPauser())
            {
                SetVisible(false);
                return;
            }

            if (Input.GetKeyDown(KeyCode.F8))
                visible = !visible;

            RegionRegistry.RegionSetId region = scene.RegionRegistry.GetCurrentRegionSetId();
            bool desert = region == RegionRegistry.RegionSetId.DESERT;
            ZoneDirector.Zone zone = scene.PlayerZoneTracker.GetCurrentZone();
            if (!visible || (!desert && region != RegionRegistry.RegionSetId.HOME)
                    || !scene.PlayerState.HasUnlockedMap(zone))
            {
                SetVisible(false);
                return;
            }

            if (nativeMap != ui || nativePlayerMarker == null)
            {
                SetVisible(false);
                if (Time.unscaledTime < nextBind)
                    return;
                nextBind = Time.unscaledTime + 1f;
                if (!Bind(ui, scene.Player))
                    return;
            }

            if (nativePlayerMarker.IsInUnknownArea())
            {
                SetVisible(false);
                return;
            }

            Transform player = scene.Player.transform;
            Vector3 position = player.position;
            Vector2 slope = desert ? DesertSlope : HomeSlope;
            Vector2 center = (desert ? DesertMap : HomeMap) + markerOrigin
                + Vector2.Scale(new Vector2(position.x, position.z) - (desert ? DesertWorld : HomeWorld), slope);
            mapArt.anchoredPosition = -center * MapScale;

            Vector3 forward = player.forward;
            float angle = Mathf.Atan2(-forward.x * slope.x, forward.z * slope.y) * Mathf.Rad2Deg;
            marker.localRotation = Quaternion.Euler(0f, 0f, angle);
            foreach (KeyValuePair<Image, ZoneDirector.Zone> layer in fogLayers)
            {
                bool locked = !scene.PlayerState.HasUnlockedMap(layer.Value);
                if (layer.Key.enabled != locked)
                    layer.Key.enabled = locked;
            }
            SetVisible(true);
        }

        bool Bind(MapUI ui, GameObject player)
        {
            if (ui.mapContentArea == null || ui.mapMarkerSection == null)
                return false;
            Transform tiles = ui.mapContentArea.transform.Find("MapQuads");
            var section = ui.mapMarkerSection.GetComponent<RectTransform>();
            var display = player.GetComponent<PlayerDisplayOnMap>();
            var markerPrefab = display == null || display.markerPrefab == null
                ? null : display.markerPrefab.GetComponent<RectTransform>();
            if (tiles == null || section == null || markerPrefab == null
                    || section.rect.width <= 0f || section.rect.height <= 0f)
                return false;

            if (overlay == null)
                CreateOverlay();
            if (mapArt != null)
                Destroy(mapArt.gameObject);
            fogLayers.Clear();
            mapArt = ChildRect(viewport, "Map artwork", Vector2.one, new Vector2(0.5f, 0.5f));
            mapArt.localScale = new Vector3(MapScale, MapScale, 1f);
            mapArt.SetAsFirstSibling();

            int tileCount = 0;
            foreach (Image tile in tiles.GetComponentsInChildren<Image>(true))
            {
                if (CopyArtwork(tile, section) != null)
                    ++tileCount;
            }
            if (tileCount == 0)
                return false;

            if (ui.zoneFogEntries != null)
            {
                foreach (MapUI.ZoneFogEntry fog in ui.zoneFogEntries)
                {
                    if (fog.fogObject == null)
                        continue;
                    foreach (Image source in fog.fogObject.GetComponentsInChildren<Image>(true))
                    {
                        Image layer = CopyArtwork(source, section);
                        if (layer != null)
                            fogLayers.Add(layer, fog.zoneId);
                    }
                }
            }

            Rect rect = section.rect;
            Vector2 anchor = markerPrefab.anchorMin + Vector2.Scale(markerPrefab.anchorMax - markerPrefab.anchorMin, markerPrefab.pivot);
            markerOrigin = rect.min + Vector2.Scale(rect.size, anchor);
            nativePlayerMarker = display;
            nativeMap = ui;
            Debug.Log("[SlimeRancherBridge] MiniMapOverlay ready, tiles=" + tileCount + ", fog=" + fogLayers.Count);
            return true;
        }

        Image CopyArtwork(Image source, RectTransform section)
        {
            Sprite sprite = source.overrideSprite;
            if (sprite == null)
                return null;
            source.rectTransform.GetWorldCorners(worldCorners);
            Vector2 bottomLeft = section.InverseTransformPoint(worldCorners[0]);
            Vector2 topRight = section.InverseTransformPoint(worldCorners[2]);
            Vector2 size = topRight - bottomLeft;
            if (size.x <= 0f || size.y <= 0f)
                return null;

            RectTransform target = ChildRect(mapArt, source.gameObject.name, size, new Vector2(0.5f, 0.5f));
            target.anchoredPosition = (bottomLeft + topRight) * 0.5f;

            Image copy = target.gameObject.AddComponent<Image>();
            copy.sprite = sprite;
            copy.type = source.type;
            copy.preserveAspect = source.preserveAspect;
            copy.fillCenter = source.fillCenter;
            copy.color = source.color;
            copy.raycastTarget = false;
            return copy;
        }

        void CreateOverlay()
        {
            overlay = GetComponent<Canvas>();
            overlay.renderMode = RenderMode.ScreenSpaceOverlay;
            overlay.sortingOrder = 20;
            overlay.enabled = false;
            CanvasScaler scaler = GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 1f;

            Vector2 upperRight = new Vector2(1f, 1f);
            RectTransform frame = ChildRect(transform, "Frame", Vector2.one * PanelSize, upperRight);
            frame.pivot = upperRight;
            frame.anchoredPosition = new Vector2(-18f, -47f);
            Fill(frame, new Color(0.10f, 0.16f, 0.21f, 0.96f));

            Vector2 middle = new Vector2(0.5f, 0.5f);
            viewport = ChildRect(frame, "Viewport", Vector2.one * (PanelSize - 6f), middle);
            viewport.gameObject.AddComponent<RectMask2D>();
            Fill(viewport, new Color(0.06f, 0.12f, 0.19f, 1f));
            marker = ChildRect(viewport, "Player direction", new Vector2(24f, 28f), middle);
            marker.gameObject.AddComponent<MiniMapPointer>().raycastTarget = false;
        }

        static RectTransform ChildRect(Transform parent, string name, Vector2 size, Vector2 anchor)
        {
            var rect = (RectTransform)new GameObject(name, typeof(RectTransform)).transform;
            rect.SetParent(parent, false);
            rect.anchorMin = anchor;
            rect.anchorMax = anchor;
            rect.sizeDelta = size;
            rect.anchoredPosition = Vector2.zero;
            return rect;
        }

        static void Fill(RectTransform rect, Color color)
        {
            Image image = rect.gameObject.AddComponent<Image>();
            image.color = color;
            image.raycastTarget = false;
        }

        void SetVisible(bool show)
        {
            if (overlay != null && overlay.enabled != show)
                overlay.enabled = show;
        }
    }

    public sealed class MiniMapPointer : MaskableGraphic
    {
        protected override void OnPopulateMesh(VertexHelper vertices)
        {
            vertices.Clear();
            AddArrow(vertices, 1f, new Color32(15, 34, 46, 255));
            AddArrow(vertices, 0.70f, new Color32(218, 254, 255, 255));
        }

        static void AddArrow(VertexHelper vertices, float scale, Color32 color)
        {
            int first = vertices.currentVertCount;
            vertices.AddVert(new Vector3(0f, 13f) * scale, color, Vector2.zero);
            vertices.AddVert(new Vector3(-10f, -9f) * scale, color, Vector2.zero);
            vertices.AddVert(new Vector3(0f, -5f) * scale, color, Vector2.zero);
            vertices.AddVert(new Vector3(10f, -9f) * scale, color, Vector2.zero);
            vertices.AddTriangle(first, first + 1, first + 2);
            vertices.AddTriangle(first, first + 2, first + 3);
        }
    }
}
