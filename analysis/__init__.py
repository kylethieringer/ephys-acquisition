"""
Offline analysis and quality control for saved recordings.

Nothing in this package touches hardware — every module here operates on files
that acquisition already wrote to disk, so it is safe to import and run on any
machine.

Contents
--------
- :mod:`analysis.analyze_steps` — input resistance and step-response analysis
- :mod:`analysis.align_video` — align video frames to camera TTL edges
- :mod:`analysis.detect_spikes` — spike detection
- :mod:`analysis.batch_intrinsics` — batch intrinsic-property extraction
- :mod:`analysis.summary_figures` — summary figure generation
- :mod:`analysis.qc` — the post-recording QC pipeline
- :mod:`analysis.qc_report` — CLI to re-run QC on an existing recording
- :mod:`analysis.qc_alignment` — CLI for the standalone rig alignment check
"""