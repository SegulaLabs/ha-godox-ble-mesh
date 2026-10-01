"""Minimal Bluetooth Mesh crypto (Mesh Profile 1.0 section 3.8).

This is the exact crypto used by the standalone `godox_mesh.py` Mac/Linux
controller in the same project, validated there against 496 real packets
captured from the Godox iPhone app's own logs. Copied here unchanged rather
than imported, since Home Assistant custom components ship self-contained.
"""
from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.exceptions import InvalidTag


def cmac(key: bytes, msg: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def aes_ecb(key: bytes, block: bytes) -> bytes:
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(block) + e.finalize()


def s1(m: bytes) -> bytes:
    return cmac(bytes(16), m)


def k1(n: bytes, salt: bytes, p: bytes) -> bytes:
    return cmac(cmac(salt, n), p)


def k2(n: bytes, p: bytes = b"\x00"):
    t = cmac(s1(b"smk2"), n)
    t1 = cmac(t, p + b"\x01")
    t2 = cmac(t, t1 + p + b"\x02")
    t3 = cmac(t, t2 + p + b"\x03")
    return t1[15] & 0x7F, t2, t3  # NID, EncryptionKey, PrivacyKey


def k3(n: bytes) -> bytes:
    return cmac(cmac(s1(b"smk3"), n), b"id64\x01")[-8:]


def k4(n: bytes) -> int:
    return cmac(cmac(s1(b"smk4"), n), b"id6\x01")[-1] & 0x3F


class NetKey:
    def __init__(self, key_hex: str):
        self.key = bytes.fromhex(key_hex)
        self.nid, self.enc, self.priv = k2(self.key)
        self.network_id = k3(self.key)
        self.beacon_key = k1(self.key, s1(b"nkbk"), b"id128\x01")


class AppKey:
    def __init__(self, key_hex: str):
        self.key = bytes.fromhex(key_hex)
        self.aid = k4(self.key)


def _pecb(nk: NetKey, iv: int, privacy_random: bytes) -> bytes:
    return aes_ecb(nk.priv, bytes(5) + iv.to_bytes(4, "big") + privacy_random[:7])


def encrypt_network_pdu(nk: NetKey, iv: int, ctl: int, ttl: int, seq: int, src: int,
                        dst: int, transport_pdu: bytes, proxy: bool = False) -> bytes:
    ctl_ttl = (ctl << 7) | (ttl & 0x7F)
    nonce_type = 0x03 if proxy else 0x00
    nonce = bytes([nonce_type, 0 if proxy else ctl_ttl]) + seq.to_bytes(3, "big") + \
        src.to_bytes(2, "big") + bytes(2) + iv.to_bytes(4, "big")
    mic_len = 8 if ctl else 4
    enc = AESCCM(nk.enc, tag_length=mic_len).encrypt(nonce, dst.to_bytes(2, "big") + transport_pdu, None)
    header = bytes([ctl_ttl]) + seq.to_bytes(3, "big") + src.to_bytes(2, "big")
    pecb = _pecb(nk, iv, enc)
    obf = bytes(a ^ b for a, b in zip(header, pecb))
    return bytes([((iv & 1) << 7) | nk.nid]) + obf + enc


def decrypt_network_pdu(nk: NetKey, iv_candidates, pdu: bytes, proxy: bool = False):
    """Return dict or None. Tries each IV index whose LSB matches IVI."""
    if len(pdu) < 14 or (pdu[0] & 0x7F) != nk.nid:
        return None
    ivi = pdu[0] >> 7
    for iv in iv_candidates:
        if (iv & 1) != ivi:
            continue
        pecb = _pecb(nk, iv, pdu[7:])
        hdr = bytes(a ^ b for a, b in zip(pdu[1:7], pecb))
        ctl, ttl = hdr[0] >> 7, hdr[0] & 0x7F
        seq = int.from_bytes(hdr[1:4], "big")
        src = int.from_bytes(hdr[4:6], "big")
        mic_len = 8 if ctl else 4
        nonce = bytes([0x03 if proxy else 0x00, 0 if proxy else hdr[0]]) + hdr[1:6] + bytes(2) + iv.to_bytes(4, "big")
        try:
            plain = AESCCM(nk.enc, tag_length=mic_len).decrypt(nonce, pdu[7:], None)
        except InvalidTag:
            continue
        return dict(iv=iv, ctl=ctl, ttl=ttl, seq=seq, src=src,
                    dst=int.from_bytes(plain[:2], "big"), transport=plain[2:])
    return None


def app_nonce(device: bool, seq: int, src: int, dst: int, iv: int, szmic: int = 0) -> bytes:
    return bytes([0x02 if device else 0x01, szmic << 7]) + seq.to_bytes(3, "big") + \
        src.to_bytes(2, "big") + dst.to_bytes(2, "big") + iv.to_bytes(4, "big")


def encrypt_access(key: bytes, device: bool, seq: int, src: int, dst: int, iv: int, access: bytes) -> bytes:
    return AESCCM(key, tag_length=4).encrypt(app_nonce(device, seq, src, dst, iv), access, None)


def decrypt_access(key: bytes, device: bool, seq: int, src: int, dst: int, iv: int, upper: bytes, mic_len: int = 4):
    try:
        return AESCCM(key, tag_length=mic_len).decrypt(
            app_nonce(device, seq, src, dst, iv, 1 if mic_len == 8 else 0), upper, None)
    except InvalidTag:
        return None


def parse_secure_beacon(nk: NetKey, data: bytes):
    """data starts with beacon type byte 0x01. Returns (iv_index, flags) if authenticated."""
    if len(data) != 22 or data[0] != 0x01:
        return None
    flags, net_id, iv, auth = data[1], data[2:10], data[10:14], data[14:22]
    if net_id != nk.network_id:
        return None
    if cmac(nk.beacon_key, data[1:14])[:8] != auth:
        return None
    return int.from_bytes(iv, "big"), flags
