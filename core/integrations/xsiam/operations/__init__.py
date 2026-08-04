# core/integrations/xsiam/operations/__init__.py
"""Declarative catalog of Cortex XSIAM Platform API operations.

One YAML pack per API category under ``packs/`` describes every callable
operation (method, path, access class, consent). Mirrors the Tool Adapter
Framework: a single Pydantic schema is the source of truth, the loader
reject-and-logs (never raises), and a cleared-and-rebuilt dict singleton is
loaded at boot. The gated executor in ``core/api/xsiam.py`` dispatches ops
through ``XsiamClient.request``.
"""
