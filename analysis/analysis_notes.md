# Experiment 5 — Phase 3 statistical analysis
Source data: user-recorded `results.zip` (30 single-flow trials) and `result2.zip` (two concurrent-flow scenarios).

## Experimental conditions
20 Mbps egress netem bottleneck, 100 ms configured one-way delay, 0%, 0.1%, 0.5%, 1%, or 2% configured random loss. Native Ubuntu WSL2, Mininet/OVS bridge. BBR not available on this WSL2 kernel; Reno used as permitted alternative.

## Receiver throughput (mean ± sample standard deviation, Mbps; n=3)

| Loss | CUBIC | Reno | Mathis-style reference* |
|---:|---:|---:|---:|
| 0% | 18.846 ± 0.000 | 18.828 ± 0.001 | 20.000 |
| 0.1% | 6.479 ± 1.177 | 5.504 ± 0.610 | 4.469 |
| 0.5% | 2.415 ± 0.172 | 2.404 ± 0.160 | 1.999 |
| 1% | 1.510 ± 0.369 | 1.359 ± 0.181 | 1.413 |
| 2% | 1.010 ± 0.070 | 0.906 ± 0.061 | 0.999 |

*Simplified reference = 1.22 × MSS × 8 / (RTT × sqrt(p)); MSS=1,448 bytes; RTT=0.100 s; p is fractional loss. At p=0, use the 20 Mbps link cap. This Reno-like approximation is not a precise CUBIC predictor, especially with queue-induced loss or RTT inflation.*

## Fairness (n=1 concurrent 60-second run per queue size)

| Queue packets | CUBIC Mbps | Reno Mbps | Total Mbps | Jain J | Netem drops |
|---:|---:|---:|---:|---:|---:|
| 1000 | 10.661 | 8.169 | 18.830 | 0.98278 | 1075 |
| 250 | 13.406 | 5.579 | 18.985 | 0.85471 | 246 |

Formula: Jain J = (x₁+x₂)² / [2(x₁²+x₂²)], using receiver Mbps of the two competing flows. The indices are independently recomputed from the archived CSV.

## Findings and limitations

- All 30 single-flow trial records report `status=ok`; throughput generally decreases sharply as configured loss increases. CUBIC has a higher sample mean than Reno at all five loss levels, but n=3 is too small to establish a universal performance advantage.
- The 0% configured random-loss tests still report nonzero netem drops due to bottleneck queue overflow; the results must not be described as zero total packet loss.
- At queue size 1000, Jain J is 0.983; at size 250, Jain J is 0.855, indicating less equal sharing in this observed run. Combined throughput remains near 19 Mbps in both scenarios.
- The 1000-packet case reports greater RTT and more queue drops than the 250-packet case. This pattern is consistent with queue buildup but only one 60-second run was collected for each queue size.
- The simplified loss-throughput formula assumes a baseline RTT and conventional loss-driven congestion response; measured RTT can increase under queueing, and CUBIC does not strictly follow this model.
- Individual JSON and netem text files remain the raw evidence. The source `results.zip` lacks `reno_loss1_trial3_netem.txt`, but the user supplied the recovered file separately.
