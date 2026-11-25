"""CSC 6410 Experiment 5 — CUBIC vs Reno throughput data collection.

Run from the CURRENT Mininet CLI, not as a stand-alone Python process:

  mininet> px exec(open('/home/dell/EXP5/exp5_collect.py').read(), globals(), locals())

This script uses the existing Mininet h1, h2 and s1 objects; it does NOT
create or stop the topology. Test parameters reflect the user's experiment:
20 Mb/s, 100 ms delay, netem losses of 0, 0.1, 0.5, 1 and 2%, 30 sec.
"""


def run_exp5(h1, h2, s1, losses=(0, 0.1, 0.5, 1, 2), trials=3, seconds=30):
    import csv
    import json
    import os
    import re
    import time
    from datetime import datetime
    from pathlib import Path

    if seconds < 5 or trials < 1:
        raise ValueError('Each trial must last >=5 seconds and trials >=1')
    if any(float(p) < 0 or float(p) > 100 for p in losses):
        raise ValueError('Loss percentages must be between 0 and 100')

    # Fresh output directory preserves all initial and previous results.
    base = Path('/home/dell/EXP5/results')
    base.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = base / ('formal_' + timestamp)
    suffix = 2
    while run_dir.exists():
        run_dir = base / ('formal_' + timestamp + '_' + str(suffix))
        suffix += 1
    run_dir.mkdir()
    csv_file = run_dir / 'summary.csv'
    notes_file = run_dir / 'testbed.txt'

    # Validate the existing topology before any measurement.
    host1_ip, host2_ip = h1.IP(), h2.IP()
    if host1_ip != '10.0.0.1' or host2_ip != '10.0.0.2':
        raise RuntimeError('Unexpected Mininet topology IPs: %s, %s' % (host1_ip, host2_ip))
    port_listening = h2.cmd("ss -lnt '( sport = :5201 )'")
    if 'LISTEN' not in port_listening:
        raise RuntimeError('No iperf3 server listening on h2:5201; run: h2 iperf3 -s -D')
    available = h1.cmd('sysctl -n net.ipv4.tcp_available_congestion_control')
    for algo in ('cubic', 'reno'):
        if algo not in available.split():
            raise RuntimeError('Algorithm %s unavailable: %s' % (algo, available))

    # Retain the validated offload adjustment. Do not disable checksumming.
    h1.cmd('ethtool -K h1-eth0 tso off gso off')
    s1.cmd('ethtool -K s1-eth2 tso off gso off')

    notes_file.write_text(
        'Host: Ubuntu WSL2 (native Mininet, ovsbr standalone)\n'
        'Topology: h1 (10.0.0.1) -- s1 -- h2 (10.0.0.2)\n'
        'Bottleneck: s1-eth2 netem; delay 100ms; rate 20mbit; queue limit 1000\n'
        'Loss levels (%): %s\n'
        'Congestion control: cubic and reno, set with iperf3 -C\n'
        'Trial length (s): %s; trials per CC per loss: %s\n'
        'TSO/GSO off on h1-eth0 and s1-eth2; TX checksum setting unchanged\n'
        'Note: Netem queue drops can occur even with 0%% configured random loss.\n'
        'Available CC: %s\n' % (', '.join(map(str, losses)), seconds, trials, available.strip()),
        encoding='utf-8')

    columns = [
        'loss_percent', 'trial', 'algorithm', 'duration_requested_s',
        'receiver_mbps', 'sender_mbps', 'sender_retransmits',
        'netem_packets_dropped', 'status', 'json_file', 'netem_stats_file',
        'timestamp',
    ]
    with csv_file.open('w', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=columns).writeheader()

    jobs = len(losses) * trials * 2
    done = 0
    print('\nEXP5 collection started — %d trials. Output: %s' % (jobs, run_dir), flush=True)

    for loss in losses:
        loss = float(loss)
        loss_tag = ('%g' % loss).replace('.', 'p')
        for trial in range(1, trials + 1):
            # Alternate order across trials to reduce consistent order bias.
            algorithms = ('cubic', 'reno') if trial % 2 else ('reno', 'cubic')
            for algo in algorithms:
                done += 1
                stem = '%s_loss%s_trial%d' % (algo, loss_tag, trial)
                raw_path = run_dir / (stem + '.json')
                tc_path = run_dir / (stem + '_netem.txt')

                # Replace the qdisc anew so its drop counter is per-trial.
                s1.cmd('tc qdisc del dev s1-eth2 root 2>/dev/null')
                cmd = ('tc qdisc add dev s1-eth2 root netem '
                       'delay 100ms loss %g%% rate 20mbit limit 1000' % loss)
                tc_response = s1.cmd(cmd)
                initial = s1.cmd('tc -s qdisc show dev s1-eth2')
                if 'netem' not in initial or 'rate 20Mbit' not in initial:
                    raise RuntimeError('Cannot configure bottleneck: %s\n%s' % (tc_response, initial))

                print('[%d/%d] %s (30s target: %ds, loss=%g%%)' %
                      (done, jobs, stem, seconds, loss), flush=True)

                # IPERF JSON is saved even for a failed trial; never invent values.
                # h1.cmd returns when the 30-second iperf command finishes.
                raw = h1.cmd('iperf3 -c 10.0.0.2 -p 5201 -C %s -t %d -J' %
                             (algo, seconds))
                raw_path.write_text(raw, encoding='utf-8')
                stats = s1.cmd('tc -s qdisc show dev s1-eth2')
                tc_path.write_text('BEFORE:\n' + initial + '\nAFTER:\n' + stats,
                                   encoding='utf-8')

                receiver = sender = retrans = None
                status = 'ok'
                try:
                    data = json.loads(raw)
                    if 'error' in data:
                        raise ValueError(data['error'])
                    end = data['end']
                    receiver = round(end['sum_received']['bits_per_second'] / 1e6, 3)
                    sender = round(end['sum_sent']['bits_per_second'] / 1e6, 3)
                    retrans = end['sum_sent'].get('retransmits')
                except Exception as exc:
                    status = 'FAILED: ' + str(exc)[:100]

                match = re.search(r'\(dropped\s+(\d+)', stats)
                drops = int(match.group(1)) if match else None
                row = dict(
                    loss_percent=loss, trial=trial, algorithm=algo,
                    duration_requested_s=seconds, receiver_mbps=receiver,
                    sender_mbps=sender, sender_retransmits=retrans,
                    netem_packets_dropped=drops, status=status,
                    json_file=raw_path.name, netem_stats_file=tc_path.name,
                    timestamp=datetime.now().isoformat(timespec='seconds'))
                with csv_file.open('a', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=columns)
                    writer.writerow(row)

                if status != 'ok':
                    print('STOP: trial failed. Check %s and %s' %
                          (raw_path, tc_path), flush=True)
                    return str(run_dir)
                print('    receiver=%.3f Mbps, retrans=%s, netem drops=%s' %
                      (receiver, retrans, drops), flush=True)
                time.sleep(1)

    print('\nCOMPLETE: %d/%d trials. CSV: %s' % (done, jobs, csv_file), flush=True)
    return str(run_dir)


# Automatic launch from the Mininet CLI: `px exec(open(...).read(), globals(), locals())`
# First execute only two REAL 30-second trials (0% loss); these count as real data.
# After verifying output, set EXP5_FULL_RUN=True in the Mininet CLI to run all 30.
if locals().get('EXP5_FULL_RUN', False):
    run_exp5(h1, h2, s1)
else:
    run_exp5(h1, h2, s1, losses=(0,), trials=1, seconds=30)
