"""
Dropdown options. Edit this file when new sets release or if anything
below is wrong — nothing else in the app needs to change.

Legend names came from riftbound.gg's guide index plus the nine Vendetta
legends (set 4, 31 July 2026). Coverage of Origins / Spiritforged / Unleashed
may be incomplete. Add or fix names here - this file is the only place the
form gets its options from.
"""

# The six domains.
DOMAINS = ["Body", "Calm", "Chaos", "Fury", "Mind", "Order"]

LEGENDS = [
    "Ahri, Nine-Tailed Fox",
    "Akali, Rogue Assassin",
    "Ambessa, Matriarch of War",
    "Annie, Dark Child",
    "Azir, Emperor of the Sands",
    "Darius, Hand of Noxus",
    "Diana, Scorn of the Moon",
    "Draven, Glorious Executioner",
    "Ezreal, Prodigal Explorer",
    "Fiora, Grand Duelist",
    "Garen, Might of Demacia",
    "Irelia, Blade Dancer",
    "Ivern, Green Father",
    "Jax, Grandmaster at Arms",
    "Jayce, Defender of Tomorrow",
    "Jhin, Virtuoso",
    "Jinx, Loose Cannon",
    "Kai'Sa, Daughter of the Void",
    "Kennen, Heart of the Tempest",
    "Kha'Zix, Voidreaver",
    "LeBlanc, Deceiver",
    "Lee Sin, Blind Monk",
    "Leona, Radiant Dawn",
    "Lillia, Bashful Bloom",
    "Lucian, Purifier",
    "Lux, Lady of Luminosity",
    "Master Yi, Wuju Bladesman",
    "Master Yi, Wuju Master",
    "Mel, Soul's Reflection",
    "Miss Fortune, Bounty Hunter",
    "Nasus, Curator of the Sands",
    "Ornn, Fire Below the Mountain",
    "Poppy, Keeper of the Hammer",
    "Pyke, Bloodharbor Ripper",
    "Rek'Sai, Void Burrower",
    "Renata Glasc, Chem-Baroness",
    "Renekton, Butcher of the Sands",
    "Rengar, Pridestalker",
    "Rumble, Mechanized Menace",
    "Sett, The Boss",
    "Shen, Eye of Twilight",
    "Sivir, Battle Mistress",
    "Teemo, Swift Scout",
    "Vex, Gloomist",
    "Vi, Piltover Enforcer",
    "Viktor, Herald of the Arcane",
    "Volibear, Relentless Storm",
    "Yasuo, Unforgiven",
    "Zed, Master of Shadows",
]

EVENT_TYPES = [
    "Nexus Night",
    "Summoner Skirmish",
    "Pre Rift",
    "Online",
]

# Riftbound sets, in release order. Add new ones here as they drop.
SETS = [
    "Origins",
    "Spiritforged",
    "Unleashed",
    "Vendetta",
]

# Pack-opening products you can log a pull from.
PRODUCTS = [
    "Single Pack",
    "Sleeve",
    "Prize Booster Pack",
    "Prize Nexus Night Pack",
    "Vault",
    "Starter Deck Pack",
    "Pre Rift Kit",
    "Bundle Box",
    "Other",
]

# Ordered (column_name, display_label) for each hit_* column on openings.
# The order here drives both the form's hit grid and the recent-openings total.
HIT_TYPES = [
    ("hit_rare",       "Rare"),
    ("hit_leader",     "Leader"),
    ("hit_dbl_leader", "Double Leader"),
    ("hit_epic",       "Epic"),
    ("hit_fa_rune",    "Full Art Rune"),
    ("hit_sig_spell",  "Signature Spells"),
    ("hit_alt_art",    "Alt Art"),
    ("hit_sp_rare",    "SP Rare"),
    ("hit_overnumber", "Overnumber"),
    ("hit_signature",  "Signature"),
    ("hit_ultimate",   "Ultimate"),
    ("hit_nn_chase",   "Nexus Night Chase"),
]

# Columns summed into the "Hits" total shown in the recent-openings table.
# Rare and Leader come guaranteed in every pack, so they'd inflate the count
# without signalling anything — the total is meant to reflect notable pulls.
# They still appear on the form and in the stored data; they just don't count
# toward the summary.
_TOTAL_EXCLUDE = {"hit_rare", "hit_leader"}
TOTAL_HIT_TYPES = [(c, l) for c, l in HIT_TYPES if c not in _TOTAL_EXCLUDE]

# Hit types tracked on the Box Hit tab. Boxes don't track Rare, Leader, or
# Nexus Night Chase, so they're dropped here. The boxes table still has those
# columns (they just stay 0) - only the form and totals use this subset.
_BOX_EXCLUDE = {"hit_rare", "hit_leader", "hit_nn_chase"}
BOX_HIT_TYPES = [(c, l) for c, l in HIT_TYPES if c not in _BOX_EXCLUDE]

# Hit types that get individual card-name capture on the form, so a specific
# premium pull can be looked up later (e.g. in Grafana's chase log). Each named
# card becomes one row in opening_cards / box_cards. Pulls name every hit from
# Alt Art up; boxes only from SP Rare up (boxes never track Nexus Night Chase,
# and Alt Art isn't named card-by-card there).
_NAMED_PULL_COLS = {
    "hit_alt_art", "hit_sp_rare", "hit_overnumber",
    "hit_signature", "hit_ultimate", "hit_nn_chase",
}
NAMED_HIT_TYPES = [(c, l) for c, l in HIT_TYPES if c in _NAMED_PULL_COLS]

_NAMED_BOX_COLS = {"hit_sp_rare", "hit_overnumber", "hit_signature", "hit_ultimate"}
NAMED_BOX_HIT_TYPES = [(c, l) for c, l in BOX_HIT_TYPES if c in _NAMED_BOX_COLS]
