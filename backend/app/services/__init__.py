"""Service layer.

Every Anthropic call goes through ``content_generator``; every Twilio call goes
through ``delivery_service`` (CLAUDE.md, Backend Conventions). No SDK client is
ever instantiated inside a route handler.
"""
