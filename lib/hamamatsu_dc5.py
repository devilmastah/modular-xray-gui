"""
Hamamatsu C9730DK-11 (DC5) / C9732DK (DC12) USB driver.
Reimplemented from the public protocol (no code copied from faxitron).
Uses libusb1 (usb1). Exposure 30 ms–10 s (hardware may clamp above ~2 s), 14-bit pixels, bulk stream on endpoint 0x82.
"""

import struct
import time
from typing import Callable, Optional, Tuple

import numpy as np

# USB identifiers
HAM_VID = 0x0661
DC5_PID = 0xA802   # C9730DK-11
DC12_PID = 0xA800  # C9732DK
DC12T_PID = 0x4500 # C9732DT

PIX_MAX = 0x3FFF  # 14-bit max

# Stream sync words (16-bit LE); values >= 0x4000 are control
MSG_ABORTED = 0x8001
MSG_BEGIN = 0x8002
MSG_END = 0x8004
MSG_ERROR = 0x8005
MSG_END_SZ = 6

STATUS_OK_DC5 = 0x03
STATUS_OK_DC12 = 0x0E

EP_CMD_OUT = 0x01
EP_CMD_IN = 0x83
EP_BULK = 0x82
CMD_REPLY_SZ = 0x0200
BULK_CHUNK = 0x4000
BULK_READ_TIMEOUT_MS = 2500


def _cmd1(dev, opcode: int, payload: bytes = b"", read: bool = True) -> bytes:
    """Send command (opcode + len + payload) to EP_CMD_OUT, optionally read reply from EP_CMD_IN."""
    buf = struct.pack(">II", opcode, len(payload)) + payload
    dev.bulkWrite(EP_CMD_OUT, buf, timeout=1000)
    if read:
        return dev.bulkRead(EP_CMD_IN, CMD_REPLY_SZ, timeout=1000)
    return b""


def _validate_cmd1(dev, opcode: int, expected: bytes, payload: bytes = b"", msg: str = "") -> None:
    got = _cmd1(dev, opcode, payload=payload)
    if got[:len(expected)] != expected:
        raise RuntimeError(f"{msg}: expected {expected!r}, got {got[:len(expected)]!r}")


def _unpack16_le(b: bytes) -> int:
    return struct.unpack("<H", b[:2])[0]


def _is_sync(b: bytes) -> int:
    """Return sync word if first 2 bytes are a control word (>= 0x4000), else 0."""
    if len(b) < 2:
        return 0
    w = _unpack16_le(b)
    return w if w >= 0x4000 else 0


def open_device(usbcontext=None):
    """Open first Hamamatsu DC5/DC12 device. Claims interface 0. Caller must hold context."""
    import usb1
    if usbcontext is None:
        usbcontext = usb1.USBContext()
    for udev in usbcontext.getDeviceList(skip_on_error=True):
        vid, pid = udev.getVendorID(), udev.getProductID()
        if (vid, pid) in ((HAM_VID, DC5_PID), (HAM_VID, DC12_PID), (HAM_VID, DC12T_PID)):
            dev = udev.open()
            dev.claimInterface(0)
            dev.resetDevice()
            return dev, usbcontext
    raise RuntimeError("No Hamamatsu DC5/DC12 device found (VID 0x0661, PIDs 0xA802/0xA800/0x4500)")


def get_info1(dev) -> Tuple[str, str, str, str]:
    """Return (vendor, model, version, serial)."""
    buf = _cmd1(dev, 0x01)
    if len(buf) < 0x80:
        raise RuntimeError("get_info1 short reply")
    buf = buf[:0x80].decode("ascii", errors="replace")
    vendor = buf[0x00:0x20].replace("\x00", "").strip()
    model = buf[0x20:0x40].replace("\x00", "").strip()
    ver = buf[0x40:0x60].replace("\x00", "").strip()
    sn = buf[0x60:0x80].replace("\x00", "").strip()
    return vendor, model, ver, sn


def get_info2(dev) -> Tuple[int, int]:
    """Return (width, height) from sensor info."""
    buf = _cmd1(dev, 0x02)
    if len(buf) < 12:
        raise RuntimeError("get_info2 short reply")
    width = struct.unpack(">H", buf[6:8])[0]
    height = struct.unpack(">H", buf[10:12])[0]
    return width, height


def set_roi_wh(dev, width: int, height: int) -> None:
    payload = b"\x00\x01\x00\x00\x00\x00" + struct.pack(">HH", width, height)
    _validate_cmd1(dev, 0x09, b"\x01", payload=payload, msg="set_roi_wh")


def get_roi_wh(dev) -> Tuple[int, int]:
    buf = _cmd1(dev, 0x04)
    return struct.unpack(">II", buf[:8])


