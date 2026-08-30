# Role A USB handoff

Copy these files onto a USB stick (8 GB is enough; 16 GB is comfortable).
After cloning the GitHub repo, overlay them onto `data/processed/`.

Do **not** copy `data/raw/`, `data/interim/`, `batch_instance.parquet`,
`batch_task.parquet`, `machine_usage.parquet`, or `time_grid_chunks/`.

The kit including the Google laptop sample (~80 MB) is about **4.8 GB**.

| File | Bytes | SHA256 |
|------|------:|--------|
| `data/processed/primary/instance_events.parquet` | 612,654,449 | `68133b133f92d6124d5ea1ae12031e02ad19f7f0d0d7ae8eec03b845e201674d` |
| `data/processed/primary/time_grid.parquet` | 4,274,444 | `9618316d0bd1f119d85500c9523a13985d322e78305daa39259e92c5698e66d0` |
| `data/processed/replication/instance_events.parquet` | 619,873,054 | `374904ef6cb292168e9907fbc56f602a5aaa15978441a725bb8831d95f06782c` |
| `data/processed/replication/time_grid.parquet` | 4,262,958 | `2a2f2e7f5be3831afa7eccc536600fc3538ccfcea7ca272e8228b2d00505000f` |
| `data/processed/google/attempts.parquet` | 3,808,473,152 | `1872da3bc20d465b0e54480432699a93aa64037a6951009350a667c020324879` |
| `data/processed/google/attempts_sample.parquet` | 83,956,886 | `df708ad9cb91aa5c1e4c77c8541616d4c3095090becf0ab56c9b451114b4d26f` |
| `data/processed/google/export_manifest.json` | 1,018 | `4d8311257b7e558e5a2c4df8120960a9572de4ee7d63bd0dc6774412680c27c5` |
| `data/processed/spec_power_curves.parquet` | 144,732 | `46270f31823f3e161090d74eac720a8f57c2c5caf0f8da8aafa92abe3b1ef57f` |
| `data/processed/primary_time_split.json` | 640 | `a437045ca1a2d12ecc404e539546394ed130e04017738f4b75a031c3e64d3350` |
| `data/processed/rack_machine_ids.json` | 692 | `f05baf75d6e2fbc65ed29742cbb9f0f5e979cfeeb012a68b592b7b2194833bac` |
| `data/processed/replication_rack_machine_ids.json` | 696 | `01ea2c5334001b6a2220fb388616a16e0d39ac77c8a824ad4d75cbc1fd5cddfc` |

Total present: **4.78 GiB** (5,133,642,721 bytes).

After copying, compare each SHA256 above. Role B is **frozen** (see `docs/role_b.md`). Overlay this Role A kit first, then generate `reports/role_b_handoff.parquet` and `models/primary_hgb_frozen.joblib` with `fpce-role-b-freeze` (or copy those freeze artifacts if they are provided separately). `fpce-cross-provider` on a laptop should use `attempts_sample.parquet` (the default if that file exists). Use the full `attempts.parquet` only on a machine with 16+ GB RAM.

