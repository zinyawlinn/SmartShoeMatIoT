#!/usr/bin/env python3
import RPi.GPIO as GPIO
import PCF8591 as ADC
import time
import subprocess
import uuid
from datetime import datetime, timezone
from aws_conn import publish_row, CLIENT_ID

# --- Pins (BOARD numbering) ---
BtnPin = 11
R = 12
G = 13
B = 15
ACTIVE_BUZZER_PIN = 16
PIR_PIN = 18

# --- ADC Channels ---
CH_PRESSURE  = 0
CH_RAIN      = 1
CH_MOISTURE  = 2

# --- Thresholds ---
PRESSURE_IDLE      = 15
PRESSURE_ACTIVE    = 30
RAIN_THRESHOLD     = 250
MOISTURE_THRESHOLD = 144

# --- Timing ---
BLINK_INTERVAL          = 0.3
PRECHECK_VOICE_INTERVAL = 3.0
CHECK_DECIDE_SEC        = 0.6
COOLDOWN                = 5.0  # motion voice cooldown

# --- Voice phrases (balanced, 3–5s) ---
SYS_START_PHRASE   = "Welcome to Smart Shoe Mat System. Checking sensors now."
MOTION_PHRASE      = "Motion detected. Please place your shoes on the mat."
PRECHECK_OK_PHRASE = "Pre-check complete. Sensors are ready."
PRECHECK_FAIL_RAIN = "Rain sensor is dirty. Please clean it."
PRECHECK_FAIL_MOIST= "Moisture sensor is dirty. Please clean it."
PRECHECK_FAIL_BOTH = "Rain and moisture sensors are dirty. Please clean both."

SENSING_SHOE       = "Checking your shoes. Please wait."
WELCOME_HOME       = "Welcome home! Your shoes are clean."
SHOE_WET           = "Your shoes are wet. Please clean them."
SHOE_MUDDY         = "Your shoes are muddy. Please clean them."
SHOES_DIRTY        = "Your shoes are dirty. Please clean them."

# --- Voice tuning (slow, smooth, human-ish) ---
VOICE      = "en+f3"   # try: en+f2, en+m2, en+m3
RATE_WPM   = "140"     # slower than 140
PITCH      = "50"      # a bit lower (0–99)
WORD_GAP   = "1"      # ms pause between words
VOLUME     = "100"     # 0–200 (espeak-ng amplitude)

def say(text: str, rate: str = RATE_WPM):
    """Speak with smoother pacing using espeak-ng."""
    try:
        subprocess.run(
            ["espeak-ng",
             "-v", VOICE,
             "-s", rate,     # words per minute
             "-p", PITCH,    # pitch
             "-a", VOLUME,   # amplitude
             "-g", WORD_GAP, # extra pause between words (ms)
             text],
            check=False
        )
        print("[VOICE]:", text)
    except Exception as e:
        print(f"TTS error: {e}")

def say_seq(*lines, gap=0.35):
    """Speak multiple short lines with a small pause for natural cadence."""
    for line in lines:
        say(line)
        time.sleep(gap)

# --- Globals ---
mode = "idle"
_last_blink = 0.0
_red_on = False
_pir_prev = 0
_last_spoken = 0.0  # PIR cooldown

_precheck_session_id = None
_check_id = None

_pre_fail_published = False
_pre_ok_published   = False
_last_precheck_voice = 0.0

_check_active = False
_check_started_at = 0.0
_spoke_sensing = False
_outcome_published = False
_outcome_spoken = False
_pressure_max = 0
_rain_hit_any = 0
_moist_hit_any = 0
_last_rain_raw = 0
_last_moist_raw = 0

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def set_led(color):
    GPIO.output(R, GPIO.LOW)
    GPIO.output(G, GPIO.LOW)
    GPIO.output(B, GPIO.LOW)
    if color == "red": GPIO.output(R, GPIO.HIGH)
    elif color == "green": GPIO.output(G, GPIO.HIGH)
    elif color == "blue": GPIO.output(B, GPIO.HIGH)

