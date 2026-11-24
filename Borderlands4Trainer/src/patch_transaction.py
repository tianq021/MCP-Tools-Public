"""All-or-rollback byte transaction; caller must quiesce target threads."""


def transact(backend, requests, report):
    snapshots, attempted, protections = [], [], []
    report.update(writes=[], rollback=[], protection_errors=[])
    try:
        # Preflight the entire batch before changing any page or byte.
        for address, allowed, target in requests:
            original = backend.read(address, len(target))
            if original not in allowed:
                raise RuntimeError(f'Unknown bytes at {address:#x}: {original.hex()}')
            snapshots.append((address, original, target))
        for address, original, target in snapshots:
            if original == target:
                continue
            old = backend.protect(address, len(target), 0x40)
            protections.append((address, len(target), old))
            attempted.append((address, original))  # Includes partial write failures.
            backend.write(address, target)
            backend.flush(address, len(target))
            if backend.read(address, len(target)) != target:
                raise RuntimeError(f'Readback mismatch at {address:#x}')
            report['writes'].append(dict(address=hex(address), before=original.hex(), after=target.hex()))
        for address, size, old in reversed(protections):
            backend.protect(address, size, old)
        report['transaction'] = 'committed'
    except BaseException:
        report['transaction'] = 'failed'
        for address, original in reversed(attempted):
            entry = dict(address=hex(address))
            try:
                backend.protect(address, len(original), 0x40)
                backend.write(address, original)
                backend.flush(address, len(original))
                if backend.read(address, len(original)) != original:
                    raise RuntimeError('Rollback readback mismatch')
                entry['restored'] = True
            except BaseException as error:
                entry.update(restored=False, error=str(error))
            report['rollback'].append(entry)
        raise
    finally:
        # Retry protection restoration even after a failed write or restoration.
        for address, size, old in reversed(protections if report.get('transaction') != 'committed' else []):
            try:
                backend.protect(address, size, old)
            except BaseException as error:
                report['protection_errors'].append(str(error))
        if report['protection_errors']:
            raise RuntimeError('Page protection restoration failed; inspect transaction log')
