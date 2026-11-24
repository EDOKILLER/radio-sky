# -*- coding: utf-8 -*-
"""
CIELO RADIO - MOTORE
====================
© 2026 Edoardo Battistel

Il cielo sopra un luogo, adesso, come lo vedrebbe un occhio radio (10 MHz - 94 GHz).

- Modelli del cielo radio diffuso:
    GSM2008  de Oliveira-Costa et al. 2008, MNRAS 388, 247 (10 MHz - 94 GHz, HEALPix nside 512, galattiche)
    LFSM     Dowell et al. 2017, MNRAS 469, 4537 (LWA1, 10 - 408 MHz, HEALPix nside 256, equatoriali)
  dati dello stesso pacchetto pygdsm (Zenodo record 3835582), letti una volta con h5py e poi
  tenuti come .npy. La combinazione delle componenti è quella di pygdsm (PCHIP per GSM2008,
  lineare + spline cubica per LFSM), rifatta qui con numpy: niente pygdsm, niente pyephem.
- HEALPix, coordinate (tempo siderale, precessione IAU 1976), Sole, Luna e pianeti
  (metodo di Paul Schlyter, ~1-2') scritti a mano: niente astropy.
- Vista occhio: mappa del cielo di Axel Mellinger (SkyView, rosso/verde/blu) + stelle d3-celestial.
- Sole radio: flussi RSTN di NOAA SWPC; Luna e pianeti come corpi neri (Rayleigh-Jeans), indicativi.
- LWA TV: immagini di tutto il cielo delle stazioni LWA (New Mexico), 10-88 MHz, in diretta.
- Disegno con Pillow: la stessa immagine va nella finestra, nei PNG e nei video MP4 (ffmpeg).
"""

import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

VERSIONE = "1.0"
FIRMA = "© 2026 Edoardo Battistel"
UA = "CieloRadio/1.0 (programma personale di astronomia; Python urllib)"

CARTELLA = Path(os.path.expanduser("~/Scrivania/Astronomia/Cielo_radio"))
CARTELLA_EINSTEIN = Path(os.path.expanduser("~/Scrivania/Astronomia/Cielo_Einstein/cache"))

T_CMB = 2.725
K_B = 1.380649e-23
C_LUCE = 299792458.0
JY = 1e-26
SFU = 1e-22                   # 1 unità di flusso solare = 10^4 Jy

# ---------------------------------------------------------------------------- fonti
ZENODO = "https://zenodo.org/record/3835582/files/{}?download=1"
DATACENTRAL = "https://apps.datacentral.org.au/pygdsm/data/{}"
MODELLI = {
    "GSM2008": {"file": "gsm_components.h5", "mb": 81.8, "fmin": 10.0, "fmax": 94000.0,
                "nome": "GSM2008 (de Oliveira-Costa et al. 2008)", "breve": "GSM2008"},
    "LFSM": {"file": "lfsm.h5", "mb": 23.6, "fmin": 10.0, "fmax": 408.0,
             "nome": "LFSM (Dowell et al. 2017, LWA1)", "breve": "LFSM"},
}
URL_CELESTIAL = "https://cdn.jsdelivr.net/npm/d3-celestial@0.7.35/data/"
SKYVIEW = "https://skyview.gsfc.nasa.gov/cgi-bin/images"
URL_NOAA_RADIO = "https://services.swpc.noaa.gov/json/solar-radio-flux.json"
URL_NOAA_F107 = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
LWATV = {  # nome mostrato, indirizzo dell'immagine
    "1": ("LWA TV 1 (LWA1, New Mexico)", "https://lwalab.phys.unm.edu/lwatv/lwatv.png"),
    "2": ("LWA TV 2", "https://lwalab.phys.unm.edu/lwatv2/lwatv.png"),
    "3": ("LWA TV 3", "https://lwalab.phys.unm.edu/lwatv3/lwatv.png"),
    "4": ("LWA TV 4", "https://lwalab.phys.unm.edu/lwatv4/lwatv.png"),
}
LWATV_PAGINA = "https://leo.phys.unm.edu/~lwa/lwatv.html"

LUOGHI = {
    "Padova": (45.4064, 11.8768, 12),
    "LWA1 (New Mexico)": (34.0689, -107.6284, 2133),
    "Onsala (SALSA)": (57.3930, 11.9177, 10),
    "Atene (PICTOR)": (37.9838, 23.7275, 100),
}


def log(t):
    print(f"[{datetime.now():%H:%M:%S}] {t}", flush=True)


# =============================================================================
# RETE: tempo massimo, ripresa dei download, riprova solo IPv4
# =============================================================================
_orig_gai = socket.getaddrinfo
_lock_ipv4 = threading.Lock()


class _SoloIPv4:
    def __enter__(self):
        _lock_ipv4.acquire()

        def gai(host, port, family=0, *a, **k):
            return _orig_gai(host, port, socket.AF_INET, *a, **k)
        socket.getaddrinfo = gai

    def __exit__(self, *a):
        socket.getaddrinfo = _orig_gai
        _lock_ipv4.release()


def scarica_bytes(url, timeout=30, totale=90, massimo=None):
    """Bytes di un indirizzo (prima normale, poi solo IPv4 se non risponde)."""
    err = None
    for ipv4 in (False, True):
        t0 = time.time()
        try:
            ctx = _SoloIPv4() if ipv4 else _Nulla()
            with ctx:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    parti, n = [], 0
                    while True:
                        b = r.read(65536)
                        if not b:
                            break
                        parti.append(b)
                        n += len(b)
                        if massimo and n > massimo:
                            raise ValueError("file troppo grande")
                        if time.time() - t0 > totale:
                            raise TimeoutError("tempo massimo superato")
                    return b"".join(parti)
        except urllib.error.HTTPError as e:
            raise e
        except Exception as e:  # noqa: BLE001
            err = e
    raise err


class _Nulla:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def scarica_file(urls, dest, avanzamento=None, ferma=None, timeout=30):
    """Scarica nel file dest (riprende un .parziale con Range). Prova gli indirizzi in ordine.
    avanzamento(byte, totale) viene chiamato spesso; ferma: threading.Event per interrompere."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parz = dest.with_suffix(dest.suffix + ".parziale")
    err = None
    for url in urls:
        for ipv4 in (False, True):
            try:
                gia = parz.stat().st_size if parz.exists() else 0
                h = {"User-Agent": UA}
                if gia:
                    h["Range"] = f"bytes={gia}-"
                with (_SoloIPv4() if ipv4 else _Nulla()):
                    req = urllib.request.Request(url, headers=h)
                    r = urllib.request.urlopen(req, timeout=timeout)
                with r:
                    if gia and r.status != 206:
                        gia = 0                      # il server non riprende: si ricomincia
                    tot = None
                    cr = r.headers.get("Content-Range", "")
                    m = re.search(r"/(\d+)$", cr)
                    if m:
                        tot = int(m.group(1))
                    elif r.headers.get("Content-Length"):
                        tot = gia + int(r.headers["Content-Length"])
                    with open(parz, "ab" if gia else "wb") as f:
                        n = gia
                        ultimo = 0
                        while True:
                            if ferma is not None and ferma.is_set():
                                raise InterruptedError("interrotto")
                            b = r.read(262144)
                            if not b:
                                break
                            f.write(b)
                            n += len(b)
                            if avanzamento and time.time() - ultimo > 0.3:
                                avanzamento(n, tot)
                                ultimo = time.time()
                if tot and parz.stat().st_size < tot:
                    raise IOError(f"scaricati {parz.stat().st_size} byte su {tot}")
                os.replace(parz, dest)
                if avanzamento:
                    avanzamento(dest.stat().st_size, dest.stat().st_size)
                return dest
            except InterruptedError:
                raise
            except urllib.error.HTTPError as e:
                err = e
                break                                # risposta vera del server: indirizzo seguente
            except Exception as e:  # noqa: BLE001
                err = e
                log(f"   {url.split('/')[2]}: {type(e).__name__}: {e} ({'IPv4' if ipv4 else 'normale'})")
    raise err or IOError("nessun indirizzo")


# =============================================================================
# HEALPIX (RING)
# =============================================================================
def ang2pix_ring(nside, theta, phi):
    """Indici RING dei pixel che contengono le direzioni (theta = colatitudine, phi in radianti)."""
    n = int(nside)
    z = np.cos(theta)
    za = np.abs(z)
    tt = np.mod(phi, 2 * np.pi) / (np.pi / 2)        # in [0, 4)
    pix = np.empty(z.shape, dtype=np.int64)
    ncap = 2 * n * (n - 1)
    npix = 12 * n * n
    eq = za <= 2.0 / 3.0
    if np.any(eq):
        t1 = n * (0.5 + tt[eq])
        t2 = n * z[eq] * 0.75
        jp = (t1 - t2).astype(np.int64)
        jm = (t1 + t2).astype(np.int64)
        ir = n + 1 + jp - jm
        kshift = 1 - (ir & 1)
        ip = (jp + jm - n + kshift + 1) // 2
        ip = np.mod(ip, 4 * n)
        pix[eq] = ncap + (ir - 1) * 4 * n + ip
    po = ~eq
    if np.any(po):
        ttp = tt[po]
        tp = ttp - np.floor(ttp)
        tmp = n * np.sqrt(3 * (1 - za[po]))
        jp = (tp * tmp).astype(np.int64)
        jm = ((1 - tp) * tmp).astype(np.int64)
        ir = jp + jm + 1
        ip = (ttp * ir).astype(np.int64)
        ip = np.mod(ip, 4 * ir)
        nord = z[po] > 0
        pix[po] = np.where(nord, 2 * ir * (ir - 1) + ip, npix - 2 * ir * (ir + 1) + ip)
    return pix


def ring_ang(nside, ipix):
    """(lon, lat) in gradi dei centri dei pixel RING (da Cielo di Einstein)."""
    ipix = np.asarray(ipix, dtype=np.int64)
    n = int(nside)
    npix, ncap = 12 * n * n, 2 * n * (n - 1)
    z = np.empty(ipix.shape)
    phi = np.empty(ipix.shape)
    a = ipix < ncap
    ir = np.floor((1 + np.sqrt(1 + 2 * ipix[a])) / 2).astype(np.int64)
    ip = ipix[a] + 1 - 2 * ir * (ir - 1)
    z[a] = 1 - ir ** 2 / (3.0 * n * n)
    phi[a] = (ip - 0.5) * np.pi / (2 * ir)
    c = ipix >= npix - ncap
    q = npix - ipix[c]
    ir = np.floor((1 + np.sqrt(2 * q - 1)) / 2).astype(np.int64)
    ip = 4 * ir + 1 - (q - 2 * ir * (ir - 1))
    z[c] = -1 + ir ** 2 / (3.0 * n * n)
    phi[c] = (ip - 0.5) * np.pi / (2 * ir)
    e = ~(a | c)
    q = ipix[e] - ncap
    ir = q // (4 * n) + n
    ip = q % (4 * n) + 1
    fodd = np.where((ir + n) & 1, 1.0, 0.5)
    z[e] = (2 * n - ir) * 2.0 / (3.0 * n)
    phi[e] = (ip - fodd) * np.pi / (2 * n)
    return np.degrees(phi) % 360, np.degrees(np.arcsin(np.clip(z, -1, 1)))


def vettori_a_pix(nside, v):
    """v: array (..., 3) di versori -> indici RING."""
    theta = np.arccos(np.clip(v[..., 2], -1, 1))
    phi = np.arctan2(v[..., 1], v[..., 0])
    return ang2pix_ring(nside, theta, phi)


# =============================================================================
# INTERPOLAZIONI (come scipy: PCHIP e spline cubica «not-a-knot»)
# =============================================================================
def _pchip_derivate(x, y):
    h = np.diff(x)
    m = np.diff(y) / h
    n = len(x)
    d = np.zeros(n)
    if n == 2:
        d[:] = m[0]
        return d
    w1 = 2 * h[1:] + h[:-1]
    w2 = h[1:] + 2 * h[:-1]
    ok = (np.sign(m[1:]) * np.sign(m[:-1]) > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        dint = (w1 + w2) / (w1 / m[:-1] + w2 / m[1:])
    d[1:-1] = np.where(ok, dint, 0.0)

    def bordo(h0, h1, m0, m1):
        dd = ((2 * h0 + h1) * m0 - h0 * m1) / (h0 + h1)
        if np.sign(dd) != np.sign(m0):
            dd = 0.0
        elif np.sign(m0) != np.sign(m1) and abs(dd) > abs(3 * m0):
            dd = 3 * m0
        return dd
    d[0] = bordo(h[0], h[1], m[0], m[1])
    d[-1] = bordo(h[-1], h[-2], m[-1], m[-2])
    return d


def pchip(x, y, xq):
    """Interpolazione PCHIP (Fritsch-Carlson, come scipy.interpolate.PchipInterpolator)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    d = _pchip_derivate(x, y)
    xq = np.atleast_1d(np.asarray(xq, float))
    k = np.clip(np.searchsorted(x, xq) - 1, 0, len(x) - 2)
    h = x[k + 1] - x[k]
    t = (xq - x[k]) / h
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    return h00 * y[k] + h10 * h * d[k] + h01 * y[k + 1] + h11 * h * d[k + 1]


