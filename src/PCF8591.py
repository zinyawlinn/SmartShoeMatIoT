# PCF8591.py
# Simple PCF8591 ADC/DAC helper for Raspberry Pi

import smbus
import time

_address = 0x48
_bus = None
_cmd = 0x40


def setup(address=0x48, bus_number=1):
    """
    Initialize PCF8591 ADC module.
    Default I2C address is usually 0x48.
    Raspberry Pi normally uses I2C bus 1.
    """
    global _address, _bus
    _address = address
    _bus = smbus.SMBus(bus_number)


def read(channel):
    """
    Read analog value from PCF8591 channel 0-3.
    Returns value from 0 to 255.
    """
    if _bus is None:
        raise RuntimeError("PCF8591 not initialized. Call setup() first.")

    if channel < 0 or channel > 3:
        raise ValueError("PCF8591 channel must be 0, 1, 2, or 3.")

    # First read can be old/stale value, so read twice and return second value.
    _bus.read_byte_data(_address, _cmd + channel)
    time.sleep(0.001)
    value = _bus.read_byte_data(_address, _cmd + channel)
    return value


def write(value):
    """
    Write analog output value to PCF8591 DAC.
    Value must be 0-255.
    """
    if _bus is None:
        raise RuntimeError("PCF8591 not initialized. Call setup() first.")

    value = max(0, min(255, int(value)))
    _bus.write_byte_data(_address, _cmd, value)