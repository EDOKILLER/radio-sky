# -*- coding: utf-8 -*-
"""Icona di Cielo radio: disco del cielo diviso in due (occhio nudo | radio), antenna a dipolo
come quelle di LWA, onde radio dorate. © 2026 Edoardo Battistel"""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

N = 2048                     # disegno grande, poi rimpicciolito (bordi lisci)


def crea(cartella):
    cartella = Path(cartella)
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:N, 0:N].astype(float) / N
    # fondo blu notte con alone
    r = np.hypot(xx - 0.5, yy - 0.45)
    fondo = np.zeros((N, N, 4))
    fondo[..., 0] = 10 + 25 * np.exp(-r * 4)
    fondo[..., 1] = 12 + 22 * np.exp(-r * 4)
    fondo[..., 2] = 30 + 55 * np.exp(-r * 4)
    fondo[..., 3] = 255
    img = Image.fromarray(fondo.clip(0, 255).astype(np.uint8), "RGBA")
    # angoli arrotondati
    maschera = Image.new("L", (N, N), 0)
    ImageDraw.Draw(maschera).rounded_rectangle([0, 0, N - 1, N - 1], radius=int(N * 0.19), fill=255)
    # disco del cielo
    cx, cy, R = 0.5, 0.44, 0.36
    dx, dy = xx - cx, yy - cy
    rr = np.hypot(dx, dy) / R
    dentro = rr <= 1
    # fascia della Via Lattea inclinata
    ang = math.radians(-32)
    dist = (dx * math.sin(ang) + dy * math.cos(ang)) / R
    lungo = (dx * math.cos(ang) - dy * math.sin(ang)) / R
    fascia = np.exp(-(dist / 0.17) ** 2) * (0.55 + 0.45 * np.exp(-(lungo / 0.5) ** 2))
    nodi = np.zeros_like(fascia)
    for _ in range(26):
        a, b = rng.uniform(-0.9, 0.9), rng.normal(0, 0.07)
        px = cx + R * (a * math.cos(ang) + b * math.sin(ang))
        py = cy + R * (-a * math.sin(ang) + b * math.cos(ang))
        nodi += rng.uniform(0.2, 0.7) * np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * (R * rng.uniform(0.015, 0.04)) ** 2))
    destra = dx > -dy * 0.25                  # linea di divisione leggermente obliqua
    # metà radio: colori «inferno»
    t = np.clip(0.08 + 0.85 * fascia + 0.6 * nodi, 0, 1)
    lut = np.array([[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21],
                    [250, 193, 39], [252, 255, 164]], float)
    k = t * (len(lut) - 1)
    i0 = np.clip(k.astype(int), 0, len(lut) - 2)
    f = (k - i0)[..., None]
    radio = lut[i0] * (1 - f) + lut[i0 + 1] * f
    # metà occhio: blu scuro, Via Lattea lattiginosa debole, stelle
    occhio = np.zeros((N, N, 3))
    occhio[..., 0] = 8 + 70 * fascia
    occhio[..., 1] = 12 + 75 * fascia
    occhio[..., 2] = 40 + 95 * fascia
    cielo = np.where(destra[..., None], radio, occhio)
    a = np.array(img).astype(float)
    a[..., :3] = np.where(dentro[..., None], cielo, a[..., :3])
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(img)
    # stelle e costellazione (Cassiopea) nella metà occhio
    W = [(0.26, 0.30), (0.31, 0.37), (0.36, 0.31), (0.41, 0.38), (0.45, 0.30)]
    for (x1, y1), (x2, y2) in zip(W, W[1:]):
        d.line([(x1 * N, y1 * N), (x2 * N, y2 * N)], fill=(170, 200, 255, 200), width=int(N * 0.006))
    for x, y in W:
        rs = N * 0.012
        d.ellipse([x * N - rs, y * N - rs, x * N + rs, y * N + rs], fill=(255, 250, 235, 255))
    for _ in range(70):
        x, y = rng.uniform(0.15, 0.85), rng.uniform(0.1, 0.8)
        if math.hypot(x - cx, y - cy) > R * 0.97 or (x - cx) > -(y - cy) * 0.25:
            continue
        rs = N * rng.uniform(0.002, 0.006)
        d.ellipse([x * N - rs, y * N - rs, x * N + rs, y * N + rs], fill=(235, 240, 255, 255))
    # bordo del disco e linea di divisione dorata
    d.ellipse([(cx - R) * N, (cy - R) * N, (cx + R) * N, (cy + R) * N], outline=(230, 200, 105, 255), width=int(N * 0.012))
    # divisione: punti del cerchio con dx = -dy*0.25
    th = math.atan2(1, -0.25)
    p1 = (cx + R * math.cos(th + math.pi), cy + R * math.sin(th + math.pi))
    p2 = (cx + R * math.cos(th), cy + R * math.sin(th))
    d.line([(p1[0] * N, p1[1] * N), (p2[0] * N, p2[1] * N)], fill=(230, 200, 105, 255), width=int(N * 0.01))
    # antenna a dipolo (come LWA) sotto il disco
    ax, ay = 0.5, 0.905
    col = (236, 206, 112, 255)
    d.line([(ax * N, (ay - 0.12) * N), (ax * N, ay * N)], fill=col, width=int(N * 0.018))
    for s in (-1, 1):
        d.line([(ax * N, (ay - 0.11) * N), ((ax + s * 0.13) * N, (ay - 0.03) * N)], fill=col, width=int(N * 0.016))
        d.line([(ax * N, (ay - 0.085) * N), ((ax + s * 0.09) * N, (ay - 0.025) * N)], fill=col, width=int(N * 0.012))
    d.line([((ax - 0.16) * N, ay * N), ((ax + 0.16) * N, ay * N)], fill=(150, 130, 80, 255), width=int(N * 0.012))
    # onde radio dorate che salgono dall'antenna verso il cielo (a destra)
    for k, rr_ in enumerate((0.07, 0.11, 0.15)):
        bb = [(ax + 0.1 - rr_) * N, (ay - 0.2 - rr_) * N, (ax + 0.1 + rr_) * N, (ay - 0.2 + rr_) * N]
        d.arc(bb, start=-70, end=10, fill=(245, 215, 120, 255 - 50 * k), width=int(N * 0.012))
    img.putalpha(Image.fromarray(np.minimum(np.array(img)[..., 3], np.array(maschera))))
    grande = img.filter(ImageFilter.GaussianBlur(N / 1500))
    cartella.mkdir(parents=True, exist_ok=True)
    grande.resize((512, 512), Image.LANCZOS).save(cartella / "cielo_radio.png")
    grande.resize((256, 256), Image.LANCZOS).save(cartella / "icona.png")
    grande.resize((256, 256), Image.LANCZOS).save(cartella / "cielo_radio.ico",
                                                   sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return cartella / "cielo_radio.png"


if __name__ == "__main__":
    print(crea(sys.argv[1] if len(sys.argv) > 1 else "."))
