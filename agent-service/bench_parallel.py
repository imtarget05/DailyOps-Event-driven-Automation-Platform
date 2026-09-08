"""Benchmark: T_sequential vs T_parallel + end-to-end supervisor latency."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from src.orchestrator import supervisor as sup

N = 5
NAMES = ["business_analyst", "knowledge_agent", "operations_agent"]
PAYLOAD = {"stock": 15, "avg_daily_sales": 8}


async def main() -> None:
    seq_ts, par_ts = [], []
    for i in range(N):
        t0 = time.perf_counter()
        await sup.run_specialists_sequential(NAMES, "inventory_check", "product_A", PAYLOAD, f"bench_{i}")
        seq_ts.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        await sup.run_specialists(NAMES, "inventory_check", "product_A", PAYLOAD, f"bench_{i}")
        par_ts.append((time.perf_counter() - t0) * 1000)
    t_seq = sum(seq_ts) / len(seq_ts)
    t_par = sum(par_ts) / len(par_ts)

    t0 = time.perf_counter()
    s = await sup.supervise("bench_e2e", "inventory_check", "product_A", PAYLOAD, {})
    t_sup = (time.perf_counter() - t0) * 1000

    print(f"T_sequential_avg_ms : {t_seq:.1f}")
    print(f"T_parallel_avg_ms   : {t_par:.1f}")
    print(f"parallel_speedup    : {t_seq / t_par:.2f}x")
    print(f"supervisor_e2e_ms   : {t_sup:.1f} (action={s['action_type']}, "
          f"specialists={s['specialists_used']})")


asyncio.run(main())
