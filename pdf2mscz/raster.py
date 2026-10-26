"""スキャン画像の楽譜を段 (システム) ごとの横長の帯に分ける (HITL で読みやすくするため)。"""
from __future__ import annotations


def _row_darkness(pix) -> list[float]:
    """各行の暗い画素の割合。"""
    w, h, n = pix.width, pix.height, pix.n
    data = pix.samples
    stride = pix.stride
    out = []
    step = max(1, w // 400)  # 横方向は間引いて高速化
    for y in range(h):
        row = data[y * stride:(y + 1) * stride]
        dark = 0
        cnt = 0
        for x in range(0, w, step):
            v = row[x * n]
            cnt += 1
            if v < 200:
                dark += 1
        out.append(dark / cnt)
    return out


def split_systems(pix) -> list[tuple[int, int]]:
    """五線 (横に長い線が 5 本並ぶ行) を探し、近いものを段にまとめて (y0, y1) の帯を返す。"""
    dark = _row_darkness(pix)
    h = len(dark)
    lines = [y for y in range(h) if dark[y] > 0.45]
    # 連続する行を 1 本の線にまとめる
    centers = []
    for y in lines:
        if centers and y - centers[-1][-1] <= 1:
            centers[-1].append(y)
        else:
            centers.append([y])
    ys = [sum(c) / len(c) for c in centers]
    # 等間隔の 5 本 = 五線
    staves = []
    i = 0
    while i + 4 < len(ys):
        gaps = [ys[i + k + 1] - ys[i + k] for k in range(4)]
        if max(gaps) - min(gaps) <= max(2, 0.2 * min(gaps)) and min(gaps) > 3:
            staves.append((ys[i], ys[i + 4], sum(gaps) / 4))
            i += 5
        else:
            i += 1
    if not staves:
        return [(0, h)]
    # 段頭の縦線 (左端) が五線どうしをつないでいれば同じ段
    w, n, stride, data = pix.width, pix.n, pix.stride, pix.samples

    def dark(x, y):
        return data[int(y) * stride + x * n] < 200

    def left_x(st):
        y = int(round(st[0]))
        for x in range(w):
            if dark(x, y) and dark(x, st[1]):
                return x
        return 0

    def connected(a, b):
        x0 = min(left_x(a), left_x(b))
        for x in range(max(0, x0 - 3), min(w, x0 + 6)):
            if all(dark(x, y) for y in range(int(a[1]), int(b[0]) + 1, 2)):
                return True
        return False

    systems = [[staves[0]]]
    for st in staves[1:]:
        if connected(systems[-1][-1], st):
            systems[-1].append(st)
        else:
            systems.append([st])
    out = []
    for k, sysm in enumerate(systems):
        sp = sysm[0][2]
        top = sysm[0][0] - 7 * sp
        bottom = sysm[-1][1] + 7 * sp
        if k > 0:
            top = max(top, (systems[k - 1][-1][1] + sysm[0][0]) / 2)
        if k + 1 < len(systems):
            bottom = min(bottom, (sysm[-1][1] + systems[k + 1][0][0]) / 2)
        out.append((int(max(0, top)), int(min(h, bottom))))
    return out