def spline_cubica(x, y, xq):
    """Spline cubica con condizioni «not-a-knot» (come scipy interp1d kind='cubic')."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    xq = np.atleast_1d(np.asarray(xq, float))
    if n < 4:
        return np.interp(xq, x, y)
    h = np.diff(x)
    A = np.zeros((n, n))
    b = np.zeros(n)
    for i in range(1, n - 1):                  # derivate seconde M
        A[i, i - 1] = h[i - 1]
        A[i, i] = 2 * (h[i - 1] + h[i])
        A[i, i + 1] = h[i]
        b[i] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
    # not-a-knot: derivata terza continua nel secondo e nel penultimo nodo
    A[0, 0], A[0, 1], A[0, 2] = h[1], -(h[0] + h[1]), h[0]
    A[-1, -3], A[-1, -2], A[-1, -1] = h[-1], -(h[-2] + h[-1]), h[-2]
    M = np.linalg.solve(A, b)
    k = np.clip(np.searchsorted(x, xq) - 1, 0, n - 2)
    hk = h[k]
    a = (x[k + 1] - xq) / hk
    bb = (xq - x[k]) / hk
    return (a * y[k] + bb * y[k + 1] + ((a ** 3 - a) * M[k] + (bb ** 3 - bb) * M[k + 1]) * hk ** 2 / 6)


# =============================================================================
# MODELLI DEL CIELO RADIO
# =============================================================================
def file_modello(nome):
    return CARTELLA / "dati" / MODELLI[nome]["file"]


def npy_pronti(nome):
    d = CARTELLA / "dati"
    if nome == "GSM2008":
        return (d / "gsm08_408locked.npy").exists() and (d / "gsm08_componenti.npy").exists()
    return (d / "lfsm_mappe.npy").exists() and (d / "lfsm_componenti.npy").exists()


def converti_h5(nome):
    """Legge l'.h5 una volta (h5py) e salva le parti che servono come .npy (float32)."""
    import h5py  # noqa: PLC0415 - serve solo qui
    d = CARTELLA / "dati"
    with h5py.File(file_modello(nome), "r") as h:
        chiavi = list(h.keys())
        servono = (["component_maps_408locked", "component_maps_23klocked", "components"] if nome == "GSM2008"
                   else ["lfsm_component_maps_3.0deg.dat", "lfsm_components.dat"])
        mancano = [k for k in servono if k not in chiavi]
        if mancano:
            raise KeyError(f"nel file {file_modello(nome).name} mancano {mancano}; ci sono {chiavi}")
        if nome == "GSM2008":
            for chiave, uscita in (("component_maps_408locked", "gsm08_408locked.npy"),
                                   ("component_maps_23klocked", "gsm08_23klocked.npy")):
                a = np.asarray(h[chiave][:], dtype=np.float32)
                if a.shape[0] < a.shape[1]:
                    a = a.T
                np.save(d / (uscita + ".tmp.npy"), a)
                os.replace(d / (uscita + ".tmp.npy"), d / uscita)
            np.save(d / "gsm08_componenti.npy", np.asarray(h["components"][:], dtype=np.float64))
        else:
            a = np.asarray(h["lfsm_component_maps_3.0deg.dat"][:], dtype=np.float32)
            if a.shape[0] < a.shape[1]:
                a = a.T
            np.save(d / "lfsm_mappe.npy", a)
            np.save(d / "lfsm_componenti.npy", np.asarray(h["lfsm_components.dat"][:], dtype=np.float64))
    log(f"{nome}: convertito in .npy")


def prepara_modello(nome, avanzamento=None, ferma=None):
    """Scarica (se manca) e converte il modello. Restituisce True se pronto."""
    if npy_pronti(nome):
        return True
    f = file_modello(nome)
    if not f.exists():
        fn = MODELLI[nome]["file"]
        scarica_file([ZENODO.format(fn), DATACENTRAL.format(fn)], f, avanzamento, ferma)
    converti_h5(nome)
    return npy_pronti(nome)


class ModelloCielo:
    """Mappa HEALPix della temperatura di brillanza (K) del cielo radio diffuso a una frequenza."""

    def __init__(self, nome):
        self.nome = nome
        d = CARTELLA / "dati"
        self._cache = {}
        if nome == "GSM2008":
            self.nside = 512
            self.coord = "G"
            self.mappe = {"haslam": np.load(d / "gsm08_408locked.npy", mmap_mode="r")}
            self._f23 = d / "gsm08_23klocked.npy"
            t = np.load(d / "gsm08_componenti.npy")
            self.lnf = np.log(t[:, 0])
            self.lnscala = np.log(t[:, 1])
            self.comp = t[:, 2:5].T
        else:
            self.nside = 256
            self.coord = "C"                       # equatoriali J2000
            self.mappe = {"lfsm": np.load(d / "lfsm_mappe.npy", mmap_mode="r")}
            t = np.load(d / "lfsm_componenti.npy")
            self.lnf = np.log(t[:, 0])
            self.lnscala = np.log(t[:, 1])
            self.comp = t[:, 2:].T
        self.fmin = MODELLI[nome]["fmin"]
        self.fmax = MODELLI[nome]["fmax"]

    def mappa_base(self, f_mhz):
        if self.nome != "GSM2008":
            return "lfsm"
        # come raccomanda il lavoro originale: sotto ~10 GHz la base Haslam (1°),
        # sopra la base WMAP 23 GHz (2°)
        if f_mhz >= 10000 and self._f23.exists():
            if "wmap" not in self.mappe:
                self.mappe["wmap"] = np.load(self._f23, mmap_mode="r")
            return "wmap"
        return "haslam"

    def mappa(self, f_mhz):
        f_mhz = float(min(max(f_mhz, self.fmin), self.fmax))
        chiave = round(f_mhz, 4)
        if chiave in self._cache:
            return self._cache[chiave]
        lnf = math.log(f_mhz)
        if self.nome == "GSM2008":
            scala = math.exp(float(pchip(self.lnf, self.lnscala, lnf)[0]))
            w = np.array([float(pchip(self.lnf, c, lnf)[0]) for c in self.comp])
            base = self.mappe[self.mappa_base(f_mhz)]
            m = (np.asarray(base, dtype=np.float32) @ w.astype(np.float32)) * np.float32(scala)
        else:
            scala = math.exp(float(np.interp(lnf, self.lnf, self.lnscala)))
            w = np.array([float(spline_cubica(self.lnf, c, lnf)[0]) for c in self.comp])
            m = (np.asarray(self.mappe["lfsm"], dtype=np.float32) @ w.astype(np.float32)) * np.float32(scala)
            m = m - np.float32(T_CMB)              # come pygdsm senza CMB
        if len(self._cache) > 6:
            self._cache.pop(next(iter(self._cache)))
        self._cache[chiave] = m
        return m

    def scala_colori(self, f_mhz):
        """Limiti (vmin, vmax) in K per i colori: percentili di tutto il cielo (non cambiano col tempo)."""
        m = self.mappa(f_mhz)
        campione = m[:: max(1, len(m) // 400000)]
        campione = campione[np.isfinite(campione) & (campione > 0)]
        if len(campione) < 10:
            return 1e-3, 1.0
        lo, hi = np.percentile(campione, [1.0, 99.85])
        return float(max(lo, 1e-6)), float(max(hi, lo * 1.01))


# =============================================================================
# TEMPO E COORDINATE
# =============================================================================
def jd(dt):
    return dt.timestamp() / 86400.0 + 2440587.5


def gmst_gradi(dt):
    j = jd(dt)
    T = (j - 2451545.0) / 36525.0
    return (280.46061837 + 360.98564736629 * (j - 2451545.0) + 0.000387933 * T * T - T ** 3 / 38710000.0) % 360


def lst_gradi(dt, lon):
    return (gmst_gradi(dt) + lon) % 360


def _r3(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, s, 0], [-s, c, 0], [0, 0, 1.0]])


def _r2(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])


def _r1(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0, 0], [0, c, s], [0, -s, c]])


def precessione(dt):
    """Matrice J2000 -> equinozio della data (IAU 1976)."""
    T = (jd(dt) - 2451545.0) / 36525.0
    a = math.radians(1 / 3600)
    zeta = (2306.2181 * T + 0.30188 * T ** 2 + 0.017998 * T ** 3) * a
    z = (2306.2181 * T + 1.09468 * T ** 2 + 0.018203 * T ** 3) * a
    th = (2004.3109 * T - 0.42665 * T ** 2 - 0.041833 * T ** 3) * a
    return _r3(-z) @ _r2(th) @ _r3(-zeta)


GAL = np.array([[-0.0548755604, -0.8734370902, -0.4838350155],      # J2000 -> galattiche
                [+0.4941094279, -0.4448296300, +0.7469822445],
                [-0.8676661490, -0.1980763734, +0.4559837762]])


