# Large Array Practices

Inspect metadata and bounded samples before whole-array operations. Estimate output size, chunk behavior, memory, and serialization limits. Keep large arrays and full logs local to the artifact/run store; model-visible context should contain only approved summaries or bounded samples. Record downsampling and aggregation decisions as provenance, not hidden frontend behavior.
