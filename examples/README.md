# Examples

Smoke-test fixtures for verifying bart works on your machine without spending real API tokens.

## Usage

```bash
# 1. Copy the sample notes into materials/
cp examples/sample_notes.md materials/

# 2. Run bart in dry-run mode — extracts files, builds corpus, plans, but
#    does not call the Anthropic API.
./run run --dry-run
```

Expected output:

- The bart splash (ASCII loaf + wordmark)
- An "extracted sample_notes.md" line
- A corpus summary
- "Dry run — stopping before API calls."

If all of that prints without errors, the install is healthy. Drop real materials and run for real.