def set_exp(dev, exp_ms: int) -> None:
    if not 30 <= exp_ms <= 10000:
        raise ValueError("Exposure must be 30–10000 ms")
    _validate_cmd1(dev, 0x20, b"\x01", payload=struct.pack(">I", exp_ms), msg="set_exp")


def get_exp(dev) -> int:
    buf = _cmd1(dev, 0x1F)
    return struct.unpack(">I", buf[:4])[0]


def force_trig(dev) -> None:
    _validate_cmd1(dev, 0x0E, b"\x01", payload=b"\x01", msg="force_trig")


def abort_stream(dev) -> None:
    _cmd1(dev, 0x0F, read=False)


def _ham_init(dev, exp_ms: int = 500) -> Tuple[int, int]:
    """Run init sequence; set exposure; return (width, height)."""
    _validate_cmd1(dev, 0x00, b"\x01", msg="init packet 0")
    get_info1(dev)
    width_ret, height_ret = get_info2(dev)
    _validate_cmd1(dev, 0x24, b"\x00\x00\x00\x06\x00\x00\x00\x20\x00\x00\x00\x03", msg="init 0x24")
    _validate_cmd1(dev, 0x2A, b"\x00", msg="init 0x2A")
    for op in (0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x4A, 0x4F):
        _validate_cmd1(dev, op, b"\x00", msg=f"init 0x{op:02X}")
    _validate_cmd1(dev, 0x23, b"\x01", msg="init 0x23")
    _validate_cmd1(dev, 0x29, b"\x00", msg="init 0x29")
    for _ in range(3):
        get_info1(dev)
    set_roi_wh(dev, width_ret, height_ret)
    w, h = get_roi_wh(dev)
    if (w, h) != (width_ret, height_ret):
        raise RuntimeError(f"ROI mismatch: got ({w},{h}), expected ({width_ret},{height_ret})")
    for payload in (b"\x00\x00\x00\x02", b"\x00\x00\x00\x12", b"\x00\x00\x00\x18"):
        _validate_cmd1(dev, 0x2E, b"\x00", payload=payload, msg="init 0x2E")
    _validate_cmd1(dev, 0x21, b"\x3F\x9E\xB8\x51\xEB\x85\x1E\xB8", payload=b"\x00\x00\x00\x00", msg="init 0x21-0")
    _validate_cmd1(dev, 0x21, b"\x40\x34\x00\x00\x00\x00\x00\x00", payload=b"\x00\x00\x00\x01", msg="init 0x21-1")
    _validate_cmd1(dev, 0x21, b"\x3F\x50\x62\x4D\xD2\xF1\xA9\xFC", payload=b"\x00\x00\x00\x02", msg="init 0x21-2")
    _validate_cmd1(dev, 0x21, b"\x00\x00\x00\x00\x00\x00\x00\x00", payload=b"\x00\x00\x00\x03", msg="init 0x21-3")
    set_exp(dev, min(exp_ms, 10000))
    get_exp(dev)
    _trig_int(dev)
    get_exp(dev)
    _validate_cmd1(dev, 0x2E, b"\x00", payload=b"\x00\x00\x00\x12", msg="init 0x2E-2")
    _validate_cmd1(dev, 0x2E, b"\x00", payload=b"\x00\x00\x00\x02", msg="init 0x2E-3")
    set_exp(dev, min(exp_ms, 10000))
    get_exp(dev)
    _trig_int(dev)
    return width_ret, height_ret


def _trig_int(dev) -> None:
    _validate_cmd1(dev, 0x2D, b"\x00", payload=struct.pack(">H", 1), msg="trig_int")


