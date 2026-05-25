# Latency Analysis Findings

## Overview
Simulated audio stream latency under varying buffer sizes (50ms, 100ms, 200ms) with moderate jitter (50ms) and 2% packet loss.

## Results
| Buffer (ms) | Avg Latency (ms) | Underruns |
|-------------|------------------|-----------|
| 50          | 223.56           | 0         |
| 100         | 332.06           | 0         |
| 200         | 98.35            | 0         |

## Analysis
- **Unexpected Results**: The 200ms buffer showed the lowest latency. This indicates that at 100ms, the simulation might be hitting a resonance point or the jitter handling logic in the `SlidingWindowBuffer` needs refinement.
- **Underruns**: All configurations performed well regarding underruns under the tested jitter conditions.
- **Recommendations**: 
  - Further analysis is needed to understand why 100ms performed poorly.
  - The sliding window buffer logic should be made more robust against small jitter bursts.
  - Implement adaptive buffer sizing: increase buffer when underruns or jitter increase, decrease when stable.
