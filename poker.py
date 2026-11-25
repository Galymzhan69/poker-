import random
from collections import Counter

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

def new_deck():
    deck = [(r, s) for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck

def card_text(card):
    r, s = card
    face = {"T":"10", "J":"J", "Q":"Q", "K":"K", "A":"A"}.get(r, r)
    return f"{face}{s}"

def cards_text(cards):
    return " ".join(card_text(c) for c in cards)

def evaluate_five(cards):
    vals = sorted([RANKS.index(c[0]) + 2 for c in cards], reverse=True)
    counts = Counter(vals)
    groups = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    unique = sorted(set(vals), reverse=True)
    straight_high = 0
    if len(unique) == 5:
        if unique[0] - unique[-1] == 4:
            straight_high = unique[0]
        elif unique == [14, 5, 4, 3, 2]:
            straight_high = 5

    if flush and straight_high:
        return (8, straight_high)
    if groups[0][1] == 4:
        return (7, groups[0][0], groups[1][0])
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *vals)
    if straight_high:
        return (4, straight_high)
    if groups[0][1] == 3:
        kickers = sorted([v for v in vals if v != groups[0][0]], reverse=True)
        return (3, groups[0][0], *kickers)
    if groups[0][1] == 2 and groups[1][1] == 2:
        pairs = sorted([groups[0][0], groups[1][0]], reverse=True)
        kicker = max(v for v in vals if v not in pairs)
        return (2, *pairs, kicker)
    if groups[0][1] == 2:
        kickers = sorted([v for v in vals if v != groups[0][0]], reverse=True)
        return (1, groups[0][0], *kickers)
    return (0, *vals)

HAND_NAMES = [
    "Старшая карта", "Пара", "Две пары", "Сет", "Стрит",
    "Флеш", "Фулл-хаус", "Каре", "Стрит-флеш"
]

def best_hand(cards):
    import itertools
    best = max(evaluate_five(list(combo)) for combo in itertools.combinations(cards, 5))
    return best, HAND_NAMES[best[0]]

def winner_ids(players, board):
    alive = [p for p in players if not p["folded"]]
    if not alive:
        return [], {}
    scores = {}
    for p in alive:
        score, name = best_hand(p["hole"] + board)
        scores[p["uid"]] = (score, name)
    best = max(v[0] for v in scores.values())
    winners = [uid for uid, value in scores.items() if value[0] == best]
    return winners, scores
