# Federation-v3 4096-bit/3-hash pilot

This isolated suite repeats the four matched single-seed workloads from the
2048-bit pilot with only the Bloom filter size changed to 4096 bits. It preserves
the 2048-bit raw failure and never writes an overall report.

The fixed order is 512-key Zipf, 512-key Uniform, 1,000-key Zipf, and 1,000-key
Uniform. Each cluster has the stated catalog size and every run contains 3,584
measured requests. Wire-v2 REQUEST frames are 538 bytes, or 542 bytes with the TCP
length prefix. Three fixed salted FNV-1a hashes and max hop count 12 are retained.

Run from the repository root:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf4096_h3_suite/run_all.sh
```

Raw runs are stored under `results/federation-v3-bf4096-h3-pilot/`; exploratory
outputs are stored under `paper_assets/federation-v3-bf4096-h3-pilot/`. Any
incomplete run stops the sequence and is never overwritten or retried.
