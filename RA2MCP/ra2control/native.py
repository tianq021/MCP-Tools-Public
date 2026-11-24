"""Bounded native transport. Never reconnect/replay a mutating request."""
import asyncio
import pathlib
import sys

import aiohttp

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))
from ra2yrproto import core, commands_yr, commands_game, ra2yr


class Native:
    def __init__(self, port=14521):
        self.port = port
        self.session = None
        self.command = self.poll = None
        self.lock = asyncio.Lock()

    async def close(self):
        if self.session:
            await self.session.close()
        self.session = self.command = self.poll = None

    async def connect(self):
        if self.session:
            return
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))
        try:
            self.command = await self.session.ws_connect(f'http://127.0.0.1:{self.port}')
            self.poll = await self.session.ws_connect(f'http://127.0.0.1:{self.port}')
        except BaseException:
            await self.close()
            raise

    async def _exchange(self, socket, message):
        await socket.send_bytes(message.SerializeToString())
        raw = await socket.receive()
        if raw.type != aiohttp.WSMsgType.BINARY:
            raise ConnectionError('Native socket closed or nonbinary reply')
        result = core.Response()
        result.ParseFromString(raw.data)
        return result

    async def execute(self, message):
        async with self.lock:
            try:
                async with asyncio.timeout(3):
                    await self.connect()
                    envelope = core.Command(command_type=core.CLIENT_COMMAND)
                    envelope.command.Pack(message)
                    reply = await self._exchange(self.command, envelope)
                    ack = core.RunCommandAck()
                    if not reply.body.Unpack(ack):
                        raise RuntimeError('Invalid native ACK')
                    while True:
                        poll = core.PollResults()
                        poll.args.queue_id = ack.queue_id
                        poll.args.timeout = 500
                        envelope = core.Command(command_type=core.POLL_BLOCKING)
                        envelope.command.Pack(poll)
                        reply = await self._exchange(self.poll, envelope)
                        results = core.PollResults()
                        if not reply.body.Unpack(results):
                            raise RuntimeError('Invalid poll response')
                        for result in results.result.results:
                            if result.command_id != ack.id:
                                continue
                            if result.result_code:
                                raise ValueError('Native rejected: ' + result.error_message)
                            output = type(message)()
                            if not result.result.Unpack(output):
                                raise RuntimeError('Invalid result type')
                            return output
            except BaseException:
                await self.close()
                raise

    async def state(self):
        return (await self.execute(commands_yr.GetGameState())).state

    async def initial(self):
        q = commands_yr.ReadValue()
        q.data.initial_game_state.SetInParent()
        return (await self.execute(q)).data.initial_game_state

    async def order(self, ids, action, xy=None, target=0):
        q = commands_game.UnitOrder(object_addresses=ids, action=getattr(ra2yr, 'UNIT_ACTION_' + action), target_object=target)
        if xy is not None:
            q.coordinates.x, q.coordinates.y = xy
        return await self.execute(q)
