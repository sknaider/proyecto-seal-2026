#!/usr/bin/env python3
"""Benchmark NCCL efímero para auditar el clúster DGX Spark sin depender de Ray."""

import os
import statistics
import time
from datetime import timedelta

import torch
import torch.distributed as dist


def main() -> None:
    rank = int(os.environ["RANK"])
    world = int(os.environ["WORLD_SIZE"])
    size_mb = int(os.environ.get("NCCL_AUDIT_SIZE_MB", "512"))
    warmups = int(os.environ.get("NCCL_AUDIT_WARMUPS", "3"))
    iterations = int(os.environ.get("NCCL_AUDIT_ITERS", "10"))

    print(
        f"NCCL_AUDIT_INIT rank={rank} world={world} "
        f"master={os.environ.get('MASTER_ADDR')}:{os.environ.get('MASTER_PORT')}",
        flush=True,
    )
    torch.cuda.set_device(0)
    dist.init_process_group(
        "nccl", rank=rank, world_size=world, timeout=timedelta(seconds=45)
    )
    print(f"NCCL_AUDIT_CONNECTED rank={rank}", flush=True)
    elements = size_mb * 1024 * 1024 // 4
    tensor = torch.ones(elements, dtype=torch.float32, device="cuda")

    for _ in range(warmups):
        dist.all_reduce(tensor)
    torch.cuda.synchronize()
    dist.barrier()

    samples = []
    for _ in range(iterations):
        tensor.fill_(1.0)
        torch.cuda.synchronize()
        started = time.perf_counter()
        dist.all_reduce(tensor)
        torch.cuda.synchronize()
        samples.append(time.perf_counter() - started)

    expected = float(world)
    observed = float(tensor[0].item())
    avg_s = statistics.mean(samples)
    p50_s = statistics.median(samples)
    alg_gbps = size_mb * 8 / 1024 / avg_s
    bus_gbps = alg_gbps * (2 * (world - 1) / world)
    print(
        f"NCCL_AUDIT rank={rank} world={world} size_mb={size_mb} "
        f"avg_ms={avg_s * 1000:.3f} p50_ms={p50_s * 1000:.3f} "
        f"algbw_gbps={alg_gbps:.2f} busbw_gbps={bus_gbps:.2f} "
        f"value={observed:.1f} expected={expected:.1f} "
        f"valid={observed == expected}",
        flush=True,
    )
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
