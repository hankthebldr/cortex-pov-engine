"""
Built-in EAL simulator plugins.

Adding a plugin:

    1. Drop a new Python file in this directory.
    2. Define a class that inherits from ``BaseSimulation``.
    3. Set ``Meta.name``, ``Meta.params_model`` (Pydantic model) and
       implement ``async def run(self, ctx) -> SimulationResult``.

The plugin registry imports every module in this package on first use; no
further wiring is required.
"""
