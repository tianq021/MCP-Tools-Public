using System;
using System.Collections.Generic;
using System.Linq;
using Controllers.Time;
using EventsSystem;
using EventsSystem.Actions;
using EventsSystem.Conditions;
using EventsSystem.Triggers;
using Newtonsoft.Json.Linq;
using Serialization;

namespace IfzMcp;

/// <summary>
/// 事件系统（EventsSystem）查询与手动触发。
/// 事件定义在游戏内以 JSON 资源存在，运行时由 EventsLoader 惰性加载；
/// 本文件暴露：全部事件目录、单事件详情（触发器/条件/动作）、强制启动指定事件。
/// </summary>
public sealed partial class Plugin
{
    private static EventsSystemController EventController() => WorldContainer().Resolve<EventsSystemController>();

    private static bool EventInPool(EventsSystemController controller, string id) =>
        controller.EventsPool.Any(item => item != null && string.Equals(item.Id, id, StringComparison.Ordinal));

    private static bool EventActive(EventsSystemController controller, string id) =>
        controller.ActiveEvents.Any(item => item != null && string.Equals(item.Id, id, StringComparison.Ordinal));

    private static bool EventAppeared(string id) =>
        SaveHandler.SaveData.EventsSystemSerializedData.IsEventAppeared(id);

    private static string[] TriggerTypes(EventDraft draft) =>
        draft.triggers?.Where(item => item != null).Select(item => item.triggerType.ToString()).ToArray()
        ?? Array.Empty<string>();

    private static string[] ActionTypes(EventDraft draft) =>
        draft.actionsOnStart?.Where(item => item != null).Select(item => item.actionType.ToString()).ToArray()
        ?? Array.Empty<string>();

    private static object[] ConditionRows(EventDraft draft)
    {
        var rows = new List<object>();
        if (draft.conditionsExpressions == null) return rows.ToArray();
        foreach (var expression in draft.conditionsExpressions)
        {
            if (expression == null) continue;
            var conditions = expression.conditionsToMet?
                .Where(item => item != null)
                .Select(item => new
                {
                    id = item.Id,
                    type = item.conditionType.ToString(),
                    negation = item.negation
                })
                .ToArray() ?? Array.Empty<object>();
            rows.Add(new { math_operator = expression.mathOperator.ToString(), conditions });
        }
        return rows.ToArray();
    }

    private static object[] TriggerRows(EventDraft draft)
    {
        if (draft.triggers == null) return Array.Empty<object>();
        return draft.triggers.Where(item => item != null).Select(item => (object)new
        {
            id = item.Id,
            type = item.triggerType.ToString(),
            chance_to_start = item.chanceToStart,
            marker_id = item.markerId,
            success_condition_id = item.successConditionId,
            min_distance = item.minDistance,
            max_distance = item.maxDistance,
            min_space = item.minSpace,
            resource_id_to_find = item.resourceIdToFind.ToString(),
            numeric_value = item.numericalCondition.numericValue,
            numeric_sign = item.numericalCondition.mathSign.ToString(),
            ignore_hideouts = item.ignoreHideouts,
            ignore_lairs = item.ignoreLairs,
            zone_type = item.zoneType.ToString()
        }).ToArray();
    }

    private static object[] ActionRows(EventDraft draft)
    {
        if (draft.actionsOnStart == null) return Array.Empty<object>();
        return draft.actionsOnStart.Where(item => item != null).Select(item => (object)new
        {
            id = item.Id,
            type = item.actionType.ToString(),
            transmission_id = item.transmissionId,
            event_id = item.eventId,
            mission_id = item.missionId,
            marker_id = item.markerId,
            action_to_run_id = item.actionToRunId,
            mechanic_id = item.mechanicId
        }).ToArray();
    }

    private static EventDraft LoadEventDraft(EventsSystemController controller, string eventId) =>
        controller._eventsLoader.GetEventById(eventId);

