# Resumen del benchmark

Actualizado: 2026-11-01 16:09:53 CST  
Estado: terminado  
Carpeta: `C:\Users\chapp\proyectos\benchmark\results`

| Ronda | Con fila | ok | spill | oom | error | Descartes | Agotadas |
|---|---|---|---|---|---|---|---|
| 1 | 38/38 | 29 | 7 | 2 | 0 | 0 | 0 |
| 2 | 38/38 | 29 | 7 | 2 | 0 | 0 | 0 |
| 3 | 38/38 | 29 | 7 | 2 | 0 | 0 | 0 |

## Corridas spill, oom y error

| Ronda | Celda | status | P99 (ms) | Throttling (s) |
|---|---|---|---|---|
| 1 | postgres PROFILE_B_MEDIUM Q1 c10 | spill | 70775.207 | 1488.83 |
| 1 | postgres PROFILE_C_SEVERE Q1 c1 | spill | 11142.058 | 146.954 |
| 1 | postgres PROFILE_A_BASELINE Q1 c10 | spill | 82581.398 | 158.522 |
| 1 | postgres PROFILE_C_SEVERE Q1 c50 | oom | — | 3158.305 |
| 1 | postgres PROFILE_A_BASELINE Q1 c50 | spill | 188247.902 | 555.365 |
| 1 | postgres PROFILE_B_MEDIUM Q1 c50 | oom | 176270.465 | 1026.589 |
| 1 | postgres PROFILE_B_MEDIUM Q1 c1 | spill | 5343.15 | 82.902 |
| 1 | postgres PROFILE_A_BASELINE Q1 c1 | spill | 2790.327 | 0.014 |
| 1 | postgres PROFILE_C_SEVERE Q1 c10 | spill | 105002.076 | 2232.364 |
| 2 | postgres PROFILE_B_MEDIUM Q1 c1 | spill | 3282.911 | 114.447 |
| 2 | postgres PROFILE_C_SEVERE Q1 c1 | spill | 8496.605 | 284.227 |
| 2 | postgres PROFILE_C_SEVERE Q1 c50 | oom | — | 2623.895 |
| 2 | postgres PROFILE_A_BASELINE Q1 c1 | spill | 2757.733 | 0.042 |
| 2 | postgres PROFILE_B_MEDIUM Q1 c10 | spill | 74661.836 | 510.745 |
| 2 | postgres PROFILE_C_SEVERE Q1 c10 | spill | 97429.236 | 1991.041 |
| 2 | postgres PROFILE_A_BASELINE Q1 c10 | spill | 82456.254 | 77.39 |
| 2 | postgres PROFILE_A_BASELINE Q1 c50 | spill | 211003.424 | 179.24 |
| 2 | postgres PROFILE_B_MEDIUM Q1 c50 | oom | 131863.613 | 990.054 |
| 3 | postgres PROFILE_C_SEVERE Q1 c1 | spill | 8232.957 | 119.061 |
| 3 | postgres PROFILE_A_BASELINE Q1 c50 | spill | 210293.921 | 118.031 |
| 3 | postgres PROFILE_A_BASELINE Q1 c10 | spill | 86204.525 | 241.533 |
| 3 | postgres PROFILE_A_BASELINE Q1 c1 | spill | 2940.986 | 0.014 |
| 3 | postgres PROFILE_B_MEDIUM Q1 c50 | oom | 157157.018 | 875.209 |
| 3 | postgres PROFILE_C_SEVERE Q1 c10 | spill | 110414.37 | 2271.428 |
| 3 | postgres PROFILE_B_MEDIUM Q1 c10 | spill | 77263.405 | 688.262 |
| 3 | postgres PROFILE_C_SEVERE Q1 c50 | oom | — | 2637.274 |
| 3 | postgres PROFILE_B_MEDIUM Q1 c1 | spill | 5788.733 | 67.87 |

## Tamaños y espacio

- `results/`: 12.8 MiB
- `evidence/`: 0.2 MiB
- Carpeta de esta ejecución: 12.8 MiB
- Libre en C:: 327.2 GB

## Avisos del entorno

- No se pudo comprobar con powercfg: accion_tapa (no cierres la tapa durante la matriz)
