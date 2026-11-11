"""Serial driver for the Winegard Carryout's firmware console.

The command sequence ("q", "target", "g <az> <el>") and the position parsing
come from carryout_rotor.py and carryout_scan.py by Gabe Emerson
(saveitforparts). Tested upstream on a 2003 Carryout running HandEra HAL
1.00.065; other models are untested.

Two coordinate systems are in play:
  * dish coordinates - what the firmware understands; azimuth 0 is wherever
    the base happens to be facing.
  * true coordinates - real-world azimuth/elevation.
true = dish + offset, where the offsets come from `carryout-fta calibrate`.
"""
import re
import time


class DishError(Exception):
    pass


def parse_position(reply):
    """Best-effort parse of the firmware's live position output.

    Port of the upstream logic: find the token "el"; azimuth is three tokens
    before it and elevation two tokens after, both in hundredths of a degree.
    Returns (dish_az, dish_el) or None.
    """
    tokens = reply.split(" ")
    for i, tok in enumerate(tokens):
        if tok == "el" and i >= 3 and i + 2 < len(tokens):
            try:
                az = int(re.sub(r"[^0-9-]", "", tokens[i - 3])) / 100
                el = int(re.sub(r"[^0-9-]", "", tokens[i + 2][:4])) / 100
                return az, el
            except ValueError:
                continue
    return None


class Carryout:
    def __init__(self, port="/dev/ttyUSB0", baud=57600, north_offset=0.0,
                 el_offset=0.0, el_min=22.0, el_max=73.0,
                 slew_deg_per_s=6.0, settle_s=1.0):
        self.north_offset = north_offset
        self.el_offset = el_offset
        self.el_min = el_min
        self.el_max = el_max
        self.slew = slew_deg_per_s
        self.settle = settle_s
        self.dish_az = None      # last commanded position, dish coordinates
        self.dish_el = None
        self.reported = None     # last position the firmware reported, if any
        self._open(port, baud)
        self._write("q")         # leave any submenu a previous session left open
        self._write("")          # clear the prompt

    # --- low-level I/O (overridden by the simulator) -------------------
    def _open(self, port, baud):
        try:
            import serial
        except ImportError as exc:
            raise DishError("pyserial is not installed (pip install pyserial)") from exc
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=1)

    def _write(self, text):
        self.ser.write((text + "\r").encode("ascii"))

    def _read(self, nbytes=200):
        return self.ser.read(nbytes).decode("ascii", errors="replace")

    def _sleep(self, seconds):
        time.sleep(seconds)

    def close(self):
        self.ser.close()

    # --- coordinates ----------------------------------------------------
    def to_dish(self, az, el):
        """True az/el -> dish az/el, checking the elevation limits."""
        dish_az = (az - self.north_offset) % 360
        dish_el = el - self.el_offset
        if not self.el_min <= dish_el <= self.el_max:
            raise DishError(
                f"elevation {dish_el:.1f} is outside the dish's range "
                f"{self.el_min:.0f}-{self.el_max:.0f}")
        return dish_az, dish_el

    def to_true(self, dish_az, dish_el):
        return (dish_az + self.north_offset) % 360, dish_el + self.el_offset

    # --- motion -----------------------------------------------------------
    def goto(self, az, el, wait=True):
        """Point at a true azimuth/elevation."""
        self.goto_raw(*self.to_dish(az, el), wait=wait)

    def goto_raw(self, dish_az, dish_el, wait=True):
        if self.dish_az is None:
            travel = 180.0       # unknown start: assume the worst case
        else:
            d_az = abs(dish_az - self.dish_az) % 360
            travel = max(min(d_az, 360 - d_az), abs(dish_el - self.dish_el))
        self._write("target")
        self._write(f"g {dish_az:.1f} {dish_el:.1f}")
        reply = self._read()
        self._write("q")
        self.reported = parse_position(reply) or self.reported
        self.dish_az, self.dish_el = dish_az, dish_el
        if wait:
            self._sleep(travel / self.slew + self.settle)

    def console(self, command, nbytes=4000):
        """Send one raw firmware command and return whatever comes back."""
        self._write(command)
        return self._read(nbytes)


class SimulatedDish(Carryout):
    """Stands in for the hardware so everything can run without a dish.

    hidden_north / hidden_el are the "real" mounting errors that calibration
    is supposed to discover.
    """

    def __init__(self, hidden_north=0.0, hidden_el=0.0, **kwargs):
        self.hidden_north = hidden_north
        self.hidden_el = hidden_el
        self.log = []
        super().__init__(**kwargs)

    def _open(self, port, baud):
        pass

    def _write(self, text):
        self.log.append(text)

    def _read(self, nbytes=200):
        return ""

    def _sleep(self, seconds):
        pass

    def close(self):
        pass

    def actual_pointing(self):
        """Where the simulated dish really points, in true coordinates."""
        return ((self.dish_az + self.hidden_north) % 360,
                self.dish_el + self.hidden_el)