def matrice_enu_equ(lst, lat):
    """Matrice che porta (Est, Nord, Su) nell'equatoriale della data."""
    L, p = math.radians(lst), math.radians(lat)
    cL, sL, cp, sp = math.cos(L), math.sin(L), math.cos(p), math.sin(p)
    return np.array([[-sL, -cL * sp, cL * cp],
                     [cL, -sL * sp, sL * cp],
                     [0.0, cp, sp]])


def radec_vett(ra, dec):
    ra, dec = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def vett_radec(v):
    return (np.degrees(np.arctan2(v[..., 1], v[..., 0])) % 360,
            np.degrees(np.arcsin(np.clip(v[..., 2], -1, 1))))


def enu_altaz(v):
    alt = np.degrees(np.arcsin(np.clip(v[..., 2], -1, 1)))
    az = np.degrees(np.arctan2(v[..., 0], v[..., 1])) % 360
    return alt, az


def altaz_enu(alt, az):
    alt, az = np.radians(alt), np.radians(az)
    return np.stack([np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)], axis=-1)


class Cornice:
    """Tutte le trasformazioni per un luogo e un istante."""

    def __init__(self, dt, lat, lon):
        self.dt = dt
        self.lat, self.lon = lat, lon
        self.lst = lst_gradi(dt, lon)
        self.P = precessione(dt)
        self.M = matrice_enu_equ(self.lst, lat)
        self.enu_j2000 = self.P.T @ self.M            # ENU -> J2000
        self.j2000_enu = self.enu_j2000.T
        self.enu_gal = GAL @ self.enu_j2000

    def altaz_da_j2000(self, ra, dec):
        v = radec_vett(np.asarray(ra, float), np.asarray(dec, float))
        return enu_altaz(v @ self.j2000_enu.T)

    def altaz_da_data(self, ra, dec):
        """Coordinate dell'equinozio della data (Sole, Luna, pianeti)."""
        v = radec_vett(np.asarray(ra, float), np.asarray(dec, float))
        return enu_altaz(v @ self.M)

    def altaz_da_galattiche(self, l, b):
        v = radec_vett(np.asarray(l, float), np.asarray(b, float))
        return enu_altaz(v @ GAL @ self.j2000_enu.T)


# =============================================================================
# SOLE, LUNA, PIANETI (Paul Schlyter, «How to compute planetary positions»)
# =============================================================================
def _rev(x):
    return x % 360.0


def _sin(x):
    return math.sin(math.radians(x))


def _cos(x):
    return math.cos(math.radians(x))


def _kepler(M, e):
    E = M + math.degrees(e * _sin(M) * (1 + e * _cos(M)))
    for _ in range(20):
        E1 = E - (E - math.degrees(e * _sin(E)) - M) / (1 - e * _cos(E))
        if abs(E1 - E) < 1e-7:
            return E1
        E = E1
    return E


ELEMENTI = {  # N, i, w, a, e, M  (costante, variazione al giorno)
    "Mercurio": ((48.3313, 3.24587e-5), (7.0047, 5.00e-8), (29.1241, 1.01444e-5), (0.387098, 0), (0.205635, 5.59e-10), (168.6562, 4.0923344368)),
    "Venere": ((76.6799, 2.46590e-5), (3.3946, 2.75e-8), (54.8910, 1.38374e-5), (0.723330, 0), (0.006773, -1.302e-9), (48.0052, 1.6021302244)),
    "Marte": ((49.5574, 2.11081e-5), (1.8497, -1.78e-8), (286.5016, 2.92961e-5), (1.523688, 0), (0.093405, 2.516e-9), (18.6021, 0.5240207766)),
    "Giove": ((100.4542, 2.76854e-5), (1.3030, -1.557e-7), (273.8777, 1.64505e-5), (5.20256, 0), (0.048498, 4.469e-9), (19.8950, 0.0830853001)),
    "Saturno": ((113.6634, 2.38980e-5), (2.4886, -1.081e-7), (339.3939, 2.97661e-5), (9.55475, 0), (0.055546, -9.499e-9), (316.9670, 0.0334442282)),
    "Urano": ((74.0005, 1.3978e-5), (0.7733, 1.9e-8), (96.6612, 3.0565e-5), (19.18171, -1.55e-8), (0.047318, 7.45e-9), (142.5905, 0.011725806)),
    "Nettuno": ((131.7806, 3.0173e-5), (1.7700, -2.55e-7), (272.8461, -6.027e-6), (30.05826, 3.313e-8), (0.008606, 2.15e-9), (260.2471, 0.005995147)),
}
DIAMETRI_KM = {"Sole": 1392700, "Luna": 3474.8, "Mercurio": 4879, "Venere": 12104, "Marte": 6779,
               "Giove": 139820, "Saturno": 116460, "Urano": 50724, "Nettuno": 49244}
T_RADIO = {"Luna": 220, "Mercurio": 400, "Venere": 600, "Marte": 210, "Giove": 160, "Saturno": 140,
           "Urano": 100, "Nettuno": 100}     # temperature di brillanza indicative (onde centimetriche)
NOMI_INGLESI = {"Sole": "Sun", "Luna": "Moon", "Mercurio": "Mercury", "Venere": "Venus", "Marte": "Mars",
                "Giove": "Jupiter", "Saturno": "Saturn", "Urano": "Uranus", "Nettuno": "Neptune"}


def _el(nome, d):
    return [c + v * d for c, v in ELEMENTI[nome]]


def _orbita(N, i, w, a, e, M):
    E = _kepler(_rev(M), e)
    xv = a * (_cos(E) - e)
    yv = a * (math.sqrt(1 - e * e) * _sin(E))
    v = math.degrees(math.atan2(yv, xv))
    r = math.hypot(xv, yv)
    xh = r * (_cos(N) * _cos(v + w) - _sin(N) * _sin(v + w) * _cos(i))
    yh = r * (_sin(N) * _cos(v + w) + _cos(N) * _sin(v + w) * _cos(i))
    zh = r * (_sin(v + w) * _sin(i))
    return xh, yh, zh, r


def posizioni_corpi(dt, lat=None, lon=None):
    """Sole, Luna, pianeti: dict nome -> (ra, dec della data, distanza in km, diametro in gradi)."""
    d = jd(dt) - 2451543.5
    ecl = 23.4393 - 3.563e-7 * d
    # Sole
    ws = 282.9404 + 4.70935e-5 * d
    es = 0.016709 - 1.151e-9 * d
    Ms = _rev(356.0470 + 0.9856002585 * d)
    E = _kepler(Ms, es)
    xv, yv = _cos(E) - es, math.sqrt(1 - es * es) * _sin(E)
    vs = math.degrees(math.atan2(yv, xv))
    rs = math.hypot(xv, yv)
    lons = _rev(vs + ws)
    xs, ys = rs * _cos(lons), rs * _sin(lons)
    AU = 149597870.7

    def a_equ(xg, yg, zg):
        xe = xg
        ye = yg * _cos(ecl) - zg * _sin(ecl)
        ze = yg * _sin(ecl) + zg * _cos(ecl)
        ra = _rev(math.degrees(math.atan2(ye, xe)))
        dec = math.degrees(math.atan2(ze, math.hypot(xe, ye)))
        return ra, dec, math.sqrt(xe * xe + ye * ye + ze * ze)
    out = {}
    ra, dec, r = a_equ(xs, ys, 0.0)
    out["Sole"] = (ra, dec, r * AU)
    # Luna
    Nm = 125.1228 - 0.0529538083 * d
    im, wm, am, em = 5.1454, 318.0634 + 0.1643573223 * d, 60.2666, 0.054900
    Mm = _rev(115.3654 + 13.0649929509 * d)
    xh, yh, zh, r = _orbita(Nm, im, wm, am, em, Mm)
    lonm = math.degrees(math.atan2(yh, xh))
    latm = math.degrees(math.atan2(zh, math.hypot(xh, yh)))
    Ls = _rev(Ms + ws)
    Lm = _rev(Mm + wm + Nm)
    D = _rev(Lm - Ls)
    F = _rev(Lm - Nm)
    lonm += (-1.274 * _sin(Mm - 2 * D) + 0.658 * _sin(2 * D) - 0.186 * _sin(Ms) - 0.059 * _sin(2 * Mm - 2 * D)
             - 0.057 * _sin(Mm - 2 * D + Ms) + 0.053 * _sin(Mm + 2 * D) + 0.046 * _sin(2 * D - Ms)
             + 0.041 * _sin(Mm - Ms) - 0.035 * _sin(D) - 0.031 * _sin(Mm + Ms) - 0.015 * _sin(2 * F - 2 * D)
             + 0.011 * _sin(Mm - 4 * D))
    latm += (-0.173 * _sin(F - 2 * D) - 0.055 * _sin(Mm - F - 2 * D) - 0.046 * _sin(Mm + F - 2 * D)
             + 0.033 * _sin(F + 2 * D) + 0.017 * _sin(2 * Mm + F))
    r += -0.58 * _cos(Mm - 2 * D) - 0.46 * _cos(2 * D)
    xg = r * _cos(lonm) * _cos(latm)
    yg = r * _sin(lonm) * _cos(latm)
    zg = r * _sin(latm)
    ra, dec, r = a_equ(xg, yg, zg)
    if lat is not None:                        # parallasse topocentrica
        mpar = math.degrees(math.asin(1 / r))
        gclat = lat - 0.1924 * _sin(2 * lat)
        rho = 0.99833 + 0.00167 * _cos(2 * lat)
        HA = _rev(lst_gradi(dt, lon) - ra)
        g = math.degrees(math.atan(math.tan(math.radians(gclat)) / _cos(HA))) if abs(_cos(HA)) > 1e-9 else 90.0
        ra = ra - mpar * rho * _cos(gclat) * _sin(HA) / _cos(dec)
        if abs(_sin(g)) > 1e-9:
            dec = dec - mpar * rho * _sin(gclat) * _sin(g - dec) / _sin(g)
        else:
            dec = dec - mpar * rho * _sin(-dec) * _cos(HA)
    out["Luna"] = (_rev(ra), dec, r * 6378.14)
    # pianeti
    Mj = _rev(19.8950 + 0.0830853001 * d)
    Msa = _rev(316.9670 + 0.0334442282 * d)
    Mu = _rev(142.5905 + 0.011725806 * d)
    for nome in ELEMENTI:
        N, i, w, a, e, M = _el(nome, d)
        xh, yh, zh, r = _orbita(N, i, w, a, e, M)
        lon_h = math.degrees(math.atan2(yh, xh))
        lat_h = math.degrees(math.atan2(zh, math.hypot(xh, yh)))
        if nome == "Giove":
            lon_h += (-0.332 * _sin(2 * Mj - 5 * Msa - 67.6) - 0.056 * _sin(2 * Mj - 2 * Msa + 21)
                      + 0.042 * _sin(3 * Mj - 5 * Msa + 21) - 0.036 * _sin(Mj - 2 * Msa) + 0.022 * _cos(Mj - Msa)
                      + 0.023 * _sin(2 * Mj - 3 * Msa + 52) - 0.016 * _sin(Mj - 5 * Msa - 69))
        elif nome == "Saturno":
            lon_h += (0.812 * _sin(2 * Mj - 5 * Msa - 67.6) - 0.229 * _cos(2 * Mj - 4 * Msa - 2)
                      + 0.119 * _sin(Mj - 2 * Msa - 3) + 0.046 * _sin(2 * Mj - 6 * Msa - 69)
                      + 0.014 * _sin(Mj - 3 * Msa + 32))
            lat_h += -0.020 * _cos(2 * Mj - 4 * Msa - 2) + 0.018 * _sin(2 * Mj - 6 * Msa - 49)
        elif nome == "Urano":
            lon_h += 0.040 * _sin(Msa - 2 * Mu + 6) + 0.035 * _sin(Msa - 3 * Mu + 33) - 0.015 * _sin(Mj - Mu + 20)
        xh = r * _cos(lon_h) * _cos(lat_h)
        yh = r * _sin(lon_h) * _cos(lat_h)
        zh = r * _sin(lat_h)
        ra, dec, rg = a_equ(xh + xs, yh + ys, zh)
        out[nome] = (ra, dec, rg * AU)
    return {k: (ra, dec, dist, math.degrees(2 * math.atan(DIAMETRI_KM[k] / 2 / dist)))
            for k, (ra, dec, dist) in out.items()}


