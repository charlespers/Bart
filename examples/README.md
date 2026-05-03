# Examples

This folder holds smoke-test fixtures you can use to verify bart works on your machine without spending real API tokens.

## Usage

```bash
# 1. Copy the sample notes into materials/
cp examples/sample_notes.md materials/

# 2. Run bart in dry-run mode — extracts files, builds corpus, plans, but
#    does NOT call the Anthropic API.
./run run --dry-run
```

You should see:
- The bart splash (ASCII loaf + wordmark)
- A "✓ extracted sample_notes.md" line
- A corpus summary
- "Dry run — stopping before API calls."

If all of that prints without errors, your install is healthy and you're ready to drop real materials and run for real.
