| Method | Heuristic | Result | vs my PB | Game steps (expansions) | Time |
|---|---|---|---|---|---|
| My PB (human baseline) | - | 27.82 s | - | - | - |
| Beam search, width 1  (greedy) | PB ghost (ranking) | 27.82 s | +0.00 s | 11,182 | 1.5 min |
| Beam search, width 10 (run 1) | PB ghost (ranking) | 27.82 s | +0.00 s | 49,534 | 6.5 min |
| Beam search, width 10 (run 2) | PB ghost (ranking) | 27.82 s | +0.00 s | 49,534 | 6.5 min |
| Beam search, width 20 | PB ghost (ranking) | 27.82 s | +0.00 s | 92,174 | 12.2 min |
| Beam search, width 40 (main run) | PB ghost (ranking) | 27.66 s | -0.16 s | 177,163 | 19.9 min |
| A* | distance / 1000 km/h | no finish (3% of the track) | - | 180,000 | 23.5 min |
| Weighted A* (w = 5) | distance / 1000 km/h | no finish (5% of the track) | - | 180,000 | 23.7 min |
| Weighted A* (w = 20) | distance / 1000 km/h | no finish (11% of the track) | - | 180,000 | 23.4 min |
| A* + PB heuristic | PB time left | no finish (9% of the track) | - | 180,000 | 24.2 min |
| Weighted A* (w = 1.5) + PB heuristic | PB time left | no finish (12% of the track) | - | 180,000 | 23.8 min |
| Weighted A* (w = 2) + PB heuristic | PB time left | no finish (11% of the track) | - | 180,000 | 25.4 min |
