"""Deletion-intent classification for request_text (multilingual, rule based).

A request authorizes deletion unless the requester explicitly asks for a different
action (access, export, correction, marketing opt-out, login help) or explicitly says
not to delete. Follow-up requests ("verify that my account was deleted", "confirm the
profile is gone") still authorize the deletion audit.

Negative (non-deletion) markers are checked first, so "please don't delete my
account" is never read as a deletion request.
"""
from __future__ import annotations

import re

NEGATIVE_PATTERNS = [
    # English
    r"not asking for deletion",
    r"not a request to (erase|delete)",
    r"\bdo not (close|delete|remove|erase)\b",
    r"\bdon'?t (delete|close|remove|erase)\b",
    r"\bleave (the|my) (account|profile)",
    r"\bkeep (the|my) account\b",
    r"without removing",
    r"continue using the service",
    r"marketing lists only",
    r"stop the promotional emails",
    r"\b(replace|change|correct)\b.*\b(number|email address|surname|name)\b",
    r"\bcopy of (the information|last year's invoices|my data)",
    r"download my account information",
    r"what personal information you hold",
    r"\bresetting the login\b",
    # other languages: explicit "do not delete" / access-only phrasing
    r"\bno (elimin|borr|cancel)\w*",                     # es / it
    r"\bne (pas )?(supprim|effac)\w*", r"\bne supprimez pas\b",  # fr
    r"\bnão (exclu|apag|remov)\w*",                      # pt
    r"\bnicht (löschen|entfernen|schließen)\b", r"\bkeine löschung\b",  # de
    r"\bniet (verwijderen|wissen)\b",                     # nl
    r"削除しないで", r"삭제하지\s*마",
    r"copia de mis datos", r"copie de mes données", r"kopie meiner daten",
]

DELETION_PATTERNS = [
    # English
    r"\bdelet", r"\berase", r"\berasure\b", r"\bremov", r"\bclos(e|ed|ing) (my|this|the) account",
    r"account (was|is|had been) deleted", r"\bgone\b", r"\bdeletion\b",
    # French / Portuguese / Spanish / Italian
    r"suppression", r"\bsupprim", r"\beffac", r"exclus[aã]o", r"\bexclu[ií]r", r"\bapagar",
    r"eliminaci[oó]n", r"\belimin", r"\bborra", r"\bcancella",
    # German / Dutch / Nordic / Polish
    r"lösch", r"\bentfern", r"verwijder", r"\bslet", r"radera", r"\busun",
    # CJK and others
    r"削除", r"삭제", r"删除", r"注销", r"удал", r"حذف", r"\bsil(in|me)",
]


def authorizes_deletion(text: str) -> bool:
    t = (text or "").lower()
    if any(re.search(p, t) for p in NEGATIVE_PATTERNS):
        return False
    return any(re.search(p, t) for p in DELETION_PATTERNS)
