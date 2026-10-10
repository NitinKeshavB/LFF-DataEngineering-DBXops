# framework/local: our overlay on the Lakeflow Framework

The submodule `framework/lakeflow_framework` is read-only (CLAUDE.md P0 #13). Anything we need to add
to the deployed framework goes here instead. `make stage-framework` copies this directory over
`src/local/` of the staged copy, so it lands in the upstream's customer-owned `src/local/` area, which
upstream upgrades never touch.

```
framework/local/
  config/       sparse overrides, deep-merged over the framework's default config
  python/       org-wide modules that dataflow specs can reference
  libraries/    org-wide wheels and loose modules put on sys.path
  init/pre/     org-wide scripts run before pipeline declarations
  init/post/    org-wide scripts run after pipeline declarations
```

All subdirectories are optional. This file and `.gitkeep` files are not copied.
