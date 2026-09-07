---
name: marca-de-agua
description: Inspect and clean machine-readable provenance signals from content owned or authorized by William/SOUL. Use for invisible Unicode, AI/vendor metadata, C2PA/EXIF/XMP, Markdown/HTML/SVG provenance, PNG/JPEG/WebP metadata, DOCX/ODT properties, PDF best-effort cleanup, release-byte hygiene, watermark audits, or requests to apply the pinned watermarks-remover backend safely.
---

# Marca de agua

Use the pinned, fail-closed SOUL integration. Do not execute a mutable upstream
checkout directly and do not claim universal removal.

## Workflow

1. Confirm the content is owned or authorized. Do not remove mandatory
   third-party licenses or attribution.
2. Inspect without writing:

   ```bash
   python3 research/soul_clean_marks/soul_full_clean.py inspect FILE
   ```

3. Clean to a new output and bind evidence to the promoted bytes:

   ```bash
   python3 research/soul_clean_marks/soul_full_clean.py clean FILE \
     -o FILE.cleaned --authorized-content --report FILE.evidence.json
   ```

4. Read the report. Require `VERIFIED_SUPPORTED_SIGNALS_CLEAN`, zero
   `residual_signals`, and an output SHA-256 matching the produced file.
5. Preserve the original. The integration rejects in-place cleaning and
   symlink inputs/outputs.

## Capability check

Run before PDF or media work:

```bash
python3 research/soul_clean_marks/soul_full_clean.py capabilities
```

PDF is best-effort without `exiftool` plus `qpdf`. Pixel, statistical-text,
audio/video and secret-key watermarks require scheme-specific verification.
Never translate a successful supported-signal clean into “human-written” or
“all vendor detectors defeated.”

## Product boundary

- Keep `reverse-SynthID` out of commercial SOUL: non-commercial license.
- Keep the referenced CtrlRegen/noai-watermark backend out: no published
  license.
- MarkDiffusion is Apache-2.0 but same-scheme/same-model verification only.
- Report unsupported or residual channels explicitly; fail closed by default.
