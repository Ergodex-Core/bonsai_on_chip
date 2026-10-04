"""Check independently scored store completion traces from a future RTL bench.

This checker validates event ordering. It does not run RTL, establish execution
provenance, certify timing, or approve an FPGA image.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, name):
    require(
        type(value) is int and value >= 0,
        name + ' must be a nonnegative integer'
    )
    return value


def validate(trace, max_latency=2):
    require(
        type(max_latency) is int and 0 <= max_latency <= 32,
        'invalid latency limit'
    )
    require(
        trace.get('schema') == 'coralnpu.lsu_store_completion_trace.v1',
        'trace schema'
    )
    require(
        trace.get('signal')
        in ('lsu.storeComplete', 'retirement.storeComplete'), 'sample point'
    )
    require(
        trace.get('provenance') in ('synthetic', 'rtl_simulation'),
        'trace provenance label'
    )
    rows = trace.get('cycles')
    require(type(rows) is list and bool(rows), 'empty trace')
    transactions, eligible = {}, deque()
    previous_cycle, checked, canceled = -1, 0, 0
    for row in rows:
        cycle = integer(row['cycle'], 'cycle')
        require(cycle > previous_cycle, 'cycles must increase')
        previous_cycle = cycle
        for event in row.get('dispatch', []):
            seq = integer(event['id'], 'transaction id')
            pc = integer(event['pc'], 'PC')
            require(pc < 2**32 and pc % 2 == 0, 'invalid RISC-V PC')
            require(seq not in transactions, 'transaction id reused')
            transactions[seq] = dict(pc=pc, state='pending')
        # The reference scoreboard determines cancellations at this sample edge.
        # A fault/flush must not produce a completion for a canceled transaction.
        for seq in row.get('cancel', []):
            integer(seq, 'cancel id')
            require(seq in transactions, 'cancel of unknown transaction')
            require(
                transactions[seq]['state'] in ('pending', 'eligible'),
                'cancel after terminal event'
            )
            transactions[seq]['state'] = 'canceled'
            eligible = deque(e for e in eligible if e[0] != seq)
            canceled += 1
        for seq in row.get('eligible', []):
            integer(seq, 'eligible id')
            require(
                seq in transactions
                and transactions[seq]['state'] == 'pending',
                'invalid eligibility'
            )
            transactions[seq]['state'] = 'eligible'
            eligible.append((seq, cycle))
        observed = row.get('observed', [])
        require(
            type(observed) is list and len(observed) <= 1,
            'single completion port'
        )
        for event in observed:
            require(
                bool(eligible),
                'unexpected, duplicate, premature or canceled completion'
            )
            seq, start = eligible.popleft()
            pc = integer(event['pc'], 'observed PC')
            require(
                pc == transactions[seq]['pc'], 'completion PC/order mismatch'
            )
            require(cycle - start <= max_latency, 'late completion')
            transactions[seq]['state'] = 'completed'
            checked += 1
        require(
            not eligible or cycle - eligible[0][1] <= max_latency,
            'completion deadline missed'
        )
    require(bool(transactions), 'no store transactions exercised')
    require(not eligible, 'missing completion at end of trace')
    require(
        all(
            t['state'] in ('completed', 'canceled')
            for t in transactions.values()
        ), 'unfinished transaction'
    )
    require(checked > 0, 'no successful store completion exercised')
    return dict(
        status='PASS trace contract',
        completions=checked,
        canceled=canceled,
        provenance=trace['provenance'],
        signal=trace['signal'],
        physical_fpga=False,
        rtl_execution_verified=False,
        routed_timing_accepted=False
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--trace-sha256', required=True)
    parser.add_argument('--max-latency', type=int, default=2)
    args = parser.parse_args()
    raw = args.trace.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    require(sha == args.trace_sha256, 'trace identity changed')
    result = validate(json.loads(raw), args.max_latency)
    result['trace_sha256'] = sha
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
