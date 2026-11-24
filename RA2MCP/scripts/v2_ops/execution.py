"""Single-attempt execution with explicit unknown/partial/failure evidence."""
import asyncio
import copy
import time
from dual_channel.collect_formation import rejection_class


async def execute_once(env, snapshot, intent, decision, capture, sleep=asyncio.sleep):
    attempted = getattr(env, '_v2_attempted_decisions', None)
    if attempted is None:
        attempted = env._v2_attempted_decisions = set()
    if decision in attempted:
        raise RuntimeError('duplicate decision refused; previous delivery may be unknown')
    attempted.add(decision)  # consumed even if setup/send/read fails; never retry
    ext = capture.extractor()
    result = {'classification': 'UNKNOWN', 'command_status': 'NOT_ATTEMPTED',
              'ack_records': [], 'native_commands': [], 'next_frame': None,
              'accepted_is_not_applied': True, 'fatal': False, 'phase': 'preflight'}
    if ext is None or capture.status().get('errors'):
        return dict(result, fatal=True, error='command provenance unavailable')
    start_index = len(ext.records)
    started = time.monotonic()
    env._dec_acks = []
    try:
        if intent is not None:
            if not capture.set_context(action_idx=None, decision_id=decision,
                    issued_frame=snapshot['frame'], action_id=decision+':command:00'):
                raise RuntimeError('command context rejected')
            result['phase'] = 'send'
            result['command_status'] = 'UNKNOWN'
            await env._commands([intent], 'v2_ops_model')
        result['phase'] = 'classify'
        acks = copy.deepcopy(env._dec_acks)
        commands = copy.deepcopy(ext.records[start_index:])
        result.update(ack_records=acks, native_commands=commands)
        errors = [a['error'] for a in acks if a.get('error')]
        skipped = any(a.get('event') == 'order_skipped' for a in acks)
        status = 'NOOP' if intent is None else rejection_class(errors[0] if errors else None, skipped)
        if len(commands)>1 or (intent is None and commands):
            raise RuntimeError('multiple or unsolicited native commands')
        if intent is not None and status=='ACCEPTED':
            if len(commands)!=1 or not acks:
                status='UNKNOWN'
            elif intent.get('object_addresses'):
                actual=commands[0].get('actor_addresses',commands[0].get('actor_unit_ids'))
                if actual is None:
                    status='UNKNOWN'
                elif sorted(actual)!=sorted(intent['object_addresses']):
                    status='PARTIAL_ACTOR_SET'
        if skipped and commands:
            status='UNKNOWN'  # a local skip cannot prove no delivery if a send exists
        result.update(classification=status,command_status=status)
        if status in ('TRANSPORT_FAILURE','UNKNOWN','PARTIAL_ACTOR_SET'):
            raise RuntimeError('uncertain or partial command; stop without resend')
        if capture.status().get('errors'):
            raise RuntimeError('native command capture errors')
        result['phase']='read_next_state'
        await sleep(max(.01,.5-(time.monotonic()-started)))
        result['state_reads']=[]
        for attempt in range(4):
            await env._capture()
            frame=env.current.get('frame')
            result['state_reads'].append({'attempt':attempt+1,'frame':frame})
            if type(frame) is not int or frame<snapshot['frame']:
                raise RuntimeError('invalid or backwards next-state frame')
            if frame>snapshot['frame']:break
            if getattr(env,'kind','running')!='running' or attempt==3:
                raise RuntimeError('non-increasing next-state frame after bounded reads')
            # A native snapshot can repeat while the game thread briefly pauses.
            # Only read again: never resend the already-attempted command.
            await sleep(.25)
        result.update(next_frame=frame,phase='complete')
    except Exception as exc:
        # Preserve evidence even when protection raises before ACK collection,
        # or the read following an already accepted command fails.
        result['ack_records']=copy.deepcopy(env._dec_acks)
        result['native_commands']=copy.deepcopy(ext.records[start_index:])
        if result['phase']=='send':result['classification']='TRANSPORT_FAILURE'
        elif result['phase']=='read_next_state':result['classification']='STATE_READ_FAILED'
        result.update(fatal=True,error=f'{type(exc).__name__}: {exc}')
    return result
