"""A server-rendered administration surface.

Deliberately plain. This is a tool a handful of people use a handful of
times a week, and a single-page application for that is a second
deployment, a second build and a second set of dependencies to keep
patched, for pages that are lists and forms.

It is not part of the public API and is not versioned with it. Nothing
here is reachable without a superuser session, and the session is its own
cookie rather than the bearer token the API uses, because a browser
loading a page cannot present one.
"""
