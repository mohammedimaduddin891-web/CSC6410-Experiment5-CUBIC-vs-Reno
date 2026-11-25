#!/usr/bin/env python3
"""Reproduce CSC6410 EXP5 charts and statistics from measured ZIP archives.
Run: python exp5_analyze.py /path/to/results.zip /path/to/result2.zip /path/to/output_dir
Uses Python standard library and matplotlib; never fabricates measurements.
"""
import sys, os, zipfile, csv, io, math, statistics, pathlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def extract_csv(zpath, name):
    with zipfile.ZipFile(zpath) as z:
        files=[s for s in z.namelist() if s.endswith('/'+name) or s==name]
        if len(files)!=1: raise ValueError(f'Expected 1 {name} in {zpath}, found {files}')
        return list(csv.DictReader(io.StringIO(z.read(files[0]).decode('utf-8'))))

def main(raw_zip,fair_zip,out_dir):
    out=pathlib.Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    raw=extract_csv(raw_zip,'summary.csv')
    fair=extract_csv(fair_zip,'fairness_summary.csv')
    if len(raw)!=30 or len(fair)!=2:raise ValueError(f'Unexpected trial counts: {len(raw)} and {len(fair)}')
    if any(r.get('status')!='ok' for r in raw+fair):raise ValueError('Non-ok trial present')
    losses=[0.0,0.1,0.5,1.0,2.0]
    algorithms=['cubic','reno']
    by={(lo,alg):[float(r['receiver_mbps']) for r in raw if float(r['loss_percent'])==lo and r['algorithm']==alg] for lo in losses for alg in algorithms}
    if any(len(v)!=3 for v in by.values()):raise ValueError('Expected three trials per loss and algorithm')
    mss=1448; rtt=0.100; link_limit=20.0
    def model(loss):
        if loss==0:return link_limit
        return min(link_limit,1.22*mss*8/(rtt*math.sqrt(loss/100))/1e6)
    summary=[]
    for lo in losses:
        for alg in algorithms:
            vals=by[(lo,alg)]
            summary.append({'loss_percent':lo,'algorithm':alg,'n':len(vals),
                            'mean_receiver_mbps':round(statistics.mean(vals),4),
                            'sample_std_mbps':round(statistics.stdev(vals),4),
                            'min_mbps':round(min(vals),4),'max_mbps':round(max(vals),4),
                            'mathis_reference_mbps':round(model(lo),4)})
    with (out/'throughput_summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    for r in fair:
        x=float(r['cubic_receiver_mbps']);y=float(r['reno_receiver_mbps']);recalc=(x+y)**2/(2*(x*x+y*y))
        if abs(float(r['jain_fairness'])-recalc)>0.0001:raise ValueError('Jain fairness mismatch')
    # Figure 1: measured means, ± 1 SD, and simplified loss-response reference.
    plt.rcParams.update({'font.size':11, 'axes.titlesize':15,'figure.facecolor':'white','axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(10.5,6.3),dpi=160)
    x=list(range(len(losses)))
    for alg in algorithms:
        means=[statistics.mean(by[(lo,alg)]) for lo in losses]
        stds=[statistics.stdev(by[(lo,alg)]) for lo in losses]
        ax.errorbar(x,means,yerr=stds,marker='o',capsize=4,lw=2,label=alg.upper() if alg=='cubic' else 'Reno')
    ax.plot(x,[model(lo) for lo in losses],marker='s',linestyle='--',lw=1.6,label='Mathis-style reference (0% = link cap)')
    ax.set_xticks(x,[f'{lo:g}%' for lo in losses]);ax.set_ylabel('Receiver throughput (Mbps)');ax.set_xlabel('Configured random packet loss')
    ax.set_title('CUBIC vs Reno — Receiver throughput across packet loss')
    ax.grid(axis='y',alpha=.22);ax.set_ylim(0,22);ax.legend(loc='upper right',fontsize=10)
    fig.text(.10,.018,'Each measurement: 30 s; n = 3 per algorithm/loss. Error bars: ±1 sample SD. 20 Mbps / 100 ms bottleneck.',fontsize=9)
    fig.subplots_adjust(bottom=.16,left=.11,right=.97,top=.89)
    fig.savefig(out/'throughput_vs_loss.png',bbox_inches='tight');plt.close(fig)
    # Figure 2: competing throughput, fairness and queue delays.
    fig,ax=plt.subplots(figsize=(9.8,5.9),dpi=160)
    xs=[0,1]; width=.32
    x1=[float(r['cubic_receiver_mbps']) for r in fair]
    x2=[float(r['reno_receiver_mbps']) for r in fair]
    bars1=ax.bar([i-width/2 for i in xs],x1,width,label='CUBIC')
    bars2=ax.bar([i+width/2 for i in xs],x2,width,label='Reno')
    for bar in list(bars1)+list(bars2):
        ax.annotate(f'{bar.get_height():.2f}',(bar.get_x()+bar.get_width()/2,bar.get_height()),xytext=(0,5),textcoords='offset points',ha='center',va='bottom',fontsize=10)
    ax.set_xticks(xs,[f"Queue {r['queue_limit_packets']} packets" for r in fair]);ax.set_ylabel('Receiver throughput (Mbps)')
    ax.set_title('Simultaneous TCP flows — 60-second fairness tests');ax.legend()
    ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.set_ylim(0,17)
    for i,r in enumerate(fair):
        ax.text(i,15.45,f"Jain's J = {float(r['jain_fairness']):.3f}",ha='center',fontsize=10)
    fig.text(.11,.025,'One run per queue size. Shared 20 Mbps link; 100 ms one-way netem delay; 0% configured random loss.',fontsize=9)
    fig.subplots_adjust(bottom=.15,left=.10,right=.97,top=.90)
    fig.savefig(out/'fairness_comparison.png',bbox_inches='tight');plt.close(fig)
    # Report-ready text note grounded in the actual data.
    md=[]
    md.append('# Experiment 5 — Phase 3 statistical analysis')
    md.append('Source data: user-recorded `results.zip` (30 single-flow trials) and `result2.zip` (two concurrent-flow scenarios).')
    md.append('\n## Experimental conditions\n20 Mbps egress netem bottleneck, 100 ms configured one-way delay, 0%, 0.1%, 0.5%, 1%, or 2% configured random loss. Native Ubuntu WSL2, Mininet/OVS bridge. BBR not available on this WSL2 kernel; Reno used as permitted alternative.')
    md.append('\n## Receiver throughput (mean ± sample standard deviation, Mbps; n=3)\n')
    md.append('| Loss | CUBIC | Reno | Mathis-style reference* |\n|---:|---:|---:|---:|')
    for lo in losses:
        def f(alg):
            v=by[(lo,alg)];return f'{statistics.mean(v):.3f} ± {statistics.stdev(v):.3f}'
        md.append(f'| {lo:g}% | {f("cubic")} | {f("reno")} | {model(lo):.3f} |')
    md.append('\n*Simplified reference = 1.22 × MSS × 8 / (RTT × sqrt(p)); MSS=1,448 bytes; RTT=0.100 s; p is fractional loss. At p=0, use the 20 Mbps link cap. This Reno-like approximation is not a precise CUBIC predictor, especially with queue-induced loss or RTT inflation.*')
    md.append('\n## Fairness (n=1 concurrent 60-second run per queue size)\n')
    md.append('| Queue packets | CUBIC Mbps | Reno Mbps | Total Mbps | Jain J | Netem drops |\n|---:|---:|---:|---:|---:|---:|')
    for r in fair:
        md.append(f"| {r['queue_limit_packets']} | {float(r['cubic_receiver_mbps']):.3f} | {float(r['reno_receiver_mbps']):.3f} | {float(r['combined_receiver_mbps']):.3f} | {float(r['jain_fairness']):.5f} | {r['netem_total_drops']} |")
    md.append('\nFormula: Jain J = (x₁+x₂)² / [2(x₁²+x₂²)], using receiver Mbps of the two competing flows. The indices are independently recomputed from the archived CSV.')
    md.append('\n## Findings and limitations\n')
    md.append('- All 30 single-flow trial records report `status=ok`; throughput generally decreases sharply as configured loss increases. CUBIC has a higher sample mean than Reno at all five loss levels, but n=3 is too small to establish a universal performance advantage.')
    md.append('- The 0% configured random-loss tests still report nonzero netem drops due to bottleneck queue overflow; the results must not be described as zero total packet loss.')
    md.append('- At queue size 1000, Jain J is 0.983; at size 250, Jain J is 0.855, indicating less equal sharing in this observed run. Combined throughput remains near 19 Mbps in both scenarios.')
    md.append('- The 1000-packet case reports greater RTT and more queue drops than the 250-packet case. This pattern is consistent with queue buildup but only one 60-second run was collected for each queue size.')
    md.append('- The simplified loss-throughput formula assumes a baseline RTT and conventional loss-driven congestion response; measured RTT can increase under queueing, and CUBIC does not strictly follow this model.')
    md.append('- Individual JSON and netem text files remain the raw evidence. The source `results.zip` lacks `reno_loss1_trial3_netem.txt`, but the user supplied the recovered file separately.')
    (out/'analysis_notes.md').write_text('\n'.join(md)+'\n',encoding='utf8')
    print('Wrote files to',out)
    for s in summary: print(s)

if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit('Usage: exp5_analyze.py results.zip result2.zip output_dir')
    main(*sys.argv[1:])
