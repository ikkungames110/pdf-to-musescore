"""SMuFL コードポイントの分類表 (MuseScore が PDF に書き出す音楽フォント用)。"""

NOTEHEAD_KIND = {}  # code -> (長さの種類, 符頭の形)
for _c in range(0xE0A0, 0xE100):
    NOTEHEAD_KIND[_c] = ("black", "normal")
NOTEHEAD_KIND.update({
    0xE0A0: ("breve", "normal"), 0xE0A1: ("breve", "normal"),
    0xE0A2: ("whole", "normal"), 0xE0A3: ("half", "normal"), 0xE0A4: ("black", "normal"),
    0xE0A6: ("breve", "cross"), 0xE0A7: ("whole", "cross"), 0xE0A8: ("half", "cross"), 0xE0A9: ("black", "cross"),
    0xE0B0: ("breve", "xcircle"), 0xE0B1: ("whole", "xcircle"), 0xE0B2: ("half", "xcircle"), 0xE0B3: ("black", "xcircle"),
    0xE0B4: ("breve", "plus"), 0xE0B5: ("whole", "plus"), 0xE0B6: ("half", "plus"), 0xE0B7: ("black", "plus"),
    0xE0D7: ("breve", "diamond"), 0xE0D8: ("whole", "diamond"), 0xE0D9: ("half", "diamond"), 0xE0DB: ("black", "diamond"),
    0xE0DD: ("half", "diamond"), 0xE0DE: ("half", "diamond"),
})
for _c in range(0xE100, 0xE110):  # slash 符頭
    NOTEHEAD_KIND[_c] = ("black", "slash")
NOTEHEAD_KIND.update({0xE101: ("whole", "slash"), 0xE102: ("whole", "slash"), 0xE103: ("half", "slash"), 0xE104: ("half", "slash")})
for _c in range(0xE1C0, 0xE1D0):  # 三角などの形状符頭 (shape notes) は黒扱い
    NOTEHEAD_KIND.setdefault(_c, ("black", "normal"))

CLEFS = {
    0xE050: "G", 0xE051: "G15mb", 0xE052: "G8vb", 0xE053: "G8va", 0xE054: "G15ma",
    0xE05C: "C", 0xE062: "F", 0xE064: "F15mb", 0xE065: "F8vb", 0xE066: "F8va", 0xE067: "F15ma",
    0xE069: "PERC", 0xE06A: "PERC",
}
# 第 5 線 (一番上の線) の全音階番号 (octave*7 + C=0..B=6)
CLEF_TOP = {
    "G": 38, "G8vb": 31, "G8va": 45, "G15mb": 24, "G15ma": 52,
    "F": 26, "F8vb": 19, "F8va": 33, "F15mb": 12, "F15ma": 40,
    "PERC": 38,
    "C1": 36, "C2": 34, "C3": 32, "C4": 30, "C5": 28,
}

TIMESIG_DIGITS = {0xE080 + i: i for i in range(10)}
TIMESIG_COMMON = 0xE08A
TIMESIG_CUT = 0xE08B

ACCIDENTALS = {0xE260: -1, 0xE261: 0, 0xE262: 1, 0xE263: 2, 0xE264: -2}

FLAGS = {  # code -> 旗の本数
    0xE240: 1, 0xE241: 1, 0xE242: 2, 0xE243: 2, 0xE244: 3, 0xE245: 3,
    0xE246: 4, 0xE247: 4, 0xE248: 5, 0xE249: 5,
}

RESTS = {
    0xE4E2: "breve", 0xE4E3: "whole", 0xE4E4: "half", 0xE4E5: "quarter", 0xE4E6: "eighth",
    0xE4E7: "16th", 0xE4E8: "32nd", 0xE4E9: "64th", 0xE4EA: "128th",
    0xE4F3: "breve", 0xE4F4: "whole", 0xE4F5: "half",
}
MMREST = (0xE4EE, 0xE4EF, 0xE4F0, 0xE4F1)

AUG_DOT = 0xE1E7

# テキスト中に音楽フォントで書かれる記号
TEXT_SUBST = {0xE260: "♭", 0xE262: "♯", 0xE261: "♮", 0xE870: "°", 0xE871: "ø", 0xE873: "Δ"}
TEMPO_NOTES = {
    0xECA5: "quarter", 0xECA7: "eighth", 0xECA3: "half", 0xECA2: "whole",
    0xE1D5: "quarter", 0xE1D7: "eighth", 0xE1D3: "half", 0xE1D2: "whole",
}