def flusso_corpo_nero(T, diam_gradi, f_mhz):
    """Flusso (Jy) di un disco a temperatura T (Rayleigh-Jeans)."""
    om = math.pi * (math.radians(diam_gradi) / 2) ** 2
    lam = C_LUCE / (f_mhz * 1e6)
    return 2 * K_B * T * om / lam ** 2 / JY


# =============================================================================
# SORGENTI RADIO FAMOSE (J2000; flussi indicativi: S a 1 GHz e indice spettrale)
# =============================================================================
SORGENTI = [
    # nome, nome inglese, ra, dec, S1GHz (Jy) o None, alfa, tipo, testo
    ("Cassiopea A", "Cas A", 350.850, 58.815, 2000, -0.77, "resto di supernova",
     "La sorgente radio più brillante fuori dal Sistema solare: il resto di una supernova esplosa verso il 1680 "
     "a 11 000 anni luce. Il guscio di gas in espansione emette per sincrotrone e si affievolisce di circa lo 0,6 % all'anno."),
    ("Cigno A", "Cyg A", 299.868, 40.734, 2000, -0.9, "radiogalassia",
     "Una radiogalassia a 760 milioni di anni luce: due getti dal buco nero centrale gonfiano due enormi lobi radio. "
     "È lontanissima eppure è la seconda sorgente del cielo radio."),
    ("Toro A (Nebulosa del Granchio)", "Tau A", 83.633, 22.014, 1000, -0.3, "resto di supernova",
     "Il resto della supernova del 1054, con una pulsar al centro che ruota 30 volte al secondo e alimenta la nebulosa."),
    ("Vergine A (M 87)", "Vir A", 187.706, 12.391, 250, -0.8, "radiogalassia",
     "La galassia ellittica gigante M 87, con il buco nero fotografato dall'Event Horizon Telescope e un getto lungo 5000 anni luce."),
    ("Sagittario A", "Sgr A", 266.417, -29.008, None, None, "centro galattico",
     "Il centro della Via Lattea: la regione radio Sgr A circonda Sgr A*, il buco nero di 4 milioni di masse solari."),
    ("Centauro A", "Cen A", 201.365, -43.019, 1300, -0.7, "radiogalassia",
     "La radiogalassia più vicina (12 milioni di anni luce), con lobi radio più grandi della Luna piena. Da Padova resta bassissima."),
    ("Ercole A", "Her A", 252.784, 4.993, 60, -1.0, "radiogalassia", "Radiogalassia con getti lunghissimi a 2 miliardi di anni luce."),
    ("Idra A", "Hya A", 139.524, -12.096, 55, -0.9, "radiogalassia", "Radiogalassia al centro di un ammasso di galassie."),
    ("Fornace A", "For A", 50.674, -37.208, 150, -0.8, "radiogalassia", "Galassia nata dalla fusione di due galassie, con due grandi lobi radio."),
    ("Pittore A", "Pic A", 79.957, -45.779, 80, -0.8, "radiogalassia", "Radiogalassia con un getto visibile anche nei raggi X."),
    ("3C 273", "3C 273", 187.278, 2.052, 45, -0.2, "quasar", "Il primo quasar riconosciuto (1963), a 2,4 miliardi di anni luce."),
    ("Nebulosa di Orione (M 42)", "Ori A", 83.822, -5.391, 400, -0.1, "regione HII",
     "Gas ionizzato dalle stelle giovani: emissione termica, quasi piatta in frequenza, assorbe sotto ~1 GHz."),
    ("Regione del Cigno X", "Cyg X", 307.0, 41.0, None, None, "regione di formazione stellare",
     "Il più grande complesso di formazione stellare vicino a noi: in radio è una macchia larga molti gradi."),
    ("Galassia di Andromeda (M 31)", "M 31", 10.685, 41.269, 10, -0.8, "galassia", "La grande galassia vicina: debole in radio, ma visibile nei radiotelescopi."),
    ("Nube di Magellano (LMC)", "LMC", 80.89, -69.76, None, None, "galassia", "Galassia satellite della Via Lattea: in radio splende la nebulosa Tarantola. Invisibile dall'Italia."),
    ("Resto di Vela", "Vela", 128.5, -45.8, 1500, -0.5, "resto di supernova", "Un grande resto di supernova di 11 000 anni fa con la pulsar Vela."),
    ("IC 443 (Medusa)", "IC 443", 94.25, 22.53, 160, -0.36, "resto di supernova", "Resto di supernova nei Gemelli, che urta una nube molecolare."),
    ("W50 e SS 433", "W50", 287.96, 4.98, 80, -0.5, "nebulosa e microquasar", "La nebulosa «Lamantino», deformata dai getti del microquasar SS 433."),
]


# =============================================================================
# SOLE RADIO (NOAA SWPC, rete RSTN)
# =============================================================================
class SoleRadio:
    def __init__(self):
        self.punti = None          # [(f_mhz, sfu)]
        self.quando = None
        self.f107 = None
        self.errore = None

    def aggiorna(self):
        p = CARTELLA / "cache" / "solar-radio-flux.json"
        try:
            b = scarica_bytes(URL_NOAA_RADIO, timeout=20, totale=40)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
        except Exception as e:  # noqa: BLE001
            self.errore = f"{type(e).__name__}"
            if not p.exists():
                return False
            b = p.read_bytes()
        try:
            js = json.loads(b)
            per_f = {}
            ultimo = max(r.get("time_tag", "") for r in js)
            giorno = ultimo[:10]
            for r in js:
                if r.get("time_tag", "")[:10] < giorno and len(per_f) > 4:
                    continue
                for d in r.get("details", []):
                    if d.get("flux") and str(d.get("observed_quality", "")).lower().startswith("good"):
                        per_f.setdefault(int(d["frequency"]), []).append(float(d["flux"]))
            self.punti = sorted((f, float(np.median(v))) for f, v in per_f.items() if v)
            self.quando = ultimo
            self.errore = None
            return bool(self.punti)
        except Exception as e:  # noqa: BLE001
            self.errore = f"{type(e).__name__}"
            return False

    def flusso_sfu(self, f_mhz):
        """Flusso del Sole (sfu) alla frequenza: interpolato in log-log tra le stazioni RSTN,
        prolungato fuori dall'intervallo misurato (indicativo)."""
        if not self.punti or len(self.punti) < 2:
            # Sole calmo tipico (indicativo)
            pts = [(245, 15), (410, 35), (610, 55), (1415, 75), (2695, 100), (4995, 160), (8800, 260), (15400, 520)]
        else:
            pts = self.punti
        lf = np.log([p[0] for p in pts])
        ls = np.log([p[1] for p in pts])
        x = math.log(f_mhz)
        if x < lf[0]:
            k = (ls[1] - ls[0]) / (lf[1] - lf[0])
            k = max(k, 0.5)
            return float(math.exp(ls[0] + k * (x - lf[0])))
        if x > lf[-1]:
            return float(math.exp(ls[-1] + 2.0 * (x - lf[-1])))   # ~ corpo nero sopra 15 GHz
        return float(math.exp(np.interp(x, lf, ls)))


# =============================================================================
# STELLE E COSTELLAZIONI (d3-celestial; copia da Cielo di Einstein se c'è)
# =============================================================================
def file_celestial(nome):
    p = CARTELLA / "cache" / nome
    if p.exists():
        return p
    q = CARTELLA_EINSTEIN / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    if q.exists():
        shutil.copyfile(q, p)
        return p
    b = scarica_bytes(URL_CELESTIAL + nome, timeout=30, totale=90)
    p.write_bytes(b)
    return p


