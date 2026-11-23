"""Reconnaissance locale de la commande finale, sans dépendance externe."""
import re
import unicodedata


def normalize(word):
    return ''.join(c for c in unicodedata.normalize('NFD', word.casefold())
                   if not unicodedata.combining(c))


def is_finish_word(word):
    """Au plus 15 % d'éditions de caractères (Levenshtein / longueur maximale)."""
    word = normalize(word)
    reference = 'termine'
    length = max(len(word), len(reference))
    if abs(len(word)-len(reference)) * 100 > 15 * length:
        return False
    previous = list(range(len(reference)+1))
    for i, letter in enumerate(word, 1):
        current = [i]
        for j, expected in enumerate(reference, 1):
            current.append(min(current[-1]+1, previous[j]+1,
                               previous[j-1]+(letter != expected)))
        previous = current
    return previous[-1] * 100 <= 15 * length


def finish_command(text):
    """Retourne le dernier mot seulement s'il correspond à la commande."""
    match = re.search(r'(?:^|\s)([^\s.!?,;:…]+)[\s.!?,;:…]*$', text)
    return match if match and is_finish_word(match.group(1)) else None