def set_active_buzzer(on):
    GPIO.output(ACTIVE_BUZZER_PIN, GPIO.LOW if on else GPIO.HIGH)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def build_row(stage, status,
              rain_hit=0, moist_hit=0,
              rain_raw=0, moisture_raw=0,
              pressure_max=0):
    return {
        "id": str(uuid.uuid4()),
        "ts_utc": utc_now_iso(),
        "device_id": CLIENT_ID,
        "stage": stage,
        "status": status,
        "precheck_session_id": _precheck_session_id,
        "check_id": _check_id or str(uuid.uuid4()),
        "rain_hit": int(rain_hit),
        "moist_hit": int(moist_hit),
        "rain_raw": int(rain_raw),
        "moisture_raw": int(moisture_raw),
        "pressure_max": int(pressure_max),
        "rain_thr": RAIN_THRESHOLD,
        "moist_thr": MOISTURE_THRESHOLD
    }

# -----------------------------------------------------------------------------
# Setup / Teardown
# -----------------------------------------------------------------------------
def setup():
    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(False)
    GPIO.setup(BtnPin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    for pin in (R, G, B):
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
    GPIO.setup(ACTIVE_BUZZER_PIN, GPIO.OUT)
    GPIO.output(ACTIVE_BUZZER_PIN, GPIO.HIGH)
    GPIO.setup(PIR_PIN, GPIO.IN)
    ADC.setup(0x48)
    GPIO.add_event_detect(BtnPin, GPIO.FALLING, callback=button_pressed, bouncetime=300)

def destroy():
    try:
        set_active_buzzer(False)
        ADC.write(0)
    finally:
        GPIO.cleanup()

# -----------------------------------------------------------------------------
# Button handler
# -----------------------------------------------------------------------------
def button_pressed(channel):
    global mode, _precheck_session_id, _check_id
    global _pre_fail_published, _pre_ok_published, _last_precheck_voice, _red_on

    _precheck_session_id = str(uuid.uuid4())
    _check_id = None
    _pre_fail_published = False
    _pre_ok_published = False
    _last_precheck_voice = 0.0
    _red_on = False
    set_active_buzzer(False)
    set_led("off")

    print(f"🆕 New session: {_precheck_session_id}")
    # smoother 2-part intro
    say_seq("Welcome to Smart Shoe Mat.", "Checking sensors now.", gap=0.35)
    mode = "precheck"

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def loop():
    global mode, _last_blink, _red_on
    global _pre_fail_published, _pre_ok_published, _last_precheck_voice
    global _check_active, _check_started_at, _spoke_sensing
    global _outcome_published, _outcome_spoken
    global _pressure_max, _rain_hit_any, _moist_hit_any
    global _last_rain_raw, _last_moist_raw, _check_id
    global _pir_prev, _last_spoken

    set_led("off")
    set_active_buzzer(False)

    while True:
        pressure_raw = ADC.read(CH_PRESSURE)
        now = time.time()

        # -------------------- PRECHECK --------------------
        if mode == "precheck":
            rain_raw  = ADC.read(CH_RAIN)
            moist_raw = ADC.read(CH_MOISTURE)
            rain_hit  = (rain_raw  < RAIN_THRESHOLD)
            moist_hit = (moist_raw < MOISTURE_THRESHOLD)
            any_hit   = (rain_hit or moist_hit)

            if any_hit:
                # continuous blink (LED + buzzer together)
                if now - _last_blink >= BLINK_INTERVAL:
                    _red_on = not _red_on
                    GPIO.output(R, GPIO.HIGH if _red_on else GPIO.LOW)
                    GPIO.output(G, GPIO.LOW)
                    GPIO.output(B, GPIO.LOW)
                    set_active_buzzer(_red_on)
                    _last_blink = now

                # repeat fail voice
                if now - _last_precheck_voice >= PRECHECK_VOICE_INTERVAL:
                    if rain_hit and moist_hit:
                        say(PRECHECK_FAIL_BOTH)
                    elif rain_hit:
                        say(PRECHECK_FAIL_RAIN)
                    else:
                        say(PRECHECK_FAIL_MOIST)
                    _last_precheck_voice = now

                # send fail once
                if not _pre_fail_published:
                    row = build_row("precheck", "fail",
                                    rain_hit=int(rain_hit), moist_hit=int(moist_hit),
                                    rain_raw=rain_raw, moisture_raw=moist_raw)
                    publish_row(row)
                    print("📤 Sent precheck FAIL row:", row)
                    _pre_fail_published = True
            else:
                # steady red when OK
                set_led("red")
                set_active_buzzer(False)
                if not _pre_ok_published:
                    say(PRECHECK_OK_PHRASE)
                    row = build_row("precheck", "ok",
                                    rain_hit=0, moist_hit=0,
                                    rain_raw=rain_raw, moisture_raw=moist_raw)
                    publish_row(row)
                    print("📤 Sent precheck OK row:", row)
                    _pre_ok_published = True
                    mode = "pressure"
                    _red_on = False
                    time.sleep(0.3)
            time.sleep(0.05)
            continue

        # -------------------- MOTION SENSOR --------------------
        if mode == "pressure" and not _check_active:
            pir_state = GPIO.input(PIR_PIN)
            if pir_state == 1 and _pir_prev == 0 and (now - _last_spoken) > COOLDOWN:
                say(MOTION_PHRASE)
                _last_spoken = now
                print("👀 Motion detected (PIR)")
            _pir_prev = pir_state

        # -------------------- SHOE CHECK --------------------
        if mode == "pressure":
            if pressure_raw > PRESSURE_ACTIVE and not _check_active:
                _check_active = True
                _check_started_at = time.time()
                _spoke_sensing = False
                _outcome_published = False
                _outcome_spoken = False
                _check_id = str(uuid.uuid4())
                _pressure_max = 0
                _rain_hit_any = 0
                _moist_hit_any = 0
                _last_rain_raw = 0
                _last_moist_raw = 0

            if _check_active:
                if not _spoke_sensing:
                    # smoother sensing cue
                    say_seq("Checking your shoes.", "Please wait.", gap=0.25)
                    _spoke_sensing = True
                    set_led("blue")
                    set_active_buzzer(False)

                _pressure_max = max(_pressure_max, pressure_raw)
                rain_raw  = ADC.read(CH_RAIN)
                moist_raw = ADC.read(CH_MOISTURE)
                _last_rain_raw  = rain_raw
                _last_moist_raw = moist_raw
                if rain_raw  < RAIN_THRESHOLD:     _rain_hit_any  = 1
                if moist_raw < MOISTURE_THRESHOLD: _moist_hit_any = 1

                status = "ok" if (_rain_hit_any == 0 and _moist_hit_any == 0) else "fail"

                # blink continuously during fail before release
                if status == "fail":
                    if now - _last_blink >= BLINK_INTERVAL:
                        _red_on = not _red_on
                        GPIO.output(R, GPIO.HIGH if _red_on else GPIO.LOW)
                        GPIO.output(G, GPIO.LOW)
                        GPIO.output(B, GPIO.LOW)
                        set_active_buzzer(_red_on)
                        _last_blink = now
                else:
                    set_led("green")
                    set_active_buzzer(False)

                # speak + publish once (but blinking continues if fail)
                if (not _outcome_published) and (time.time() - _check_started_at >= CHECK_DECIDE_SEC):
                    if not _outcome_spoken:
                        if _rain_hit_any and _moist_hit_any:
                            say(SHOES_DIRTY)
                        elif _rain_hit_any:
                            say(SHOE_WET)
                        elif _moist_hit_any:
                            say(SHOE_MUDDY)
                        else:
                            say(WELCOME_HOME)
                        _outcome_spoken = True

                    row = build_row("check", status,
                                    rain_hit=_rain_hit_any,
                                    moist_hit=_moist_hit_any,
                                    rain_raw=_last_rain_raw,
                                    moisture_raw=_last_moist_raw,
                                    pressure_max=_pressure_max)
                    publish_row(row)
                    print("📤 Sent check row (pre-release):", row)
                    _outcome_published = True

                # release → reset
                if pressure_raw <= PRESSURE_IDLE:
                    set_led("red")
                    set_active_buzzer(False)
                    _check_active = False
                    _check_id = None
                    _spoke_sensing = False
                    _outcome_spoken = False
                    _outcome_published = False
                    _pressure_max = 0
                    _rain_hit_any = 0
                    _moist_hit_any = 0
                    _last_rain_raw = 0
                    _last_moist_raw = 0
                    time.sleep(0.2)
            time.sleep(0.05)
            continue

        time.sleep(0.05)

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        setup()
        loop()
    except KeyboardInterrupt:
        destroy()