    private object ReadEventsCatalog(JObject args)
    {
        var controller = EventController();
        var query = (string)args["query"];
        var status = (string)args["status"] ?? "all";
        var limit = Math.Min(Math.Max((int?)args["limit"] ?? 200, 1), 500);
        var offset = Math.Max((int?)args["offset"] ?? 0, 0);

        var names = EventsSystemSerializer.GetEventNames() ?? new List<string>();
        var matched = new List<object>();
        var total = 0;
        foreach (var id in names)
        {
            if (string.IsNullOrEmpty(id)) continue;
            if (!string.IsNullOrEmpty(query) && id.IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0) continue;
            var pool = EventInPool(controller, id);
            var active = EventActive(controller, id);
            var appeared = EventAppeared(id);
            var pass = status switch
            {
                "pool" => pool,
                "active" => active,
                "appeared" => appeared,
                "not_appeared" => !appeared,
                _ => true
            };
            if (!pass) continue;
            total++;
            if (total <= offset) continue;
            if (matched.Count >= limit) continue;
            var draft = LoadEventDraft(controller, id);
            if (draft == null)
            {
                matched.Add(new { id, in_pool = pool, active, appeared, loaded = false });
                continue;
            }
            matched.Add(new
            {
                id = draft.Id,
                in_pool = pool,
                active,
                appeared,
                loaded = true,
                story_event = draft.storyEvent,
                tutorial_event = draft.tutorialEvent,
                slots_cost = draft.SlotsCost,
                limited = draft.Limited,
                run_multiple_times = draft.RunMultipleTimes,
                without_triggers = draft.EventWithoutTriggers,
                trigger_types = TriggerTypes(draft),
                action_types = ActionTypes(draft),
                comment = draft.comment
            });
        }
        return new
        {
            total_known = names.Count,
            total_matched = total,
            offset,
            limit,
            truncated = total > offset + matched.Count,
            items = matched
        };
    }

    private object ReadEventDetail(JObject args)
    {
        var eventId = Text(args, "event_id", 120);
        var controller = EventController();
        var draft = LoadEventDraft(controller, eventId);
        if (draft == null)
            throw new BridgeException("event_not_found", "No event definition with that ID. Read get_events_catalog first.");
        return new
        {
            id = draft.Id,
            in_pool = EventInPool(controller, eventId),
            active = EventActive(controller, eventId),
            appeared = EventAppeared(eventId),
            story_event = draft.storyEvent,
            tutorial_event = draft.tutorialEvent,
            slots_cost = draft.SlotsCost,
            limited = draft.Limited,
            run_multiple_times = draft.RunMultipleTimes,
            without_triggers = draft.EventWithoutTriggers,
            transfer_to_new_zone_pool = draft.transferToNewZonePoolList,
            transfer_to_new_zone_active = draft.transferToNewZoneActiveList,
            blocking_new_zone_pool = draft.blockingNewZoneIfOnPoolList,
            blocking_new_zone_active = draft.blockingNewZoneIfOnActiveList,
            comment = draft.comment,
            conditions = ConditionRows(draft),
            triggers = TriggerRows(draft),
            actions_on_start = ActionRows(draft)
        };
    }

    private object StartEventById(JObject args)
    {
        var eventId = Text(args, "event_id", 120);
        var controller = EventController();
        var draft = LoadEventDraft(controller, eventId);
        if (draft == null)
            throw new BridgeException("event_not_found", "No event definition with that ID. Read get_events_catalog first.");
        if (EventActive(controller, eventId) && !draft.RunMultipleTimes)
            throw new BridgeException("event_already_active", "That event is already active and is not repeatable.");
        controller.StartEventId(eventId);
        return new
        {
            event_id = eventId,
            active_after = EventActive(controller, eventId),
            in_pool_after = EventInPool(controller, eventId),
            active_events = controller.ActiveEvents.Count
        };
    }
}
