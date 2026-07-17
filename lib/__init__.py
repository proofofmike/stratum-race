"""Shared computation core for StratumRace.

Pure logic with no storage or framework dependencies, imported by both the
cloud deployment and the standalone runtime. This is the single source of
truth for statistical computation, ensuring both modes never drift apart.
"""
