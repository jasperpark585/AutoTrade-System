"""Operational agent harness helpers.

The default harness is deterministic and offline. It prepares a clean contract
that can later be wrapped by the OpenAI Agents SDK without letting an LLM place
orders or mutate brokerage state.
"""
