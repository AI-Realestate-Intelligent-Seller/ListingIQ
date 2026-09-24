"""The lead pool: CSV import, signal/stage vocabulary, and campaign hand-off.

Submodules are intentionally loaded on demand. Eagerly importing campaign and
SMS dependencies here makes lightweight users such as migrations and the
geocoding worker enter the SMS package through a circular import.
"""