class CieloOttico:
    def __init__(self):
        self.stelle = np.zeros((0, 4))       # ra, dec, mag, bv
        self.nomi_stelle = []
        self.linee = []                      # liste di [ra, dec]
        self.costellazioni = []              # (nome, ra, dec, rank)
        self.errore = None

    def carica(self):
        try:
            st = json.loads(file_celestial("stars.6.json").read_text(encoding="utf-8"))
            righe, nomi = [], {}
            try:
                nm = json.loads(file_celestial("starnames.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                nm = {}
            for f in st["features"]:
                m = f["properties"].get("mag")
                if m is None or m > 5.5:
                    continue
                lon, lat = f["geometry"]["coordinates"]
                bv = f["properties"].get("bv")
                try:
                    bv = float(bv)
                except (TypeError, ValueError):
                    bv = 0.6
                righe.append((lon % 360, lat, m, bv))
                n = nm.get(str(f.get("id")), {})
                if isinstance(n, dict) and m < 1.6 and (n.get("it") or n.get("name")):
                    nomi[len(righe) - 1] = n.get("it") or n.get("name")
            self.stelle = np.array(righe, float)
            self.nomi_stelle = nomi
            cj = json.loads(file_celestial("constellations.json").read_text(encoding="utf-8"))
            lj = json.loads(file_celestial("constellations.lines.json").read_text(encoding="utf-8"))
            for f in lj["features"]:
                for l in f["geometry"]["coordinates"]:
                    self.linee.append(np.array([[a % 360, b] for a, b in l], float))
            for f in cj["features"]:
                p = f["properties"]
                lon, lat = f["geometry"]["coordinates"]
                self.costellazioni.append((p.get("it") or p.get("name") or f["id"], lon % 360, lat,
                                           int(p.get("rank") or 3)))
            return True
        except Exception as e:  # noqa: BLE001
            self.errore = f"{type(e).__name__}: {e}"
            log(f"Stelle/costellazioni non disponibili: {self.errore}")
            return False


# =============================================================================
# FITS (immagini SkyView), SENZA ASTROPY
# =============================================================================
def leggi_fits_immagine(b):
    pos, h = 0, {}
    while True:
        blocco = b[pos:pos + 2880]
        if len(blocco) < 2880:
            raise ValueError("FITS troncato")
        pos += 2880
        fine = False
        for i in range(0, 2880, 80):
            c = blocco[i:i + 80].decode("ascii", "replace")
            k = c[:8].strip()
            if k == "END":
                fine = True
                break
            if c[8:10] == "= ":
                v = c[10:]
                if v.lstrip().startswith("'"):
                    v = v.strip()[1:].split("'")[0].strip()
                else:
                    v = v.split("/")[0].strip()
                    try:
                        v = float(v) if any(ch in v for ch in ".eE") else int(v)
                    except ValueError:
                        pass
                h[k] = v
        if fine:
            break
    nx, ny = int(h.get("NAXIS1", 0)), int(h.get("NAXIS2", 0))
    if h.get("NAXIS", 0) < 2 or nx < 2 or ny < 2:
        raise ValueError("FITS senza immagine (SkyView non ha dati qui)")
    tipo = {-32: ">f4", -64: ">f8", 16: ">i2", 32: ">i4", 8: "u1"}[int(h["BITPIX"])]
    a = np.frombuffer(b, dtype=tipo, count=nx * ny, offset=pos).reshape(ny, nx).astype(np.float64)
    a = a * float(h.get("BSCALE", 1)) + float(h.get("BZERO", 0))
    if "BLANK" in h and int(h["BITPIX"]) > 0:
        a[a == float(h["BLANK"])] = np.nan
    return h, a[::-1]                     # riga 0 = in alto


def skyview_url(survey, parametri):
    q = dict(parametri)
    q["Survey"] = survey
    q["Return"] = "FITS"
    return SKYVIEW + "?" + urllib.parse.urlencode(q)


def skyview_fits(survey, parametri, timeout=60, totale=300):
    for tentativo in range(4):
        b = scarica_bytes(skyview_url(survey, parametri), timeout=timeout, totale=totale)
        if b[:6] == b"SIMPLE":
            return leggi_fits_immagine(b)
        if re.search(rb"overload|Too Many", b[:3000], re.I):
            time.sleep(15 * (tentativo + 1))
            continue
        raise ValueError("SkyView non ha restituito un FITS")
    raise IOError("SkyView sovraccarico")


class CieloVisibile:
    """Mappa di Mellinger (Car galattica) per la vista a occhio nudo."""
    BANDE = (("Mellinger Red", "mellinger_r.npy"), ("Mellinger Green", "mellinger_g.npy"),
             ("Mellinger Blue", "mellinger_b.npy"))

    def __init__(self):
        self.rgb = None

    def pronto(self):
        return all((CARTELLA / "cache" / f).exists() for _, f in self.BANDE)

    def scarica(self, ferma=None):
        for s, f in self.BANDE:
            p = CARTELLA / "cache" / f
            if p.exists():
                continue
            if ferma is not None and ferma.is_set():
                return False
            _, a = skyview_fits(s, {"Position": "0,0", "Coordinates": "Galactic", "Projection": "Car",
                                    "Size": "360,180", "Pixels": "1440,720"}, timeout=90, totale=400)
            p.parent.mkdir(parents=True, exist_ok=True)
            np.save(p, a.astype(np.float32))
            time.sleep(2)
        return True

    def carica(self):
        if not self.pronto():
            return False
        canali = [np.load(CARTELLA / "cache" / f) for _, f in self.BANDE]
        rgb = np.stack(canali, axis=-1)
        rgb = np.nan_to_num(rgb, nan=0.0)
        lo = np.percentile(rgb, 5)
        hi = np.percentile(rgb, 99.9)
        rgb = np.clip((rgb - lo) / (hi - lo + 1e-9), 0, 1) ** 1.25
        self.rgb = (rgb * 255).astype(np.uint8)
        return True

    def colori(self, l, b):
        """l, b in gradi (array) -> RGB uint8. Mappa Car: colonna 0 = l 180 (a sinistra l cresce)."""
        h, w = self.rgb.shape[:2]
        x = ((180.0 - ((l + 180.0) % 360.0 - 180.0)) / 360.0 * w).astype(np.int64) % w
        y = np.clip(((90.0 - b) / 180.0 * h).astype(np.int64), 0, h - 1)
        return self.rgb[y, x]


# =============================================================================
# COLORI
# =============================================================================
_LUT_TESTO = {
    "inferno": "00000401000501010601010802010a02020c02020e03021004031204031405041706041907051b08051d09061f0a07220b07240c08260d08290e092b10092d110a30120a32140b34150b37160b39180c3c190c3e1b0c411c0c431e0c451f0c48210c4a230c4c240c4f260c51280b53290b552b0b572d0b592f0a5b310a5c320a5e340a5f3609613809623909633b09643d09653e0966400a67420a68440a68450a69470b6a490b6a4a0c6b4c0c6b4d0d6c4f0d6c510e6c520e6d540f6d550f6d57106e59106e5a116e5c126e5d126e5f136e61136e62146e64156e65156e67166e69166e6a176e6c186e6d186e6f196e71196e721a6e741a6e751b6e771c6d781c6d7a1d6d7c1d6d7d1e6d7f1e6c801f6c82206c84206b85216b87216b88226a8a226a8c23698d23698f24699025689225689326679526679727669827669a28659b29649d29649f2a63a02a63a22b62a32c61a52c60a62d60a82e5fa92e5eab2f5ead305dae305cb0315bb1325ab3325ab43359b63458b73557b93556ba3655bc3754bd3853bf3952c03a51c13a50c33b4fc43c4ec63d4dc73e4cc83f4bca404acb4149cc4248ce4347cf4446d04545d24644d34743d44842d54a41d74b3fd84c3ed94d3dda4e3cdb503bdd513ade5238df5337e05536e15635e25734e35933e45a31e55c30e65d2fe75e2ee8602de9612bea632aeb6429eb6628ec6726ed6925ee6a24ef6c23ef6e21f06f20f1711ff1731df2741cf3761bf37819f47918f57b17f57d15f67e14f68013f78212f78410f8850ff8870ef8890cf98b0bf98c0af98e09fa9008fa9207fa9407fb9606fb9706fb9906fb9b06fb9d07fc9f07fca108fca309fca50afca60cfca80dfcaa0ffcac11fcae12fcb014fcb216fcb418fbb61afbb81dfbba1ffbbc21fbbe23fac026fac228fac42afac62df9c72ff9c932f9cb35f8cd37f8cf3af7d13df7d340f6d543f6d746f5d949f5db4cf4dd4ff4df53f4e156f3e35af3e55df2e661f2e865f2ea69f1ec6df1ed71f1ef75f1f179f2f27df2f482f3f586f3f68af4f88ef5f992f6fa96f8fb9af9fc9dfafda1fcffa4",
    "jet (come LWA TV)": "00008000008400008900008d00009200009600009b00009f0000a40000a80000ad0000b20000b60000bb0000bf0000c40000c80000cd0000d10000d60000da0000df0000e30000e80000ed0000f10000f60000fa0000ff0000ff0000ff0000ff0000ff0004ff0008ff000cff0010ff0014ff0018ff001cff0020ff0024ff0028ff002cff0030ff0034ff0038ff003cff0040ff0044ff0048ff004cff0050ff0054ff0058ff005cff0060ff0064ff0068ff006cff0070ff0074ff0078ff007cff0080ff0084ff0088ff008cff0090ff0094ff0098ff009cff00a0ff00a4ff00a8ff00acff00b0ff00b4ff00b8ff00bcff00c0ff00c4ff00c8ff00ccff00d0ff00d4ff00d8ff00dcfe00e0fb00e4f802e8f406ecf109f0ee0cf4eb0ff8e713fce416ffe119ffde1cffdb1fffd723ffd426ffd129ffce2cffca30ffc733ffc436ffc139ffbe3cffba40ffb743ffb446ffb149ffad4dffaa50ffa753ffa456ffa05aff9d5dff9a60ff9763ff9466ff906aff8d6dff8a70ff8773ff8377ff807aff7d7dff7a80ff7783ff7387ff708aff6d8dff6a90ff6694ff6397ff609aff5d9dff5aa0ff56a4ff53a7ff50aaff4dadff49b1ff46b4ff43b7ff40baff3cbeff39c1ff36c4ff33c7ff30caff2cceff29d1ff26d4ff23d7ff1fdbff1cdeff19e1ff16e4ff13e7ff0febff0ceeff09f1fc06f4f802f8f500fbf100feed00ffea00ffe600ffe200ffde00ffdb00ffd700ffd300ffd000ffcc00ffc800ffc400ffc100ffbd00ffb900ffb600ffb200ffae00ffab00ffa700ffa300ff9f00ff9c00ff9800ff9400ff9100ff8d00ff8900ff8600ff8200ff7e00ff7a00ff7700ff7300ff6f00ff6c00ff6800ff6400ff6000ff5d00ff5900ff5500ff5200ff4e00ff4a00ff4700ff4300ff3f00ff3b00ff3800ff3400ff3000ff2d00ff2900ff2500ff2200ff1e00ff1a00ff1600ff1300fa0f00f60b00f10800ed0400e80000e40000df0000da0000d60000d10000cd0000c80000c40000bf0000bb0000b60000b20000ad0000a80000a400009f00009b00009600009200008d0000890000840000800000",
    "viridis": "44015444025645045745055946075a46085c460a5d460b5e470d60470e6147106347116447136548146748166848176948186a481a6c481b6d481c6e481d6f481f70482071482173482374482475482576482677482878482979472a7a472c7a472d7b472e7c472f7d46307e46327e46337f463480453581453781453882443983443a83443b84433d84433e85423f854240864241864142874144874045884046883f47883f48893e49893e4a893e4c8a3d4d8a3d4e8a3c4f8a3c508b3b518b3b528b3a538b3a548c39558c39568c38588c38598c375a8c375b8d365c8d365d8d355e8d355f8d34608d34618d33628d33638d32648e32658e31668e31678e31688e30698e306a8e2f6b8e2f6c8e2e6d8e2e6e8e2e6f8e2d708e2d718e2c718e2c728e2c738e2b748e2b758e2a768e2a778e2a788e29798e297a8e297b8e287c8e287d8e277e8e277f8e27808e26818e26828e26828e25838e25848e25858e24868e24878e23888e23898e238a8d228b8d228c8d228d8d218e8d218f8d21908d21918c20928c20928c20938c1f948c1f958b1f968b1f978b1f988b1f998a1f9a8a1e9b8a1e9c891e9d891f9e891f9f881fa0881fa1881fa1871fa28720a38620a48621a58521a68522a78522a88423a98324aa8325ab8225ac8226ad8127ad8128ae8029af7f2ab07f2cb17e2db27d2eb37c2fb47c31b57b32b67a34b67935b77937b87838b9773aba763bbb753dbc743fbc7340bd7242be7144bf7046c06f48c16e4ac16d4cc26c4ec36b50c46a52c56954c56856c66758c7655ac8645cc8635ec96260ca6063cb5f65cb5e67cc5c69cd5b6ccd5a6ece5870cf5773d05675d05477d1537ad1517cd2507fd34e81d34d84d44b86d54989d5488bd6468ed64590d74393d74195d84098d83e9bd93c9dd93ba0da39a2da37a5db36a8db34aadc32addc30b0dd2fb2dd2db5de2bb8de29bade28bddf26c0df25c2df23c5e021c8e020cae11fcde11dd0e11cd2e21bd5e21ad8e219dae319dde318dfe318e2e418e5e419e7e419eae51aece51befe51cf1e51df4e61ef6e620f8e621fbe723fde725",
    "magma": "00000401000501010601010802010902020b02020d03030f03031204041405041606051806051a07061c08071e0907200a08220b09240c09260d0a290e0b2b100b2d110c2f120d31130d34140e36150e38160f3b180f3d19103f1a10421c10441d11471e114920114b21114e22115024125325125527125829115a2a115c2c115f2d11612f116331116533106734106936106b38106c390f6e3b0f703d0f713f0f72400f74420f75440f764510774710784910784a10794c117a4e117b4f127b51127c52137c54137d56147d57157e59157e5a167e5c167f5d177f5f187f601880621980641a80651a80671b80681c816a1c816b1d816d1d816e1e81701f81721f817320817521817621817822817922827b23827c23827e24828025828125818326818426818627818827818928818b29818c29818e2a81902a81912b81932b80942c80962c80982d80992d809b2e7f9c2e7f9e2f7fa02f7fa1307ea3307ea5317ea6317da8327daa337dab337cad347cae347bb0357bb2357bb3367ab5367ab73779b83779ba3878bc3978bd3977bf3a77c03a76c23b75c43c75c53c74c73d73c83e73ca3e72cc3f71cd4071cf4070d0416fd2426fd3436ed5446dd6456cd8456cd9466bdb476adc4869de4968df4a68e04c67e24d66e34e65e44f64e55064e75263e85362e95462ea5661eb5760ec5860ed5a5fee5b5eef5d5ef05f5ef1605df2625df2645cf3655cf4675cf4695cf56b5cf66c5cf66e5cf7705cf7725cf8745cf8765cf9785df9795df97b5dfa7d5efa7f5efa815ffb835ffb8560fb8761fc8961fc8a62fc8c63fc8e64fc9065fd9266fd9467fd9668fd9869fd9a6afd9b6bfe9d6cfe9f6dfea16efea36ffea571fea772fea973feaa74feac76feae77feb078feb27afeb47bfeb67cfeb77efeb97ffebb81febd82febf84fec185fec287fec488fec68afec88cfeca8dfecc8ffecd90fecf92fed194fed395fed597fed799fed89afdda9cfddc9efddea0fde0a1fde2a3fde3a5fde5a7fde7a9fde9aafdebacfcecaefceeb0fcf0b2fcf2b4fcf4b6fcf6b8fcf7b9fcf9bbfcfbbdfcfdbf",
}
LUT = {k: np.frombuffer(bytes.fromhex(v), dtype=np.uint8).reshape(256, 3) for k, v in _LUT_TESTO.items()}
LUT["grigio"] = np.repeat(np.arange(256, dtype=np.uint8)[:, None], 3, axis=1)


def colora(valori, vmin, vmax, lut):
    t = (np.log10(np.maximum(valori, vmin)) - math.log10(vmin)) / (math.log10(vmax) - math.log10(vmin))
    i = np.clip((t * 255).astype(np.int64), 0, 255)
    return LUT[lut][i]


# =============================================================================
# PROIEZIONI
# =============================================================================
class Proiezione:
    """Griglia dei pixel di una vista: per ogni pixel la direzione (Est, Nord, Su)."""

    def __init__(self, vista, larghezza, altezza, tipo="equidistante", centro_az=180.0):
        self.vista = vista
        self.w, self.h = int(larghezza), int(altezza)
        self.tipo = tipo
        self.centro_az = centro_az
        ys, xs = np.mgrid[0:self.h, 0:self.w].astype(np.float64)
        xs += 0.5
        ys += 0.5
        if vista == "cupola":
            self.c = (self.w / 2, self.h / 2)
            self.R = min(self.w, self.h) / 2 - max(16.0, min(self.w, self.h) * 0.035)
            dx = (xs - self.c[0]) / self.R
            dy = (self.c[1] - ys) / self.R
            r = np.hypot(dx, dy)
            self.dentro = r <= 1.0
            rr = np.minimum(r, 1.0)
            zd = np.arcsin(rr) if tipo == "ortografica" else rr * (np.pi / 2)
            az = np.arctan2(-dx, dy)
            alt = np.pi / 2 - zd
        else:
            self.alt_min, self.alt_max = -10.0, 90.0
            az = np.radians((self.centro_az - 180.0 + xs / self.w * 360.0) % 360.0)
            alt = np.radians(self.alt_max - ys / self.h * (self.alt_max - self.alt_min))
            self.dentro = np.ones_like(xs, dtype=bool)
        self.alt = np.degrees(alt)
        self.az = np.degrees(az) % 360
        self.enu = np.stack([np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)], axis=-1)
        self.sopra = (self.alt >= 0) & self.dentro

    def xy(self, alt, az):
        """Posizione sullo schermo (x, y, visibile) per alt/az in gradi (array)."""
        alt = np.asarray(alt, float)
        az = np.asarray(az, float)
        if self.vista == "cupola":
            zd = np.radians(90.0 - alt)
            r = np.sin(np.minimum(zd, np.pi / 2)) if self.tipo == "ortografica" else zd / (np.pi / 2)
            x = self.c[0] - r * np.sin(np.radians(az)) * self.R
            y = self.c[1] - r * np.cos(np.radians(az)) * self.R
            return x, y, alt >= 0
        x = ((az - (self.centro_az - 180.0)) % 360.0) / 360.0 * self.w
        y = (self.alt_max - alt) / (self.alt_max - self.alt_min) * self.h
        return x, y, alt >= self.alt_min

    def altaz_pixel(self, x, y):
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h and self.dentro[y, x]:
            return float(self.alt[y, x]), float(self.az[y, x])
        return None


# =============================================================================
# DISEGNO
# =============================================================================
_FONT_FILE = None


def font(dim, grassetto=False):
    global _FONT_FILE
    nomi = (["DejaVuSans-Bold.ttf"] if grassetto else []) + ["DejaVuSans.ttf"]
    for n in nomi:
        for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF",
                  "C:/Windows/Fonts"):
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, int(dim))
                except OSError:
                    pass
    try:
        return ImageFont.load_default(size=int(dim))
    except TypeError:
        return ImageFont.load_default()


