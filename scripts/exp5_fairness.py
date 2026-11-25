"""CSC 6410 Experiment 5: concurrent CUBIC/Reno fairness measurements.

Run INSIDE the currently working Mininet CLI in native Ubuntu WSL2:

    mininet> px exec(open('/home/dell/EXP5/exp5_fairness.py').read(), globals(), locals())

Requires the existing h1 -- s1 -- h2 topology, Open vSwitch bridge, and
iperf3 server listening on h2:5201. This script creates no new topology.
It tests BOTH 60-second cases: netem queue limits 1000 and 250 packets.
Data are written under /home/dell/EXP5/results/fairness_TIMESTAMP/.
"""


def run_exp5_fairness(h1, h2, s1, seconds=60, queue_limits=(1000, 250)):
    import csv
    import json
    import re
    import subprocess
    import time
    from datetime import datetime
    from pathlib import Path

    if seconds < 10:
        raise ValueError('Use at least 10 seconds per fairness test.')
    if h1.IP() != '10.0.0.1' or h2.IP() != '10.0.0.2':
        raise RuntimeError('Expected h1=10.0.0.1 and h2=10.0.0.2; do not change topology.')
    available = h1.cmd('sysctl -n net.ipv4.tcp_available_congestion_control').split()
    if 'cubic' not in available or 'reno' not in available:
        raise RuntimeError('Both cubic and reno must be available: %s' % available)

    # Reuse port 5201, which was already confirmed in the throughput study.
    listeners = h2.cmd('ss -lnt')
    if not re.search(r':5201\s', listeners):
        raise RuntimeError('h2:5201 not listening; at mininet> run: h2 iperf3 -s -D')
    if not re.search(r':5202\s', listeners):
        response = h2.cmd('iperf3 -s -p 5202 -D')
        time.sleep(0.5)
        listeners = h2.cmd('ss -lnt')
        if not re.search(r':5202\s', listeners):
            raise RuntimeError('Cannot start second iperf3 server on h2:5202: %s' % response)

    # Keep the TSO/GSO adjustment used in the successfully collected 30 trials.
    h1.cmd('ethtool -K h1-eth0 tso off gso off')
    s1.cmd('ethtool -K s1-eth2 tso off gso off')

    base = Path('/home/dell/EXP5/results')
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = base / ('fairness_' + stamp)
    suffix = 2
    while outdir.exists():
        outdir = base / ('fairness_' + stamp + '_' + str(suffix))
        suffix += 1
    outdir.mkdir()

    summary = outdir / 'fairness_summary.csv'
    fields = ['queue_limit_packets', 'duration_requested_s', 'cubic_receiver_mbps',
              'reno_receiver_mbps', 'combined_receiver_mbps', 'jain_fairness',
              'cubic_sender_retransmits', 'reno_sender_retransmits',
              'netem_total_drops', 'status', 'timestamp']
    with summary.open('w', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()

    (outdir / 'testbed.txt').write_text(
        'Native Ubuntu WSL2 Mininet 2-host topology: h1 -- s1 (ovsbr) -- h2\n'
        'Both TCP flows originate on h1 and terminate on h2.\n'
        'CUBIC -> h2:5201 and Reno -> h2:5202, run concurrently.\n'
        'Bottleneck: s1-eth2; netem delay=100ms loss=0%% rate=20mbit.\n'
        'Queue limit compared: %s packets. Duration: %s seconds each.\n'
        'TSO/GSO off on h1-eth0 and s1-eth2; TX checksum unchanged.\n'
        '0%% requested random loss does not rule out queue overflow drops.\n'
        'Available congestion controls: %s\n' %
        (', '.join(map(str, queue_limits)), seconds, ' '.join(available)), encoding='utf-8')

    print('\nEXP5 FAIRNESS: %d concurrent-flow scenarios; output: %s' %
          (len(queue_limits), outdir), flush=True)

    for case_no, limit in enumerate(queue_limits, start=1):
        if int(limit) <= 0:
            raise ValueError('Queue limit must be positive.')
        name = 'queue%d' % int(limit)
        print('[%d/%d] %s: two simultaneous TCP flows for %d seconds' %
              (case_no, len(queue_limits), name, seconds), flush=True)

        # New qdisc per run ensures the drop counter is per-scenario.
        s1.cmd('tc qdisc del dev s1-eth2 root 2>/dev/null')
        qdisc_cmd = ('tc qdisc add dev s1-eth2 root netem delay 100ms '
                     'loss 0%% rate 20mbit limit %d' % int(limit))
        response = s1.cmd(qdisc_cmd)
        before = s1.cmd('tc -s qdisc show dev s1-eth2')
        if 'netem' not in before or ('limit %d' % int(limit)) not in before:
            raise RuntimeError('Bottleneck configuration failed: %s\n%s' % (response, before))

        # Different server ports allow truly concurrent iperf3 client tests.
        specs = [('cubic', 5201), ('reno', 5202)]
        processes = []
        handles = []
        try:
            for algo, port in specs:
                stdout_path = outdir / ('%s_%s.json' % (name, algo))
                stderr_path = outdir / ('%s_%s.stderr.txt' % (name, algo))
                out_h = stdout_path.open('w', encoding='utf-8')
                err_h = stderr_path.open('w', encoding='utf-8')
                handles.extend([out_h, err_h])
                cmd = ['iperf3', '-c', '10.0.0.2', '-p', str(port),
                       '-C', algo, '-t', str(seconds), '-J']
                process = h1.popen(cmd, stdout=out_h, stderr=err_h)
                processes.append((algo, process, stdout_path, stderr_path))
            # Snapshot the connection states, including RTT/cwnd, during overlap.
            time.sleep(min(10, seconds / 3))
            (outdir / ('%s_ss_during.txt' % name)).write_text(
                h1.cmd('ss -tin') + '\n', encoding='utf-8')
            # Both clients were already started before any process is awaited.
            for algo, proc, stdout_path, stderr_path in processes:
                proc.wait(timeout=seconds + 40)
        except Exception:
            for algo, proc, _, _ in processes:
                if proc.poll() is None:
                    proc.terminate()
            raise
        finally:
            for fh in handles:
                fh.close()

        after = s1.cmd('tc -s qdisc show dev s1-eth2')
        (outdir / ('%s_netem.txt' % name)).write_text(
            'BEFORE:\n' + before + '\nAFTER:\n' + after, encoding='utf-8')
        match = re.search(r'\(dropped\s+(\d+)', after)
        total_drops = int(match.group(1)) if match else None

        data = {}
        problems = []
        for algo, proc, json_path, stderr_path in processes:
            try:
                result = json.loads(json_path.read_text(encoding='utf-8'))
                if 'error' in result:
                    raise ValueError(result['error'])
                if result.get('end', {}).get('sender_tcp_congestion') != algo:
                    raise ValueError('Expected sender CC=%s, got %s' %
                                     (algo, result.get('end', {}).get('sender_tcp_congestion')))
                Mbps = result['end']['sum_received']['bits_per_second'] / 1e6
                retrans = result['end']['sum_sent'].get('retransmits')
                data[algo] = {'mbps': Mbps, 'retrans': retrans}
                if proc.returncode:
                    raise ValueError('iperf3 exit code %s' % proc.returncode)
            except Exception as exc:
                problems.append('%s: %s' % (algo, exc))

        status = 'ok' if not problems else 'FAILED: ' + '; '.join(problems)
        c = data.get('cubic', {}).get('mbps')
        r = data.get('reno', {}).get('mbps')
        combined = (c + r) if c is not None and r is not None else None
        jain = ((c + r) ** 2 / (2 * (c*c + r*r))) if c is not None and r is not None and c*c + r*r else None
        row = dict(queue_limit_packets=int(limit), duration_requested_s=seconds,
                   cubic_receiver_mbps=round(c, 3) if c is not None else '',
                   reno_receiver_mbps=round(r, 3) if r is not None else '',
                   combined_receiver_mbps=round(combined, 3) if combined is not None else '',
                   jain_fairness=round(jain, 5) if jain is not None else '',
                   cubic_sender_retransmits=data.get('cubic', {}).get('retrans'),
                   reno_sender_retransmits=data.get('reno', {}).get('retrans'),
                   netem_total_drops=total_drops, status=status,
                   timestamp=datetime.now().isoformat(timespec='seconds'))
        with summary.open('a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=fields).writerow(row)

        if problems:
            print('STOP: %s. Inspect stderr files in %s' % (status, outdir), flush=True)
            break
        print('  CUBIC %.3f Mbps | Reno %.3f Mbps | Total %.3f Mbps | Jain %.5f | netem drops %s' %
              (c, r, combined, jain, total_drops), flush=True)
        time.sleep(1)

    # Restore the baseline 0%-random-loss, 1000-packet configuration.
    restore = s1.cmd('tc qdisc replace dev s1-eth2 root netem delay 100ms loss 0% rate 20mbit limit 1000')
    print('Restored baseline netem:', s1.cmd('tc qdisc show dev s1-eth2').strip(), flush=True)
    print('FAIRNESS FINISHED. CSV:', summary, flush=True)
    return str(outdir)


# Automatic launch in current Mininet CLI; DOES NOT restart Mininet.
run_exp5_fairness(h1, h2, s1)
