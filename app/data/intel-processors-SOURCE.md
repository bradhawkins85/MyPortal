# Intel CPU reference snapshot

`intel_cpus.csv` is a reduced, merged import of the root CSV files from
https://github.com/toUpperCase78/intel-processors, retrieved 12 November 2026.
The upstream author attributes the specifications to Intel ARK.
Upstream is licensed under GPL-3.0; its full license is preserved in
`intel-processors-LICENSE.md`. MyPortal's license remains in the repository root.

The downloaded master archive SHA-256 was
`1eb6eb3ddbb61c746302a23d75babe7ddae99b3c2eef5191252e378e4f69a121`.
Each row records its upstream filename. Newer numeric versions take precedence
for the same product; older files retain legacy processors no longer listed in
newer exports. The snapshot contains 3,014 models and 415 temperature references.

To update, download and extract a trusted upstream snapshot, then run:

```sh
python3 scripts/import_intel_cpus.py /path/to/intel-processors
pytest tests/test_cpu_catalog.py -q
```

Review changes and update this provenance record before committing the CSV.
The application performs no network fetch at startup or during asset requests.

Release dates retain their original quarter. Missing asset ages are estimated
from the middle of that quarter at read time, so they remain current without
overwriting explicit ages. This is CPU model age, not purchase or installation age.
Model matching removes Intel branding, trademark markers, CPU/processor labels
and trailing `@` clock speeds; model suffixes and other variants stay distinct.
Unknown models and unavailable temperatures stay unknown.

Maximum temperature values are references, not validated sensor alert thresholds:
upstream substitutes T-junction or T-base values when an operating limit is
unavailable and uses the higher value when both exist. Health-check AI receives
the reference and that qualification. Existing fixed temperature alerts remain
in effect; verify the Intel specification and sensor type before changing them.
