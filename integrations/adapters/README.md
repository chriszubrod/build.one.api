# Adapter Naming Guidelines

The adapter package centralizes persistence mappings that connect Build One
entities with external providers. Each adapter module should follow the
conventions below so the integration layer can remain reusable across systems:

- Modules must use the pattern `map_<source>_to_<target>.py`.
- Each module must define a dataclass named `Map<Source>To<Target>`.
- Use the :func:`integrations.adapters.register_adapter` decorator on the
  dataclass to enforce the naming rule at import time.
- Helper functions should follow the same `map_<source>_to_<target>` naming
  convention (for example, `create_map_vendor_to_intuit_vendor`).

Following these conventions ensures that new providers can reuse the existing
adapter layer instead of introducing bespoke persistence modules.
