# V2-OPS cell-existence repair

This active package starts from the immutable experiment at
`reports/v2-ops-20261103-215505/v2ops`. Network shapes and action parameters are
unchanged. Historical experiment files and weights are not edited.

`cell_map.py` reads the existing `ReadValue(data.map_data)` RPC. A nonzero
`Cell.index` equal to its array slot is positive evidence of a native cell:
`get_valid_cells` selects non-null `TryGetCellAt` pointers; `parse_cells` computes
row-major indices; `Cell::copy_to` and `StateSave::update_MapData` preserve them.
The first map initialization does not set indices. Missing/zero indices are
UNKNOWN, not passable: initial empty data is refused. Cell zero is ambiguous
and conservatively excluded. Nonzero native offsets are currently unsupported.

This is cell **existence**, not unit terrain passability, building occupancy,
path reachability, or proof that an accepted command completed. No shape is
inferred from rectangular bounds, shroud or terrain. Full native cell contents
are reduced to indices before entering the policy; hidden objects/economy and
terrain values are not policy features.

Every snapshot carries episode-bound geometry and `legality_mask_version`.
MOVE x is permitted only if at least one representable y is valid; y depends
on the chosen x. If there is no representable destination, MOVE is disabled.
NOOP and other legal families remain available. Training and inference use the
same mask function. Old snapshots without the cell-map payload are rejected.

`load(path)` rejects unversioned old weights by default. Explicit
`allow_legacy_mask=True` permits frozen legacy weights with the new mask;
this changes the effective policy and must be disclosed in evaluation.
New checkpoints store the mask version. Forecast and control weights are
otherwise unchanged; no old checkpoint is overwritten.

`play.py` is the new module entrypoint for future authorized bounded matches.
It requires a **new** MATCH_PLAN.json naming `native-cell-index/1`, the actual
model SHA256, and an explicit legacy-mask opt-in when using old weights. It
reads/caches geometry once per fresh game process, records CELL_MAP.json, and
passes it into SnapshotBuilder. It does not prepare the game or create a run
authorization. Existing launch/watch helpers still target the historical
runner; they must not be reused silently. The next live run must explicitly
select this entrypoint and preserve the existing launcher/cleanup safeguards.

Validation:

```
F:\conda\envs\python_mmx_LLM\python.exe -B reports/map-legality-20261103/test_cell_map.py
```

Archived regression results are in `reports/map-legality-20261103/REPLAY.json`.
Archived SoA lacks indices, so that offline replay uses a separately labeled
positive-evidence map. It does not pretend to be a real native-index RPC test.
The live builder refuses this offline provenance. Bounded native validations
are recorded in `reports/map-legality-live-retry1-20261103` and
`reports/architecture-live-20261103`; these do not establish policy strength.

`execution.py` performs a single send attempt per decision. It preserves
command/ACK evidence if the following state read fails, stops on uncertain
delivery or a partial actor set, and requires a strictly advancing next frame.
`play.py` writes and fsyncs ATTEMPT before execution, then DECISIONS and RESULT.
An unmatched ATTEMPT means unknown delivery, not permission to resend.
ACK acceptance is distinct from observed completion of the game action.

Fault injection tests are in `reports/architecture-faults-20261103/test_faults.py`;
they use synthetic capture/environment failures. Normal native regression and
journal pairing evidence are kept separately in
`reports/architecture-faults-live-20261103`. Real interruption and concurrent
failure scenarios still require bounded validation.

Multi-instance matches may specify `port` and `instance` in MATCH_PLAN.json.
`runtime.py` checks the direct isolated game directory, bridge port, and
nonzero-instance executable/mutex marker before launch. Each process retains
its own capture files and instance lock; global stale-game killing stays off.
The first two-worker evidence is in `reports/tech-learning-20261104`.

If a native snapshot repeats the previous frame, execution permits at most
four state reads with 250ms gaps; it never repeats the command. Every read
frame is retained in `state_reads`. Backwards/invalid frames and persistent
stalls still fail. This handles brief pauses without fabricating a transition.
