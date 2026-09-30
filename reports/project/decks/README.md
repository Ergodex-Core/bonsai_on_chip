# Bonsai-on-chip weekly updates

The editable five-slide [30 September progress deck](Bonsai-on-chip-progress-2026-09-30.pptx)
uses an evidence cutoff of 17:40 UTC. Routing and native Arcilator validation were
pending at that cutoff. Consult the current ERG-103 report for later results.

Copy the [blank weekly template](Bonsai-on-chip-weekly-template.pptx) for each update:

1. Report week/date, overall milestone and main open gate.
2. Verified progress with workload, source revision and evidence links.
3. Execution status, simulator cycles/second, operation latency and FPGA clock/timing.
4. Design changes, implementation boundary, interfaces and capacity decisions.
5. Next deliverables, owners, planned dates and acceptance evidence.

Replace every bracketed field and refresh speaker-note citations. Keep missing
or failed evidence visible. Simulator cycles per wall-clock second depend on
host, backend, workload and included host work; they are separate from a modeled
clock or a timing-closed FPGA clock. Label emulation, estimates and physical
measurements explicitly. Each issue delivers a PR, runnable simulation, actual
FPGA evidence for its scope and an attached report. Design-only proposals stay
separate from implemented features.

Both decks contain five16:9 slides, editable text and native tables. Package,
layout, font-policy and first-party import checks passed; all rendered slides
were visually inspected. Native PowerPoint execution was not tested.
