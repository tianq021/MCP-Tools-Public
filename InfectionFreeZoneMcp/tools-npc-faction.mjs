// NPC faction takeover tools: the model reads one co-op NPC faction and may drive it
// (squads, vehicles, staffing, production, construction, strategy). Requires the IFZ Enhanced NPC
// addon 0.2.0+ loaded inside a hosted multiplayer campaign.
import { z } from "zod";

const sessionId = z.string().uuid().describe("Current session_id from get_state");
const factionId = z.string().min(1).max(32).describe("NPC faction id from npc_list_factions");
const lease = z.string().uuid().describe("NPC control lease from npc_acquire_control");
const requestId = z.string().uuid().describe("New UUID per command; reuse it only to retry the same command");
const position = z.object({ x: z.number().finite(), z: z.number().finite() });

export function registerNpcFactionTools({ registerLocal, request, readOnly = false, testCopy = false, readAnnotations = {} }) {
  const read = (name, description, inputSchema = {}) =>
    registerLocal(name, description, inputSchema, (args) => request(name, args), readAnnotations);
  const write = (name, description, inputSchema) => {
    if (readOnly) return;
    registerLocal(name, description, inputSchema, (args) => request(name, args));
  };

  read("npc_status",
    "Check the NPC addon and hosted campaign, selected faction id, and whether the test copy can found a faction via MCP.",
    {});

  read("npc_list_factions",
    "List co-op NPC factions with their ids, founded state, headquarters and takeover eligibility. Read the game's native neutral zone with get_npc_zone.",
    { session_id: sessionId });

  read("npc_get_faction",
    "Read the NPC faction's resources, workers, squads, workplaces, construction, recipes, threats, targets and takeover mode. Supporting builds list owned and nearby discovered unclaimed vehicles. The development copy includes a rolling 24-game-hour food production and consumption report for the current run.",
    { session_id: sessionId, faction_id: factionId });

  if (testCopy) {
    read("npc_get_construction_catalog",
      "List the NPC faction's farm and building-adaptation drafts, including faction unlocks, compatible recipes and fixed farm costs. Filter by draft_id to inspect one draft.",
      { session_id: sessionId, faction_id: factionId, draft_id: z.string().min(1).max(96).optional() });
    read("npc_preview_farmland",
      "Check exact farmland coordinates using native site validation and the NPC's own construction resources. Returns the reason per position and does not claim land.",
      { session_id: sessionId, faction_id: factionId, draft_id: z.string().min(1).max(96),
        positions: z.array(position).min(1).max(24) });
    read("npc_get_research",
      "Read the NPC's own technology states, prerequisites, material costs, progress, unlocks and research centers. Use research_id to select a single technology.",
      { session_id: sessionId, faction_id: factionId, research_id: z.string().min(1).max(96).optional() });
  }

  if (testCopy) write("npc_found_faction",
    "Found an NPC faction in a manual-founding test copy where npc_status.can_found_faction is true. A global acquire_control lease is required. Select existing buildings by ID from get_map_buildings. preview=true validates the whole plan. Custom resources replace the default starting kit, and squads have four members. The requested headquarters is used exactly.",
    {
      session_id: sessionId,
      lease: z.string().uuid().describe("Global lease from acquire_control"),
      request_id: requestId,
      name: z.string().trim().min(1).max(48),
      hq_building_id: z.string().min(1).max(64),
      workers: z.number().int().min(1).max(64),
      squads: z.number().int().min(0).max(8),
      buildings: z.array(z.object({
        building_id: z.string().min(1).max(64),
        draft_id: z.enum(["bld_house", "bld_shelter", "bld_warehouse"]),
      })).max(8).optional(),
      starting_resources: z.array(z.object({
        resource_id: z.string().min(1).max(64),
        crates: z.number().int().min(1).max(1000),
      })).max(48).optional().describe("Exact starting kit in crates. Omit for the game's default kit."),
      preview: z.boolean().optional().describe("Validate without changing the game"),
    });

  write("npc_acquire_control",
    "Acquire an NPC-scoped 90-second write lease. To renew it, call again with the same owner, faction_id and current lease. The lease permits commands for that NPC faction only.",
    { session_id: sessionId, faction_id: factionId, owner: z.string().min(1).max(40).describe("Controller name, e.g. dsh"),
      lease: lease.optional().describe("Existing lease token when renewing control") });

  write("npc_command",
    "Issue a command to the controlled NPC faction. Use mode with take_over=true to pause local decisions, or false to resume them. action=auto clears one squad's orders and manual hold. On builds reporting npc_get_faction.vehicles, take_vehicle requires squad_id and vehicle_id from that report. A walking squad can take a faction-owned vehicle or a discovered unclaimed vehicle in range. Read vehicles[].squad_id again to confirm boarding after the order is accepted." +
      (testCopy ? " Use build_farmland with draft_id and exact x/z from npc_preview_farmland. workers and production accept an owned farm's structure_id. Use research_start or research_cancel with research_id from npc_get_research." : ""),
    {
      session_id: sessionId,
      faction_id: factionId,
      lease,
      request_id: requestId,
      action: z.enum(["strategy", "mode", "workers", "production", "build", "scavenge", "move", "return", "auto", "attack", "take_vehicle",
        ...(testCopy ? ["build_farmland", "research_start", "research_cancel"] : [])])
        .describe("Command to run"),
      squad_id: z.string().min(1).max(64).optional().describe("Owned squad id (scavenge/move/return/auto/attack/take_vehicle)"),
      vehicle_id: z.string().min(1).max(64).optional().describe("Required for take_vehicle. Use the id from npc_get_faction.vehicles."),
      building_id: z.string().min(1).max(64).optional().describe("Building id (scavenge/workers/production/build)"),
      ...(testCopy ? {
        structure_id: z.string().min(1).max(64).optional().describe("Owned building or farmland id (workers/production)"),
        research_id: z.string().min(1).max(96).optional().describe("Technology id from npc_get_research (research_start/research_cancel)"),
      } : {}),
      group_id: z.string().min(1).max(64).optional().describe("Hostile infected group id (attack)"),
      recipe_id: z.string().min(1).max(96).optional().describe("Production recipe id (production)"),
      draft_id: z.string().min(1).max(96).optional().describe("Draft id (build/build_farmland)"),
      x: z.number().finite().optional().describe("World x (move/build_farmland)"),
      z: z.number().finite().optional().describe("World z (move/build_farmland)"),
      max_workers: z.number().int().min(0).max(64).optional().describe("Staffing cap (workers)"),
      take_over: z.boolean().optional().describe("true = model takeover, false = local autonomous (mode)"),
      focus: z.enum(["balanced", "food", "materials", "defense"]).optional().describe("Economy focus (strategy)"),
      allow_expansion: z.boolean().optional().describe("Allow the local controller to claim buildings (strategy)"),
      search_radius: z.number().finite().min(200).max(1200).optional().describe("Operating radius in metres (strategy)"),
      hold_hours: z.number().finite().min(0.25).max(12).optional().describe("Game hours the command/mode wins over local automation (default 3)"),
    });

  write("npc_release_control",
    "Release the NPC-scoped lease. The faction isolation bridge restores local automation on release or expiry. With older bridges, send npc_command action=mode take_over=false while the lease is active, then release it.",
    { session_id: sessionId, lease });
}
