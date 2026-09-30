"""A server-rendered administration surface.

Deliberately plain: a single-page application would be a second
deployment and a second dependency tree to keep patched, for pages that
are lists and forms.

Not part of the public API and not versioned with it. Nothing here is
reachable without a superuser session, which travels in its own cookie
because a browser loading a page cannot present a bearer token.
"""
