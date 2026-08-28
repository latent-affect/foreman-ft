"""Local MCP server exposing the read-only atlas.query.facade.QueryFacade over stdio.

Wraps QueryFacade unchanged -- this package adds no new safety logic of its own. All read-only
enforcement, view allowlisting, and trust-gate refusal live in facade.py.
"""