def _capture_one_frame_sync(dev, width: int, height: int, depth: int = 2,
                            timeout_ms: int = 2500,
                            should_abort: Optional[Callable[[], bool]] = None) -> Optional[Tuple[bytes, int]]:
    """
    Capture one frame: wait for MSG_BEGIN, read image+footer (imgsz+2), then MSG_END.
    Returns (raw_image_bytes, average) or None on error/abort.
    When should_abort is set, use a per-read timeout that allows abort checks but avoids LIBUSB_ERROR_TIMEOUT
    (camera may take integration_time to send first bytes; single bulk read can exceed 400 ms).
    """
    imgsz = width * height * depth
    imgx_sz = imgsz + 2
    t0 = time.time()
    # Long enough for exposure delay + one chunk; short enough to check should_abort periodically (5 s)
    read_timeout_ms = 5000 if should_abort else BULK_READ_TIMEOUT_MS

    # Wait for MSG_BEGIN (first 2 bytes of a read); remainder of same read is image data
    rawbuff = bytearray()
    while (time.time() - t0) * 1000 < timeout_ms:
        if should_abort and should_abort():
            return None
        chunk = dev.bulkRead(EP_BULK, 512, timeout=read_timeout_ms)
        if should_abort:
            time.sleep(0)  # yield GIL so main thread can process HV Off / UI
        if len(chunk) < 2:
            continue
        sync = _is_sync(chunk)
        if sync == MSG_ABORTED:
            return None
        if sync == MSG_BEGIN:
            rawbuff.extend(chunk[2:])  # first payload bytes may follow BEGIN
            break
    else:
        raise TimeoutError("Timeout waiting for MSG_BEGIN")

    # Accumulate until we have image + 2-byte footer; watch for sync at chunk start
    while len(rawbuff) < imgx_sz:
        if should_abort and should_abort():
            return None
        chunk = dev.bulkRead(EP_BULK, BULK_CHUNK, timeout=read_timeout_ms)
        if should_abort:
            time.sleep(0)  # yield GIL so main thread can process HV Off / UI
        if (time.time() - t0) * 1000 >= timeout_ms:
            raise TimeoutError("Timeout reading frame data")
        if len(chunk) < 2:
            rawbuff.extend(chunk)
            continue
        sync = _is_sync(chunk)
        if sync:
            rawbuff.clear()
            if sync == MSG_ABORTED:
                return None
            if sync == MSG_END:
                # Discard this frame, next read may be BEGIN
                continue
            # MSG_BEGIN or MSG_ERROR: restart with this chunk's payload
            rawbuff.extend(chunk[2:])
            continue
        rawbuff.extend(chunk)

    rawimg = bytes(rawbuff[:imgsz])
    footer = rawbuff[imgsz:imgx_sz]
    average = struct.unpack("<H", footer)[0]

    # Next read: MSG_END (6 bytes). Should arrive immediately after image; use short timeout
    # so we don't block 5 s here (would block HV Off / UI). Frame is already complete in rawimg.
    if should_abort and should_abort():
        return None
    end_timeout_ms = 1500  # END follows image immediately; avoid long block for UI
    end_chunk = dev.bulkRead(EP_BULK, 512, timeout=end_timeout_ms)
    if should_abort:
        time.sleep(0)  # yield GIL after frame complete so main thread can process HV Off
    sync = _is_sync(end_chunk)
    if sync != MSG_END:
        return None
    if len(end_chunk) < 6:
        return None
    status, _counter = struct.unpack("<HH", end_chunk[2:6])
    if status not in (STATUS_OK_DC5, STATUS_OK_DC12):
        return None
    return rawimg, average


def raw_to_float32(raw: bytes, width: int, height: int, depth: int = 2,
                   rotate_180: bool = True) -> np.ndarray:
    """Convert 16-bit LE raw buffer to (H,W) float32. Optionally rotate 180."""
    n = width * height * depth
    if len(raw) != n:
        raise ValueError(f"Raw length {len(raw)} != {n}")
    arr = np.frombuffer(raw, dtype="<u2")
    arr = arr.reshape((height, width)).astype(np.float32)
    if rotate_180:
        arr = np.rot90(arr, 2)
    return arr


class HamamatsuDC5:
    """
    Single device handle: open, init, set exposure, capture frames.
    Use open_device() to obtain (dev, context); then pass dev to constructor.
    """

    def __init__(self, dev, usbcontext, exp_ms: int = 500, init: bool = True):
        self._dev = dev
        self._ctx = usbcontext
        self._exp_ms = exp_ms
        self._width: Optional[int] = None
        self._height: Optional[int] = None
        self._depth = 2
        if init:
            self._width, self._height = _ham_init(dev, exp_ms=exp_ms)
        self._is_dc12 = (get_info1(dev)[1].startswith("C9732"))

    @property
    def width(self) -> int:
        if self._width is None:
            raise RuntimeError("Device not initialized")
        return self._width

    @property
    def height(self) -> int:
        if self._height is None:
            raise RuntimeError("Device not initialized")
        return self._height

    def set_exp(self, exp_ms: int) -> None:
        set_exp(self._dev, exp_ms)
        self._exp_ms = exp_ms

    def get_exp(self) -> int:
        return get_exp(self._dev)

    def capture_one(self, timeout_ms: int = 2500,
                    should_abort: Optional[Callable[[], bool]] = None) -> Optional[np.ndarray]:
        """Trigger, read one frame, return float32 (H,W) or None on error. If should_abort is set, it is checked during read (Stop responsive)."""
        set_roi_wh(self._dev, self.width, self.height)
        get_roi_wh(self._dev)
        force_trig(self._dev)
        res = _capture_one_frame_sync(
            self._dev, self.width, self.height, self._depth,
            timeout_ms=timeout_ms, should_abort=should_abort
        )
        if res is None:
            return None
        rawimg, _ = res
        return raw_to_float32(rawimg, self.width, self.height, self._depth)

    def abort_and_drain(self) -> None:
        """Call abort_stream and drain MSG_ABORTED from bulk endpoint."""
        abort_stream(self._dev)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            try:
                chunk = self._dev.bulkRead(EP_BULK, 512, timeout=500)
                if _is_sync(chunk) == MSG_ABORTED:
                    break
            except Exception:
                break