class Impostazioni:
    def __init__(self, **kw):
        self.modo = "radio"                 # radio | occhio
        self.f_mhz = 38.0
        self.modello = "GSM2008"
        self.vista = "cupola"
        self.proiezione = "equidistante"
        self.colori = "inferno"
        self.costellazioni = True
        self.nomi_costellazioni = True
        self.stelle_in_radio = False
        self.sorgenti = True
        self.pianeti = True
        self.griglia = True
        self.piano_galattico = True
        self.scala_testo = 1.5
        self.luogo = "Padova"
        self.lat, self.lon, self.quota = LUOGHI["Padova"]
        for k, v in kw.items():
            setattr(self, k, v)


def num(x, cifre=0):
    """Numero all'italiana: spazio per le migliaia, virgola per i decimali."""
    if x is None or not math.isfinite(x):
        return "?"
    if cifre == "g":
        if abs(x) >= 1000:
            return num(x, 0)
        t = f"{x:.3g}"
        if "e" in t:
            m, e = t.split("e")
            return f"{m.replace('.', ',')}·10^{int(e)}"
        return t.replace(".", ",")
    t = f"{x:,.{cifre}f}"
    return t.replace(",", " ").replace(".", ",")


def testo_frequenza(f):
    if f >= 1000:
        g = f / 1000
        return (f"{g:.1f} GHz" if g < 10 else f"{g:.0f} GHz").replace(".", ",")
    return (f"{f:.1f} MHz" if f < 100 else f"{f:.0f} MHz").replace(".", ",")


def testo_frequenza_en(f):
    return testo_frequenza(f).replace(",", ".")


def lunghezza_onda(f):
    lam = C_LUCE / (f * 1e6)
    if lam >= 1:
        return f"{lam:.1f} m".replace(".", ",")
    if lam >= 0.01:
        return f"{lam * 100:.1f} cm".replace(".", ",")
    return f"{lam * 1000:.1f} mm".replace(".", ",")


_BV = np.array([-0.4, 0.0, 0.4, 0.8, 1.2, 1.6, 2.0])
_BV_RGB = np.array([[155, 176, 255], [202, 215, 255], [248, 247, 255], [255, 244, 232], [255, 222, 180],
                    [255, 196, 140], [255, 170, 110]], float)


def colore_stella(bv):
    return tuple(int(np.interp(bv, _BV, _BV_RGB[:, k])) for k in range(3))


