"""Evidence-backed cell existence, not terrain passability or pathfinding.

The existing native MapData update writes Cell.index only for non-null
TryGetCellAt entries (state_parser.cpp parse_cells/Cell::copy_to). The initial
parse_MapData does NOT write indices: uninitialized/unknown entries remain
blocked until a map-difference update has populated them. Index zero is
ambiguous in proto3 and is deliberately not admitted.
"""
from dataclasses import dataclass
from functools import lru_cache
import asyncio
import hashlib
import json

MASK_VERSION = 'native-cell-index/1'


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class CellMap:
    width: int
    height: int
    rows: tuple
    episode: str
    frame: int
    source: str = 'native-mapdata-index'

    def __post_init__(self):
        if not (0 < self.width <= 256 and 0 < self.height <= 256):
            raise ValueError('cell map dimensions outside contract')
        if len(self.rows) != self.height or any(type(r) is not int or not 0 <= r < 1 << self.width for r in self.rows):
            raise ValueError('invalid cell map rows')
        if not self.episode or type(self.frame) is not int or self.frame < 0:
            raise ValueError('missing cell map episode/frame')
        if not any(self.rows):
            raise ValueError('no verified cells; map index update may not have arrived')

    @classmethod
    def from_native(cls, message, episode, frame):
        width, height = int(message.width), int(message.height)
        if not (0 < width <= 256 and 0 < height <= 256):
            raise ValueError('invalid native map dimensions')
        if message.offset_x or message.offset_y:
            raise ValueError('unsupported native map offsets')
        if len(message.cells) != width * height:
            raise ValueError('incomplete native cell array')
        rows = [0] * height
        for slot, cell in enumerate(message.cells):
            index = int(cell.index)
            if index == 0:
                continue  # unknown, NOT evidence that this cell does not exist
            if index != slot:
                raise ValueError('native index/array position mismatch')
            rows[slot // width] |= 1 << (slot % width)
        return cls(width, height, tuple(rows), str(episode), int(frame))

    def payload(self):
        data = {'version': MASK_VERSION, 'width': self.width, 'height': self.height,
                'rows_hex': [hex(r) for r in self.rows], 'episode': self.episode,
                'frame': self.frame, 'source': self.source}
        return dict(data, sha256=checksum(data))

    def attach(self, snapshot):
        if (snapshot['episode'] != self.episode or snapshot['frame'] < self.frame or
                snapshot['map_width'] != self.width or snapshot['map_height'] != self.height):
            raise ValueError('cell map episode/frame/dimensions mismatch')
        snapshot['cell_map'] = self.payload()
        snapshot['legality_mask_version'] = MASK_VERSION
        return snapshot


@lru_cache(maxsize=64)
def _decode(encoded, width, height):
    if len(encoded) != height:
        raise ValueError('cell map row count mismatch')
    rows = tuple(int(v, 16) for v in encoded)
    if any(not 0 <= r < 1 << width for r in rows):
        raise ValueError('cell map bit outside dimensions')
    return rows


def rows_for(snapshot):
    data = snapshot.get('cell_map')
    if not data or snapshot.get('legality_mask_version') != MASK_VERSION:
        raise ValueError('verified cell map required; legacy bounds masks refused')
    if data.get('version') != MASK_VERSION or data.get('source') not in ('native-mapdata-index', 'archived-soa-positive-evidence'):
        raise ValueError('unknown cell map provenance')
    if (data['episode'] != snapshot['episode'] or data['frame'] > snapshot['frame'] or
            data['width'] != snapshot['map_width'] or data['height'] != snapshot['map_height']):
        raise ValueError('stale or incompatible cell map')
    if data['sha256'] != checksum({k: v for k, v in data.items() if k != 'sha256'}):
        raise ValueError('cell map integrity mismatch')
    return _decode(tuple(data['rows_hex']), data['width'], data['height'])


def move_options(snapshot, origin):
    """Each x factor maps to exactly the y factors with verified cells.

    An x with no representable y is never sampled, so autoregressive decoding
    cannot enter a dead end. The 256-factor relative-offset contract is kept.
    """
    rows = rows_for(snapshot)
    ox, oy = origin
    return _options(rows, snapshot['map_width'], ox, oy)


@lru_cache(maxsize=128)
def _options(rows, width, ox, oy):
    columns = [0] * 256
    for fy in range(256):
        y = oy + fy - 128
        if not 0 <= y < len(rows):
            continue
        bits = rows[y]
        while bits:
            low = bits & -bits
            x = low.bit_length() - 1
            fx = x - ox + 128
            if 0 <= fx < 256:
                columns[fx] |= 1 << fy
            bits -= low
    return tuple(columns)


async def read_native_cells(client, episode, frame, timeout=20.):
    """Read-only existing RPC. No DLL, protobuf or game configuration changes."""
    from ra2yrproto import commands_yr
    query = commands_yr.ReadValue()
    query.data.map_data.SetInParent()
    response = await asyncio.wait_for(client.exec_command(query, timeout=min(15., timeout)), timeout)
    if getattr(response,'result_code',0)!=0:
        raise ValueError('native MapData read rejected: '+str(getattr(response,'error_message','')))
    message = commands_yr.ReadValue()
    if not response.result.Unpack(message):
        raise ValueError('wrong native MapData response')
    return CellMap.from_native(message.data.map_data, episode, frame)