class Pittore:
    """Prepara l'immagine del cielo (radio o occhio) con tutte le sovrapposizioni."""

    def __init__(self):
        self.modelli = {}
        self.ottico = CieloOttico()
        self.visibile = CieloVisibile()
        self.sole = SoleRadio()
        self._proiezioni = {}
        self.ultimi_valori = None           # temperature per pixel (per il puntatore)
        self.ultima_proiezione = None
        self.ultima_cornice = None
        self.ultimi_oggetti = []            # (nome, x, y, info)

    def modello(self, nome):
        if nome not in self.modelli and npy_pronti(nome):
            self.modelli[nome] = ModelloCielo(nome)
        return self.modelli.get(nome)

    def proiezione(self, vista, w, h, tipo):
        k = (vista, w, h, tipo)
        if k not in self._proiezioni:
            if len(self._proiezioni) > 3:
                self._proiezioni.pop(next(iter(self._proiezioni)))
            self._proiezioni[k] = Proiezione(vista, w, h, tipo)
        return self._proiezioni[k]

    # ------------------------------------------------------------------ immagine
    def disegna(self, imp, dt, w, h, didascalia=True, barra=True):
        """Restituisce un'immagine PIL RGB di w x h pixel."""
        alto_didasc = int(54 * imp.scala_testo * max(0.8, min(w, h) / 900)) if didascalia else 0
        hh = h - alto_didasc
        P = self.proiezione(imp.vista, w, hh, imp.proiezione)
        cor = Cornice(dt, imp.lat, imp.lon)
        corpi = posizioni_corpi(dt, imp.lat, imp.lon)
        sole_alt = float(cor.altaz_da_data(corpi["Sole"][0], corpi["Sole"][1])[0])
        rgb = np.zeros((hh, w, 3), np.uint8)
        valori = None
        mod = self.modello(imp.modello)
        if imp.modo == "radio" and mod is not None:
            f = min(max(imp.f_mhz, mod.fmin), mod.fmax)
            mappa = mod.mappa(f)
            if mod.coord == "G":
                v = P.enu @ cor.enu_gal.T
            else:
                v = P.enu @ cor.enu_j2000.T
            pix = vettori_a_pix(mod.nside, v)
            valori = np.asarray(mappa[pix], dtype=np.float64)
            valori = self._aggiungi_sole_luna(valori, P, cor, corpi, f)
            vmin, vmax = mod.scala_colori(f)
            rgb = colora(valori, vmin, vmax, imp.colori)
            self.scala = (vmin, vmax)
        elif imp.modo == "radio":
            rgb[:] = (12, 12, 20)
            self.scala = None
        else:
            self.scala = None
            if self.visibile.rgb is None:
                self.visibile.carica()
            if self.visibile.rgb is not None:
                lb = P.enu @ cor.enu_gal.T
                l, b = vett_radec(lb)
                rgb = self.visibile.colori(l, b).copy()
            else:
                rgb[:] = (6, 8, 22)
            # luce del giorno e del crepuscolo
            luce = float(np.clip((sole_alt + 12.0) / 18.0, 0, 1))
            if luce > 0:
                cielo = np.array([110, 160, 225], float)
                fatt = 0.15 + 0.85 * np.clip(P.alt / 90, 0, 1)[..., None]
                giorno = cielo * (1.0 - 0.35 * fatt)
                rgb = (rgb * (1 - luce) + giorno * luce).clip(0, 255).astype(np.uint8)
        # terreno
        sotto = ~P.sopra
        if imp.vista == "cupola":
            rgb[~P.dentro] = (0, 0, 0)
            rgb[P.dentro & (P.alt < 0)] = (20, 22, 16)
        else:
            terra = rgb[sotto].astype(float) * 0.25 + np.array([24, 26, 18]) * 0.75
            rgb[sotto] = terra.astype(np.uint8)
        img = Image.fromarray(rgb, "RGB")
        self.ultimi_valori = valori
        self.ultima_proiezione = P
        self.ultima_cornice = cor
        self.ultimi_oggetti = []
        self._sovrapposizioni(img, imp, P, cor, corpi, sole_alt)
        if didascalia:
            tela = Image.new("RGB", (w, h), (0, 0, 0))
            tela.paste(img, (0, 0))
            self._didascalia(tela, imp, dt, hh, alto_didasc, barra and imp.modo == "radio" and mod is not None)
            return tela
        return img

    def _aggiungi_sole_luna(self, valori, P, cor, corpi, f):
        """Sole e Luna dipinti con la risoluzione del modello (~1°): T_b media nel fascio."""
        lam = C_LUCE / (f * 1e6)
        fwhm = math.radians(1.0)
        om_fascio = 1.1331 * fwhm ** 2
        sig = fwhm / 2.3548
        for nome in ("Sole", "Luna"):
            ra, dec, dist, diam = corpi[nome]
            alt, az = cor.altaz_da_data(ra, dec)
            if alt < -3:
                continue
            u = altaz_enu(float(alt), float(az))
            cosd = np.clip(P.enu @ u, -1, 1)
            vicino = cosd > math.cos(math.radians(4))
            if not np.any(vicino):
                continue
            th = np.arccos(cosd[vicino])
            g = np.exp(-0.5 * (th / sig) ** 2)
            om = math.pi * (math.radians(diam) / 2) ** 2
            if nome == "Sole":
                S = self.sole.flusso_sfu(f) * SFU
                dT = S * lam ** 2 / (2 * K_B * om_fascio)
                valori[vicino] = valori[vicino] + dT * g
            else:
                riemp = om / om_fascio
                # la Luna (220 K) copre il cielo dietro: a bassa frequenza è un'ombra, in alto una macchia tiepida
                valori[vicino] = valori[vicino] + riemp * (T_RADIO["Luna"] - valori[vicino]) * g
        return valori

    # ------------------------------------------------------------------ disegni
    def _sovrapposizioni(self, img, imp, P, cor, corpi, sole_alt):
        d = ImageDraw.Draw(img, "RGBA")
        s = imp.scala_testo * max(0.75, min(P.w, P.h) / 900)
        f_pic = font(13 * s)
        f_med = font(15 * s)
        f_gr = font(19 * s, True)
        radio = imp.modo == "radio"

        def linea(punti_altaz, colore, larg=1, tratteggio=False):
            alt, az = punti_altaz
            x, y, ok = P.xy(alt, az)
            ok = ok & (alt >= -0.2)
            for i in range(len(x) - 1):
                if not (ok[i] and ok[i + 1]):
                    continue
                if abs(x[i + 1] - x[i]) > P.w / 3:
                    continue
                if tratteggio and i % 2:
                    continue
                d.line([(x[i], y[i]), (x[i + 1], y[i + 1])], fill=colore, width=larg)

        # griglia di altezza e orizzonte
        if imp.griglia:
            az = np.linspace(0, 360, 241)
            for a in (30, 60):
                linea((np.full_like(az, a), az), (255, 255, 255, 60), max(1, int(s)), tratteggio=True)
            if imp.vista == "cupola":
                for a0 in range(0, 360, 45):
                    linea((np.linspace(0, 89, 30), np.full(30, a0)), (255, 255, 255, 35), 1, tratteggio=True)
        az = np.linspace(0, 360, 361)
        linea((np.zeros_like(az), az), (230, 230, 200, 200), max(2, int(2 * s)))
        # punti cardinali
        for nome, a in (("N", 0), ("NE", 45), ("E", 90), ("SE", 135), ("S", 180), ("SO", 225), ("O", 270), ("NO", 315)):
            if imp.vista == "cupola":
                rr = P.R + (11 if len(nome) == 1 else 9) * s
                if len(nome) == 2:
                    rr = P.R - 12 * s
                x = P.c[0] - rr * math.sin(math.radians(a))
                y = P.c[1] - rr * math.cos(math.radians(a))
            else:
                x, y, _ = P.xy(-3, a)
            ff = f_gr if len(nome) == 1 else f_pic
            self._scritta(d, float(x), float(y), nome, ff, (255, 235, 150, 255), centro=True)
        # piano galattico
        if imp.piano_galattico:
            l = np.linspace(0, 360, 361)
            alt, az = cor.altaz_da_galattiche(l, np.zeros_like(l))
            linea((alt, az), (140, 200, 255, 120), max(1, int(s)), tratteggio=True)
        # costellazioni
        if imp.costellazioni and self.ottico.linee:
            col = (170, 200, 255, 95) if radio else (150, 185, 255, 120)
            for seg in self.ottico.linee:
                alt, az = cor.altaz_da_j2000(seg[:, 0], seg[:, 1])
                linea((alt, az), col, max(1, int(s)))
        if imp.nomi_costellazioni and self.ottico.costellazioni:
            for nome, ra, dec, rank in self.ottico.costellazioni:
                if rank > 2 and min(P.w, P.h) < 700:
                    continue
                alt, az = cor.altaz_da_j2000(ra, dec)
                if alt < 3:
                    continue
                x, y, _ = P.xy(alt, az)
                self._scritta(d, float(x), float(y), nome, f_pic, (190, 210, 255, 170), centro=True)
        # stelle
        if (not radio or imp.stelle_in_radio) and len(self.ottico.stelle):
            st = self.ottico.stelle
            alt, az = cor.altaz_da_j2000(st[:, 0], st[:, 1])
            x, y, ok = P.xy(alt, az)
            ok = ok & (alt > 0)
            luce = float(np.clip((sole_alt + 12.0) / 18.0, 0, 1)) if not radio else 0.0
            mag_lim = 5.5 - 5.0 * luce
            for i in np.nonzero(ok & (st[:, 2] <= mag_lim))[0]:
                m = st[i, 2]
                r = max(0.6, (5.8 - m) * 0.65) * s
                c = colore_stella(st[i, 3]) + (255 if not radio else 160,)
                d.ellipse([x[i] - r, y[i] - r, x[i] + r, y[i] + r], fill=c)
                if i in self.ottico.nomi_stelle and not radio and m < 1.0:
                    self._scritta(d, float(x[i]) + 4 * s, float(y[i]) - 14 * s, self.ottico.nomi_stelle[i], f_pic,
                                  (230, 230, 230, 200))
        # sorgenti radio
        if imp.sorgenti:
            for nome, breve, ra, dec, S1, alfa, tipo, testo in SORGENTI:
                alt, az = cor.altaz_da_j2000(ra, dec)
                if alt < 0:
                    continue
                x, y, _ = P.xy(alt, az)
                x, y = float(x), float(y)
                r = 7 * s
                d.ellipse([x - r, y - r, x + r, y + r], outline=(80, 255, 200, 230), width=max(2, int(2 * s)))
                self._scritta(d, x + r + 3, y - r - 2, breve, f_med, (120, 255, 210, 255))
                self.ultimi_oggetti.append(("sorgente", nome, x, y))
        # Sole, Luna, pianeti
        if imp.pianeti:
            for nome, (ra, dec, dist, diam) in corpi.items():
                alt, az = cor.altaz_da_data(ra, dec)
                if alt < 0:
                    continue
                x, y, _ = P.xy(alt, az)
                x, y = float(x), float(y)
                if nome == "Sole":
                    r = 10 * s
                    d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 220, 60, 255), width=max(2, int(3 * s)))
                    d.ellipse([x - 2 * s, y - 2 * s, x + 2 * s, y + 2 * s], fill=(255, 220, 60, 255))
                elif nome == "Luna":
                    r = 9 * s
                    d.ellipse([x - r, y - r, x + r, y + r], outline=(230, 230, 230, 255), width=max(2, int(2 * s)))
                else:
                    r = 5 * s
                    d.rectangle([x - r, y - r, x + r, y + r], outline=(255, 170, 90, 255), width=max(2, int(2 * s)))
                self._scritta(d, x + r + 3, y + 2, nome, f_med, (255, 210, 140, 255))
                self.ultimi_oggetti.append(("corpo", nome, x, y))

    def _scritta(self, d, x, y, testo, ff, colore, centro=False):
        if centro:
            bb = d.textbbox((0, 0), testo, font=ff)
            x -= (bb[2] - bb[0]) / 2
            y -= (bb[3] - bb[1]) / 2
        d.text((x + 1, y + 1), testo, font=ff, fill=(0, 0, 0, min(220, colore[3] if len(colore) > 3 else 255)))
        d.text((x, y), testo, font=ff, fill=colore)

    def _didascalia(self, tela, imp, dt, y0, alto, barra):
        d = ImageDraw.Draw(tela)
        w = tela.width
        s = alto / 54
        locale = dt.astimezone()
        if imp.modo == "radio":
            mod = MODELLI[imp.modello]["breve"]
            t = (f"Radio sky over {imp.luogo}  ·  {testo_frequenza_en(imp.f_mhz)} "
                 f"(wavelength {lunghezza_onda(imp.f_mhz).replace(',', '.')})  ·  model {mod}")
        else:
            t = f"Sky over {imp.luogo}  ·  visible light (Milky Way: A. Mellinger)"
        t += f"  ·  {locale:%Y-%m-%d %H:%M} {locale.tzname() or ''} ({dt.astimezone(timezone.utc):%H:%M} UTC)"
        # riga 1: il testo si rimpicciolisce finché sta nella larghezza
        dim = 14 * s
        ff = font(dim)
        while dim > 7 and d.textbbox((0, 0), t, font=ff)[2] > w - 16 * s:
            dim *= 0.93
            ff = font(dim)
        d.text((8 * s, y0 + 5 * s), t, font=ff, fill=(235, 235, 235))
        # riga 2: scala dei colori a sinistra, firma a destra
        fr = font(min(14 * s, dim * 1.0))
        bbf = d.textbbox((0, 0), FIRMA, font=fr)
        xf = w - (bbf[2] - bbf[0]) - 10 * s
        d.text((xf, y0 + 29 * s), FIRMA, font=fr, fill=(200, 190, 140))
        if barra and getattr(self, "scala", None):
            vmin, vmax = self.scala
            t1, t2 = f"{vmin:.3g} K", f"{vmax:.3g} K"
            for etich in ("brightness temperature (log scale):", "T (log):", ""):
                dimb = 12 * s
                fp = font(dimb)
                larg_testi = sum(d.textbbox((0, 0), x, font=fp)[2] for x in (etich, t1, t2)) + 30 * s
                spazio = xf - 20 * s - 8 * s - larg_testi
                if spazio > 60 * s or etich == "":
                    break
            bw = int(max(40, min(240 * s, spazio)))
            x = 8 * s
            yb = y0 + 30 * s
            bh = int(12 * s)
            if etich:
                d.text((x, yb - 2 * s), etich, font=fp, fill=(220, 220, 220))
                x += d.textbbox((0, 0), etich, font=fp)[2] + 8 * s
            d.text((x, yb - 2 * s), t1, font=fp, fill=(240, 240, 240))
            x += d.textbbox((0, 0), t1, font=fp)[2] + 6 * s
            lut = LUT[imp.colori]
            grad = np.repeat(lut[np.linspace(0, 255, bw).astype(int)][None, :, :], bh, axis=0)
            tela.paste(Image.fromarray(grad, "RGB"), (int(x), int(yb)))
            d.rectangle([int(x) - 1, int(yb) - 1, int(x) + bw, int(yb) + bh], outline=(200, 200, 200))
            x += bw + 6 * s
            d.text((x, yb - 2 * s), t2, font=fp, fill=(240, 240, 240))

    # ------------------------------------------------------------------ informazioni
    def info_punto(self, imp, x, y):
        """Testo per il puntatore sul pixel (x, y) dell'ultima immagine."""
        P, cor = self.ultima_proiezione, self.ultima_cornice
        if P is None:
            return None
        r = P.altaz_pixel(x, y)
        if r is None:
            return None
        alt, az = r
        v = altaz_enu(alt, az)
        ra, dec = vett_radec(v @ cor.enu_j2000.T)
        l, b = vett_radec(v @ cor.enu_gal.T)
        t = (f"Altezza {num(alt, 1)}°  azimut {num(az, 1)}°\nAR {num(float(ra) / 15, 2)} h  dec {num(float(dec), 1)}°  ·  "
             f"l {num(float(l), 1)}°  b {num(float(b), 1)}°")
        if self.ultimi_valori is not None and 0 <= int(y) < self.ultimi_valori.shape[0] and 0 <= int(x) < self.ultimi_valori.shape[1]:
            T = float(self.ultimi_valori[int(y), int(x)])
            t += f"\nTemperatura di brillanza: {num(T, 'g')} K"
        return t

    def oggetto_vicino(self, x, y, raggio):
        best, dmin = None, raggio
        for tipo, nome, ox, oy in self.ultimi_oggetti:
            dd = math.hypot(ox - x, oy - y)
            if dd < dmin:
                best, dmin = (tipo, nome), dd
        return best

    def scheda(self, imp, dt, tipo, nome):
        """Testo della scheda di una sorgente o di un corpo del Sistema solare; (testo, (ra, dec) J2000 o None)."""
        f = imp.f_mhz
        cor = Cornice(dt, imp.lat, imp.lon)
        if tipo == "sorgente":
            for n, breve, ra, dec, S1, alfa, tipo_s, testo in SORGENTI:
                if n == nome:
                    alt, az = cor.altaz_da_j2000(ra, dec)
                    l, b = vett_radec(radec_vett(ra, dec) @ GAL.T)
                    t = f"{n} ({breve}) – {tipo_s}\n\n{testo}\n\n"
                    t += (f"Posizione: AR {num(ra / 15, 3)} h, dec {num(dec, 2)}° (l {num(float(l), 1)}°, "
                          f"b {num(float(b), 1)}°)\n")
                    t += f"Adesso da {imp.luogo}: altezza {num(float(alt), 1)}°, azimut {num(float(az), 0)}°\n"
                    if S1:
                        S = S1 * (f / 1000.0) ** alfa
                        t += (f"Flusso indicativo a {testo_frequenza(f)}: circa {num(S, 'g')} Jy "
                              f"(legge di potenza con indice {num(alfa, 2)}: solo un ordine di grandezza)\n")
                    return t, (ra, dec)
        corpi = posizioni_corpi(dt, imp.lat, imp.lon)
        ra, dec, dist, diam = corpi[nome]
        alt, az = cor.altaz_da_data(ra, dec)
        t = f"{nome}\n\nAdesso da {imp.luogo}: altezza {num(float(alt), 1)}°, azimut {num(float(az), 0)}°\n"
        t += f"Distanza: {num(dist, 0)} km, diametro apparente {num(diam * 60, 1)}′\n"
        if nome == "Sole":
            S = self.sole.flusso_sfu(f)
            fonte = (f"stazioni RSTN di NOAA, {self.sole.quando[:10]}" if self.sole.punti else "valori tipici del Sole calmo")
            t += (f"\nFlusso radio a {testo_frequenza(f)}: circa {num(S, 'g')} unità di flusso solare (sfu), cioè "
                  f"{num(S * 1e4, 'g')} Jy ({fonte}; fuori da 245 MHz – 15,4 GHz è un prolungamento indicativo).\n")
            t += ("In radio il Sole è di gran lunga la sorgente più brillante del cielo, e il cielo resta buio anche di "
                  "giorno: per un radiotelescopio il giorno non esiste. La corona a un milione di gradi domina alle "
                  "frequenze basse; i brillamenti possono moltiplicare il flusso per migliaia di volte.")
        elif nome == "Luna":
            S = flusso_corpo_nero(T_RADIO["Luna"], diam, f)
            t += f"\nLa Luna emette come un corpo a circa 220 K: a {testo_frequenza(f)} circa {num(S, 'g')} Jy.\n"
            t += ("Alle frequenze basse il cielo dietro di lei è più caldo di 220 K, così la Luna appare come "
                  "un'ombra scura sulla Via Lattea; nelle microonde diventa una macchia tiepida.")
        else:
            S = flusso_corpo_nero(T_RADIO.get(nome, 150), diam, f)
            t += (f"\nEmissione termica indicativa (circa {T_RADIO.get(nome, 150)} K): {num(S, 'g')} Jy "
                  f"a {testo_frequenza(f)}.\n")
            if nome == "Giove":
                t += ("Giove è speciale: sotto 40 MHz produce tempeste radio fortissime, guidate dalla luna Io, "
                      "e le sue fasce di radiazione emettono per sincrotrone attorno a 1 GHz.")
        return t, None

    # ------------------------------------------------------------------ video
    def video(self, imp, dt_iniziale, tipo, file_mp4, w=1080, h=1080, fotogrammi=240, fps=24,
              ore=12.0, avanzamento=None, ferma=None):
        """tipo: 'notte' (ore a partire da dt_iniziale) o 'frequenze' (10 MHz -> fmax)."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise OSError("ffmpeg non trovato: installarlo con «sudo apt install ffmpeg»")
        tmp = Path(tempfile.mkdtemp(prefix="cielo_radio_"))
        try:
            vecchia_f = imp.f_mhz
            mod = self.modello(imp.modello)
            fmin = mod.fmin if mod else 10.0
            fmax = mod.fmax if mod else 94000.0
            for k in range(fotogrammi):
                if ferma is not None and ferma.is_set():
                    raise InterruptedError("interrotto")
                if tipo == "notte":
                    dt = dt_iniziale + timedelta(hours=ore * k / max(1, fotogrammi - 1))
                else:
                    dt = dt_iniziale
                    imp.f_mhz = fmin * (fmax / fmin) ** (k / max(1, fotogrammi - 1))
                img = self.disegna(imp, dt, w, h)
                img.save(tmp / f"f{k:05d}.png")
                if avanzamento:
                    avanzamento(k + 1, fotogrammi)
            imp.f_mhz = vecchia_f
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(tmp / "f%05d.png"),
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(file_mp4)]
            amb = dict(os.environ)
            if "LD_LIBRARY_PATH_ORIG" in amb:
                amb["LD_LIBRARY_PATH"] = amb["LD_LIBRARY_PATH_ORIG"]
            elif "LD_LIBRARY_PATH" in amb and getattr(__import__("sys"), "frozen", False):
                amb.pop("LD_LIBRARY_PATH")
            r = subprocess.run(cmd, capture_output=True, text=True, env=amb, timeout=1800)
            if r.returncode != 0:
                raise OSError("ffmpeg: " + (r.stderr or "errore")[-400:])
            return Path(file_mp4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# =============================================================================
# LWA TV
# =============================================================================
def immagine_lwatv(canale):
    nome, url = LWATV[canale]
    b = scarica_bytes(url + f"?nocache={int(time.time())}", timeout=15, totale=40, massimo=5_000_000)
    from io import BytesIO  # noqa: PLC0415
    return Image.open(BytesIO(b)).convert("RGB")


def ore_fa_lwa(dt, lon_luogo, lon_lwa=-107.6284):
    """Quante ore fa il luogo ha avuto allo zenit (stesso tempo siderale) il cielo che ora è sopra LWA.
    Per Padova circa 8 ore: il cielo passa prima sopra l'Italia e poi sopra il New Mexico."""
    return ((lst_gradi(dt, lon_luogo) - lst_gradi(dt, lon_lwa)) % 360) / 15.0 / 1.0027379


# =============================================================================
# RITAGLI SKYVIEW PER LE SCHEDE
# =============================================================================
RITAGLI = [("NVSS", "NVSS 1,4 GHz"), ("TGSS ADR1", "TGSS 150 MHz"), ("VLSSr", "VLSSr 74 MHz"),
           ("HI4PI", "HI4PI riga a 21 cm")]


def ritaglio_png(survey, ra, dec, lato_gradi=2.0, pixel=300):
    _, a = skyview_fits(survey, {"Position": f"{ra:.4f},{dec:.4f}", "Size": f"{lato_gradi}",
                                 "Pixels": str(pixel)}, timeout=60, totale=180)
    v = a[np.isfinite(a)]
    if v.size == 0:
        raise ValueError("nessun dato")
    lo, hi = np.percentile(v, [1, 99.7])
    t = np.clip((np.nan_to_num(a, nan=lo) - lo) / (hi - lo + 1e-12), 0, 1)
    t = np.arcsinh(t * 10) / np.arcsinh(10)
    return Image.fromarray(LUT["inferno"][(t * 255).astype(int)], "RGB")
